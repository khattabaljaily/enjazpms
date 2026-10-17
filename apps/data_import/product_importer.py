"""
Row-processing engine for the product import commit step.

Mirrors, rather than reinvents, four existing ORM/logic patterns:
- opening balance:  apps/stocks/views.py `opening_balance_save_api`
- batch creation:   apps/purchases/services.py `confirm_purchase_invoice`
- default stock:    apps/stocks/views.py (`filter(is_default=True).first() or .first()`)
- per-product units: apps/items/views.py `_save_item_units` (the same helper the
  manual "add product" form uses — a product's units are NOT a shared/tenant-level
  list, they're defined per-product as an optional smaller+larger pair)
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction, IntegrityError
from django.utils import timezone

from apps.items.models import Category, Item, ItemBatch
from apps.items.views import _apply_hc_prices, _save_item_units
from apps.items.dedup import find_existing_item_match
from apps.suppliers.models import Supplier
from apps.stocks.models import Stock, StockQuantity
from apps.stocks.scope import opening_balance_stocks
from apps.sales.models import StockMovement
from apps.ai.services import smart_map_headers, match_category_name
from apps.core.io_utils import parse_uploaded_file, smart_get, safe_decimal, safe_date, bool_from_str

from .schemas import get_product_schema
from .gating import products_import_blocked_reason

logger = logging.getLogger('data_import')

# وضع العملة الصعبة: حقول الاستيراد بالعملة الصعبة ونظيراتها المحلية، وعناوين الأعمدة المحلية المعروفة
# (التي يكتبها التصدير). عمود لا يدل عنوانه على «عملة صعبة» لا يجوز أن يُقرأ كسعر بالعملة الصعبة،
# وإلا ضُرب سعر محلي في سعر الصرف فخرجت أسعار ضخمة.
HC_TO_LOCAL = {'cost_price_hc': 'cost_price', 'selling_price_hc': 'selling_price',
               'min_selling_price_hc': 'min_selling_price'}
LOCAL_PRICE_HEADERS = {
    'cost_price': ('سعر التكلفة (محلي)', 'سعر التكلفة', 'التكلفة', 'cost_price'),
    'selling_price': ('سعر البيع (محلي)', 'سعر البيع', 'selling_price'),
    'min_selling_price': ('أدنى سعر بيع (محلي)', 'أدنى سعر بيع', 'min_selling_price'),
}
_HC_MARKERS = ('صعبة', 'hc', 'usd', 'دولار', '$')


def _apply_local_prices(item, tenant, local_prices):
    """السعر المحلي الوارد في الملف هو المرجع (يبقى كما كُتب) والصعبة تُشتق منه = محلي ÷ سعر الصرف."""
    rate = Decimal(str(tenant.exchange_rate or 0))
    for field, value in local_prices.items():
        if getattr(item, field) == value and getattr(item, field + '_hc'):
            continue   # لم يتغير السعر المحلي: لا نعيد اشتقاق الصعبة منه فلا ينحرف شيء
        setattr(item, field, value)
        setattr(item, field + '_hc', (value / rate).quantize(Decimal('0.0001')) if rate else Decimal('0'))


def _is_hc_header(header) -> bool:
    text = str(header or '').lower()
    return any(marker in text for marker in _HC_MARKERS)


def _name_lookup(queryset):
    return {obj.name.strip().lower(): obj for obj in queryset}


def import_products(tenant, uploaded_file, user):
    """Returns {'created': int, 'updated': int, 'errors': [...], 'possible_duplicates': [...]}"""
    empty_result = {'created': 0, 'updated': 0, 'possible_duplicates': []}
    if products_import_blocked_reason(tenant):
        return {**empty_result, 'errors': [{'row': 0, 'field': '', 'message': 'أضف تصنيفاً واحداً على الأقل أولاً'}]}

    rows, err = parse_uploaded_file(uploaded_file)
    if err:
        return {**empty_result, 'errors': [{'row': 0, 'field': '', 'message': err}]}
    if not rows:
        return {**empty_result, 'errors': [{'row': 0, 'field': '', 'message': 'الملف فارغ أو لا يحتوي على بيانات'}]}

    # مستخدم الفرع (حين يُسمح للفروع بإدارة المنتجات) يستورد إلى فرعه فقط: منتجاته ومخازنه وموردوه.
    branch = user.branch if getattr(user, 'branch_id', None) else None

    schema = get_product_schema(tenant)
    schema_by_field = {s['field']: s for s in schema}
    has_stock_column = 'stock' in schema_by_field

    ai_schema = [
        {'field': s['field'], 'description': s['description'], 'required': s.get('required', False)}
        for s in schema
    ]
    actual_headers = list(rows[0].keys())
    try:
        mapping = smart_map_headers(actual_headers, ai_schema)
    except Exception:
        mapping = {}
    if tenant.hard_currency_mode:
        for header, canonical in list(mapping.items()):
            if canonical in HC_TO_LOCAL and not _is_hc_header(header):
                mapping[header] = HC_TO_LOCAL[canonical]

    categories = _name_lookup(Category.objects.for_tenant(tenant).filter(is_active=True))
    stocks = _name_lookup(opening_balance_stocks(tenant, branch))
    suppliers = _name_lookup(Supplier.objects.for_tenant(tenant).for_branch(branch).filter(is_active=True))

    default_stock = None
    if not has_stock_column:
        active_stocks = opening_balance_stocks(tenant, branch).order_by('-is_default', 'name')
        default_stock = active_stocks.filter(is_default=True).first() or active_stocks.first()

    category_ai_cache: dict = {}

    def resolve_category(raw: str):
        if not raw:
            return None, None
        key = raw.strip().lower()
        if key in categories:
            return categories[key], None
        if raw in category_ai_cache:
            cached = category_ai_cache[raw]
            return (categories.get(cached.strip().lower()), None) if cached else (None, f'التصنيف "{raw}" غير موجود')
        matched = match_category_name(raw, [c.name for c in categories.values()])
        category_ai_cache[raw] = matched
        if matched and matched.strip().lower() in categories:
            return categories[matched.strip().lower()], None
        return None, f'التصنيف "{raw}" غير موجود — اختره من القائمة المنسدلة'

    def resolve_stock(raw: str):
        if not raw:
            return default_stock, None
        key = raw.strip().lower()
        if key in stocks:
            return stocks[key], None
        return None, f'المخزن "{raw}" غير موجود'

    def resolve_supplier(raw: str):
        """Unlike category/stock, an unknown supplier name is created on the
        fly — pricelists rarely have a pre-curated supplier list, and this
        field exists purely for the tenant's own later filtering."""
        raw = (raw or '').strip()
        if not raw:
            return None
        key = raw.lower()
        if key in suppliers:
            return suppliers[key]
        if branch is not None and tenant.is_central_purchasing():
            return None   # في النمط «مركزي» لا موردين لدى الفروع
        supplier = Supplier.objects.create(tenant=tenant, name=raw, created_by=user, updated_by=user, branch=branch)
        suppliers[key] = supplier
        return supplier

    created = 0
    updated = 0
    errors = []
    possible_duplicates = []

    for i, row in enumerate(rows, start=2):
        try:
            with transaction.atomic():
                name = smart_get(row, 'name', mapping, schema_by_field['name']['header_ar'], 'اسم المنتج', 'الاسم', 'name')
                if not name:
                    errors.append({'row': i, 'field': 'name', 'message': 'اسم المنتج مطلوب'})
                    continue

                category_raw = smart_get(row, 'category', mapping, schema_by_field.get('category', {}).get('header_ar', ''), 'التصنيف', 'category')
                category, cat_err = resolve_category(category_raw)
                if cat_err:
                    errors.append({'row': i, 'field': 'category', 'message': cat_err})
                    continue

                supplier_raw = smart_get(row, 'supplier', mapping, schema_by_field.get('supplier', {}).get('header_ar', ''), 'الشركة الموردة', 'supplier')
                supplier = resolve_supplier(supplier_raw)

                base_unit_name = smart_get(row, 'base_unit_name', mapping, schema_by_field.get('base_unit_name', {}).get('header_ar', ''), 'اسم الوحدة الأساسية', 'base_unit_name')
                large_unit_name = smart_get(row, 'large_unit_name', mapping, schema_by_field.get('large_unit_name', {}).get('header_ar', ''), 'اسم وحدة أكبر', 'large_unit_name')
                large_unit_count_raw = smart_get(row, 'large_unit_count', mapping, schema_by_field.get('large_unit_count', {}).get('header_ar', ''), 'large_unit_count')

                units_data = []
                if large_unit_name and not base_unit_name:
                    errors.append({'row': i, 'field': 'base_unit_name', 'message': 'حدّد اسم الوحدة الأساسية أولاً قبل تحديد وحدة أكبر'})
                    continue
                if base_unit_name:
                    units_data.append({'name': base_unit_name, 'factor': 1})
                if large_unit_name:
                    large_unit_count = safe_decimal(large_unit_count_raw, default=Decimal('0'))
                    if large_unit_count <= 0:
                        errors.append({'row': i, 'field': 'large_unit_count', 'message': 'حدّد كم وحدة أساسية داخل الوحدة الأكبر'})
                        continue
                    units_data.append({'name': large_unit_name, 'factor': float(large_unit_count)})

                target_stock = default_stock
                if has_stock_column:
                    stock_raw = smart_get(row, 'stock', mapping, schema_by_field['stock']['header_ar'], 'المخزن', 'stock')
                    target_stock, stock_err = resolve_stock(stock_raw)
                    if stock_err:
                        errors.append({'row': i, 'field': 'stock', 'message': stock_err})
                        continue

                kwargs = {
                    'tenant': tenant, 'created_by': user, 'updated_by': user, 'branch': branch,
                    'name': name, 'category': category, 'supplier': supplier,
                    'is_active': True,
                }

                for spec in schema:
                    field = spec['field']
                    if field in ('name', 'category', 'supplier', 'base_unit_name', 'large_unit_name', 'large_unit_count',
                                 'opening_quantity', 'stock', 'batch_number', 'expiry_date'):
                        continue
                    raw_value = smart_get(row, field, mapping, spec['header_ar'], field)
                    if spec['dtype'] == 'decimal':
                        kwargs[field] = safe_decimal(raw_value, default=Decimal('0'))
                    elif spec['dtype'] == 'bool':
                        kwargs[field] = bool_from_str(raw_value) if raw_value else False
                    elif spec['dtype'] == 'choice_fixed':
                        label_to_value = {label: value for value, label in spec['choices']}
                        kwargs[field] = label_to_value.get(raw_value, spec['choices'][0][0])
                    else:
                        kwargs[field] = raw_value

                # أسعار محلية وردت في الملف (مثل ملف صدّره النظام نفسه) بلا نظيرتها بالعملة الصعبة:
                # المحلي هو المرجع والصعبة تُشتق منه، لا العكس.
                local_prices = {}
                if tenant.hard_currency_mode:
                    rate = Decimal(str(tenant.exchange_rate or 0))
                    for local_field, headers in LOCAL_PRICE_HEADERS.items():
                        raw = smart_get(row, local_field, mapping, *headers)
                        if not raw:
                            continue
                        value = safe_decimal(raw, default=Decimal('0'))
                        hc_in_file = kwargs.get(local_field + '_hc')
                        # الملف الذي يحمل العمودين (تصدير النظام): المحلي هو المرجع ما دام متسقًا مع الصعبة
                        # (فارق تقريب الـ4 خانات فقط)، وإلا فالمستخدم عدّل الصعبة عمدًا فتُعتمد هي.
                        if not hc_in_file or (rate and abs(value / rate - hc_in_file) <= Decimal('0.0001')):
                            local_prices[local_field] = value

                dup_match = find_existing_item_match(
                    tenant, name=name,
                    generic_name=kwargs.get('generic_name', ''),
                    dosage_form=kwargs.get('dosage_form', ''),
                    strength=kwargs.get('strength', ''),
                    barcode=kwargs.get('barcode', ''),
                )

                if dup_match and dup_match['match_type'] in ('barcode', 'exact'):
                    # Re-importing/overlapping supplier lists should update the
                    # existing tenant-specific fields, not create a full duplicate.
                    existing = dup_match['item']
                    if branch is not None and existing.branch_id != branch.id:
                        errors.append({'row': i, 'field': 'name',
                                       'message': f'المنتج "{existing.name}" موجود في كتالوج الإدارة أو فرع آخر ولا يمكن لفرعك تعديله'})
                        continue
                    update_fields = []
                    for field in ('cost_price', 'selling_price', 'min_selling_price', 'tax_rate',
                                  'min_quantity', 'max_quantity'):
                        if field in kwargs:
                            setattr(existing, field, kwargs[field])
                            update_fields.append(field)
                    existing.updated_by = user
                    update_fields.append('updated_by')
                    if tenant.hard_currency_mode:
                        # أسعار العملة الصعبة هي المصدر في هذا الوضع: نأخذ القيم الواردة
                        # في الملف قبل اشتقاق الأسعار المحلية، وإلا طغت الأسعار القديمة.
                        # تُحدَّث الأسعار التي تغيّرت فعلًا فقط؛ غير المتغيرة تبقى حرفيًا، لأن الصعبة مخزّنة
                        # بـ4 خانات فإعادة ضربها في سعر الصرف تُنحرف عن المحلي (10,000.00 ← 9,999.75).
                        rate = Decimal(str(tenant.exchange_rate or 0))
                        for hc_field, local_field in HC_TO_LOCAL.items():
                            new_hc = kwargs.get(hc_field)
                            if new_hc and new_hc != getattr(existing, hc_field):
                                setattr(existing, hc_field, new_hc)
                                setattr(existing, local_field, (new_hc * rate).quantize(Decimal('0.01')))
                        _apply_local_prices(existing, tenant, local_prices)
                        update_fields += ['cost_price', 'selling_price', 'min_selling_price',
                                          'cost_price_hc', 'selling_price_hc', 'min_selling_price_hc']
                    existing.save(update_fields=list(set(update_fields)))
                    updated += 1
                    continue

                if dup_match and dup_match['match_type'] == 'fuzzy':
                    possible_duplicates.append({
                        'row': i, 'field': 'name',
                        'existing_item_id': dup_match['item'].id,
                        'existing_item_name': dup_match['item'].name,
                        'confidence': dup_match['confidence'],
                    })

                item = Item(**kwargs)
                if tenant.hard_currency_mode:
                    _apply_hc_prices(item, tenant)
                    _apply_local_prices(item, tenant, local_prices)
                item.save()

                if units_data:
                    _save_item_units(item, json.dumps(units_data), tenant)

                opening_qty = safe_decimal(
                    smart_get(row, 'opening_quantity', mapping, schema_by_field.get('opening_quantity', {}).get('header_ar', ''), 'الكمية الافتتاحية', 'opening_quantity'),
                    default=Decimal('0'),
                )

                if opening_qty and target_stock and item.item_type != 'service':
                    sq = StockQuantity.objects.select_for_update().get(
                        tenant=tenant, stock=target_stock, item=item,
                    )
                    sq.quantity = opening_qty
                    sq.opening_quantity = opening_qty
                    sq.save(update_fields=['quantity', 'opening_quantity'])

                    StockMovement.objects.create(
                        tenant=tenant, item=item, stock=target_stock,
                        movement_type='opening_in', direction='in',
                        quantity=opening_qty, unit_cost=item.cost_price or Decimal('0'),
                        movement_date=timezone.localdate(),
                        reference_type='opening_balance', reference_id=sq.id,
                        balance_after=sq.quantity, notes='رصيد افتتاحي عبر استيراد المنتجات',
                    )

                    batch_number = smart_get(row, 'batch_number', mapping, schema_by_field.get('batch_number', {}).get('header_ar', ''), 'رقم الدفعة', 'batch_number')
                    expiry_raw = smart_get(row, 'expiry_date', mapping, schema_by_field.get('expiry_date', {}).get('header_ar', ''), 'تاريخ الانتهاء', 'expiry_date')
                    expiry_date = safe_date(expiry_raw) if expiry_raw else None
                    if batch_number or expiry_date:
                        ItemBatch.objects.create(
                            tenant=tenant, item=item, stock=target_stock,
                            batch_number=batch_number or '', expiry_date=expiry_date,
                            quantity_received=opening_qty, quantity_remaining=opening_qty,
                            purchase_date=timezone.localdate(),
                        )

                created += 1
        except IntegrityError as exc:
            logger.error('product import row %d: %s', i, exc)
            errors.append({'row': i, 'field': 'sku', 'message': 'رمز المنتج (SKU) أو بيانات أخرى مكررة'})
        except (InvalidOperation, ValueError) as exc:
            logger.error('product import row %d: %s', i, exc)
            errors.append({'row': i, 'field': '', 'message': f'قيمة غير صالحة: {exc}'})
        except Exception as exc:
            logger.error('product import row %d: %s', i, exc, exc_info=True)
            errors.append({'row': i, 'field': '', 'message': str(exc)})

    return {'created': created, 'updated': updated, 'errors': errors, 'possible_duplicates': possible_duplicates}
