"""
Shared item-creation helpers used by more than one entry point (manual "add
product" form, bulk product import, catalog-based onboarding) so opening-
stock bookkeeping stays in one place. Mirrors the exact pattern already used
inline in apps/data_import/product_importer.py.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.sales.models import StockMovement
from apps.stocks.models import StockQuantity


def apply_opening_stock(tenant, item, stock, quantity: Decimal, batch_number: str = '', expiry_date=None):
    """
    Records an opening balance for a newly created item: sets the
    StockQuantity row (auto-created by the Item post_save signal), logs an
    'opening_in' StockMovement, and creates an ItemBatch if batch/expiry info
    was given. No-op if quantity/stock is falsy or the item is a service.
    """
    if not (quantity and stock) or item.item_type == 'service':
        return

    with transaction.atomic():
        sq = StockQuantity.objects.select_for_update().get(tenant=tenant, stock=stock, item=item)
        sq.quantity = quantity
        sq.opening_quantity = quantity
        sq.save(update_fields=['quantity', 'opening_quantity'])

        StockMovement.objects.create(
            tenant=tenant, item=item, stock=stock,
            movement_type='opening_in', direction='in',
            quantity=quantity, unit_cost=item.cost_price or Decimal('0'),
            movement_date=timezone.localdate(),
            reference_type='opening_balance', reference_id=sq.id,
            balance_after=sq.quantity, notes='رصيد افتتاحي',
        )

        if batch_number or expiry_date:
            from .models import ItemBatch
            ItemBatch.objects.create(
                tenant=tenant, item=item, stock=stock,
                batch_number=batch_number or '', expiry_date=expiry_date,
                quantity_received=quantity, quantity_remaining=quantity,
                purchase_date=timezone.localdate(),
            )


def reprice_items_for_rate(tenant, new_rate):
    """
    إعادة تسعير كل الأصناف التي لها سعر بالعملة الصعبة بعد تغيير سعر الصرف:
    السعر المحلي = السعر بالعملة الصعبة × سعر الصرف الجديد.
    يغطي الأصناف التي لها سعر بيع أو تكلفة أو حد أدنى بالعملة الصعبة (أي منهما),
    ويعيد عدد الأصناف التي أُعيد تسعيرها.
    """
    from .models import Item

    rate = Decimal(str(new_rate))
    items = Item.objects.for_tenant(tenant).filter(
        Q(selling_price_hc__isnull=False)
        | Q(cost_price_hc__isnull=False)
        | Q(min_selling_price_hc__isnull=False)
    )
    updated = 0
    bulk = []
    for item in items:
        if item.selling_price_hc:
            item.selling_price = (item.selling_price_hc * rate).quantize(Decimal('0.01'))
        if item.cost_price_hc:
            item.cost_price = (item.cost_price_hc * rate).quantize(Decimal('0.01'))
        if item.min_selling_price_hc:
            item.min_selling_price = (item.min_selling_price_hc * rate).quantize(Decimal('0.01'))
        bulk.append(item)
        updated += 1
    if bulk:
        Item.objects.bulk_update(bulk, ['selling_price', 'cost_price', 'min_selling_price'])
    return updated


# ============================================================
# زيادة/تخفيض عام على الأسعار بنسبة مئوية
# ============================================================

PRICE_FIELD_PAIRS = {
    'selling': ('selling_price', 'selling_price_hc'),
    'min': ('min_selling_price', 'min_selling_price_hc'),
    'cost': ('cost_price', 'cost_price_hc'),
}
MAX_LOCAL = Decimal('9999999999.99')      # max_digits=14, decimal_places=2
MAX_HC = Decimal('9999999999.9999')       # max_digits=14, decimal_places=4
MIN_PERCENT = Decimal('-99')
MAX_PERCENT = Decimal('1000')


class PriceAdjustmentError(ValueError):
    pass


def _category_ids_with_descendants(tenant, ids):
    """التصنيفات المختارة + كل فروعها (التصنيف هرمي)."""
    from .models import Category
    ids = {int(i) for i in ids}
    if not ids:
        return set()
    parent_of = dict(Category.objects.for_tenant(tenant).values_list('id', 'parent_id'))
    result = set(ids)
    changed = True
    while changed:
        changed = False
        for cid, pid in parent_of.items():
            if pid in result and cid not in result:
                result.add(cid)
                changed = True
    return result


def adjusted_item_prices(item, tenant, percent, fields):
    """
    الأسعار الجديدة لمنتج واحد → dict {اسم الحقل: القيمة الجديدة} للحقول التي تغيّرت فقط.

    وضع العملة الصعبة (مع سعر صرف): السعر المحلي هو المرجع كما في نموذج التعديل
    (محلي × (1+نسبة) ثم الصعبة = المحلي الجديد ÷ سعر الصرف)، وإن كان المحلي صفراً
    والصعبة موجودة تُزاد الصعبة والمحلي = الصعبة × سعر الصرف. بذلك يبقى
    المحلي = الصعبة × سعر الصرف، ولا يتغيّر الرقم المكتوب بسبب تقريب الـ4 خانات.
    خارج وضع العملة الصعبة لا تُمس حقول الصعبة.
    """
    factor = Decimal('1') + (percent / Decimal('100'))
    hc_on = bool(tenant.hard_currency_mode and tenant.exchange_rate)
    rate = Decimal(str(tenant.exchange_rate)) if hc_on else None
    cent, mill = Decimal('0.01'), Decimal('0.0001')
    changes = {}
    for key in fields:
        local_f, hc_f = PRICE_FIELD_PAIRS[key]
        local = getattr(item, local_f) or Decimal('0')
        hc = getattr(item, hc_f)
        if local:
            new_local = (local * factor).quantize(cent)
            new_hc = (new_local / rate).quantize(mill) if hc_on else None
        elif hc_on and hc:
            new_hc = (hc * factor).quantize(mill)
            new_local = (new_hc * rate).quantize(cent)
        else:
            continue
        if new_local < 0 or new_local > MAX_LOCAL or (new_hc is not None and (new_hc < 0 or new_hc > MAX_HC)):
            raise PriceAdjustmentError(f'نتيجة غير صالحة للمنتج «{item.name}»: السعر الجديد خارج الحدود المسموحة')
        if new_local != getattr(item, local_f):
            changes[local_f] = new_local
        if new_hc is not None and new_hc != hc:
            changes[hc_f] = new_hc
    return changes


def build_price_adjustment_queryset(tenant, *, only_category_ids=(), exclude_category_ids=(),
                                    exclude_supplier_ids=(), exclude_item_ids=(),
                                    exclude_services=True, exclude_inactive=False, branch=None):
    """
    يعيد (المنتجات التي سيُطبَّق عليها، عدد المستثناة، وصف النطاق).
    كل الاستثناءات AND: يكفي أن يطابق المنتج أي استثناء ليُستبعد.
    """
    from .models import Category, Item
    from apps.suppliers.models import Supplier

    base = Item.objects.for_tenant(tenant)
    if branch is not None:
        base = base.filter(branch=branch)
    scope_parts = []

    if only_category_ids:
        cats = _category_ids_with_descendants(tenant, only_category_ids)
        base = base.filter(category_id__in=cats)
        names = list(Category.objects.for_tenant(tenant).filter(id__in={int(i) for i in only_category_ids}).values_list('name', flat=True))
        scope_parts.append('النطاق: التصنيفات ' + '، '.join(names))
    else:
        scope_parts.append('النطاق: كل المنتجات')
    total_in_scope = base.count()

    excl = Q()
    if exclude_category_ids:
        cats = _category_ids_with_descendants(tenant, exclude_category_ids)
        excl |= Q(category_id__in=cats)
        names = list(Category.objects.for_tenant(tenant).filter(id__in={int(i) for i in exclude_category_ids}).values_list('name', flat=True))
        scope_parts.append('استثناء التصنيفات: ' + '، '.join(names))
    if exclude_supplier_ids:
        excl |= Q(supplier_id__in=[int(i) for i in exclude_supplier_ids])
        names = list(Supplier.objects.for_tenant(tenant).filter(id__in=[int(i) for i in exclude_supplier_ids]).values_list('name', flat=True))
        scope_parts.append('استثناء الموردين: ' + '، '.join(names))
    if exclude_item_ids:
        excl |= Q(id__in=[int(i) for i in exclude_item_ids])
        names = list(Item.objects.for_tenant(tenant).filter(id__in=[int(i) for i in exclude_item_ids]).values_list('name', flat=True)[:30])
        scope_parts.append('استثناء منتجات: ' + '، '.join(names) + (' …' if len(exclude_item_ids) > 30 else ''))
    if exclude_services:
        excl |= Q(item_type='service')
        scope_parts.append('استثناء الخدمات')
    if exclude_inactive:
        excl |= Q(is_active=False)
        scope_parts.append('استثناء المنتجات الموقوفة')

    qs = base.exclude(excl) if excl else base
    return qs, total_in_scope - qs.count(), '\n'.join(scope_parts)


def preview_price_adjustment(tenant, percent, fields, qs, sample=8):
    """(عدد المتأثرة، عينة قبل/بعد). يرفع PriceAdjustmentError عند نتيجة غير صالحة."""
    affected = 0
    rows = []
    for item in qs.order_by('name'):
        changes = adjusted_item_prices(item, tenant, percent, fields)
        if not changes:
            continue
        affected += 1
        if len(rows) < sample:
            rows.append({
                'name': item.name,
                'old_selling': str(item.selling_price), 'new_selling': str(changes.get('selling_price', item.selling_price)),
                'old_cost': str(item.cost_price), 'new_cost': str(changes.get('cost_price', item.cost_price)),
                'old_selling_hc': str(item.selling_price_hc) if item.selling_price_hc is not None else None,
                'new_selling_hc': str(changes.get('selling_price_hc', item.selling_price_hc)) if item.selling_price_hc is not None or 'selling_price_hc' in changes else None,
            })
    return affected, rows


@transaction.atomic
def apply_price_adjustment(tenant, user, percent, fields, note, qs, excluded_count, scope_summary):
    """
    يطبّق التعديل على كل منتجات qs داخل معاملة واحدة (إمّا الكل أو لا شيء)،
    ويسجّل PriceAdjustment + سطراً قبل/بعد لكل منتج متأثر.
    """
    from .models import Item, PriceAdjustment, PriceAdjustmentLine

    adj = PriceAdjustment.objects.create(
        tenant=tenant, created_by=user, percent=percent,
        affect_selling='selling' in fields, affect_min='min' in fields, affect_cost='cost' in fields,
        note=note, scope_summary=scope_summary, excluded_count=excluded_count,
        hard_currency_mode=bool(tenant.hard_currency_mode),
        hard_currency=tenant.hard_currency if tenant.hard_currency_mode else '',
        exchange_rate=tenant.exchange_rate if tenant.hard_currency_mode else None,
    )
    all_fields = [f for pair in PRICE_FIELD_PAIRS.values() for f in pair]
    touched, lines = [], []
    for item in qs.select_for_update().order_by('id'):
        changes = adjusted_item_prices(item, tenant, percent, fields)
        if not changes:
            continue
        old = {f: str(getattr(item, f)) if getattr(item, f) is not None else None for f in changes}
        for f, v in changes.items():
            setattr(item, f, v)
        item.updated_by = user
        item.updated_at = timezone.now()
        touched.append(item)
        lines.append(PriceAdjustmentLine(
            adjustment=adj, item=item, item_name=item.name, old_values=old,
            new_values={f: str(v) for f, v in changes.items()},
        ))
    if touched:
        Item.objects.bulk_update(touched, all_fields + ['updated_by', 'updated_at'], batch_size=500)
        PriceAdjustmentLine.objects.bulk_create(lines, batch_size=500)
    adj.items_count = len(touched)
    adj.save(update_fields=['items_count'])
    return adj
