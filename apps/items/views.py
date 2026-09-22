"""
Items Views - عمليات CRUD للمنتجات والتصنيفات والوحدات
كل العمليات عبر JSON API (AJAX) + صفحة واحدة لكل قسم
"""
from apps.accounts.activity_service import log_activity
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponseNotAllowed, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect, render

from decimal import Decimal
from apps.accounts.decorators import require_permission
from apps.core.utils import filter_by_branch_via
from .forms import CategoryForm, ItemForm, UnitForm
from .models import Category, Item, Unit, BOMRecipe, BOMLine, ItemBatch
from apps.catalog.models import MasterDrug, MasterDrugAlias
from apps.sales.models import StockMovement
from apps.stocks.models import StockQuantity


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _get_capabilities(tenant):
    try:
        return tenant.capabilities
    except Exception:
        return None


def _serialize_errors(form):
    return {f: [str(e) for e in errs] for f, errs in form.errors.items()}


def _apply_hc_prices(item, tenant):
    """إذا كان وضع العملة الصعبة مفعّلاً، يحسب الأسعار المحلية من سعر العملة الصعبة × سعر الصرف."""
    if not (tenant and tenant.hard_currency_mode and tenant.exchange_rate):
        return
    rate = Decimal(str(tenant.exchange_rate))
    if item.selling_price_hc:
        item.selling_price = (item.selling_price_hc * rate).quantize(Decimal('0.01'))
    if item.cost_price_hc:
        item.cost_price = (item.cost_price_hc * rate).quantize(Decimal('0.01'))
    if item.min_selling_price_hc:
        item.min_selling_price = (item.min_selling_price_hc * rate).quantize(Decimal('0.01'))


# ============================================================
# صفحة المنتجات الرئيسية
# ============================================================

@login_required
@require_permission('view_items')
def item_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Item.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    out_of_stock = qs.filter(is_active=True, min_quantity__gt=0).count()

    caps = _get_capabilities(tenant)
    from apps.core.utils import currency_symbol, CURRENCY_SYMBOLS
    from apps.suppliers.models import Supplier
    hc_cur = tenant.hard_currency if tenant.hard_currency_mode else ''
    local_cur = tenant.currency or 'SDG'
    context = {
        'item_form': ItemForm(tenant=tenant, capabilities=caps),
        'suppliers': Supplier.objects.for_tenant(tenant).filter(is_active=True).order_by('name'),
        'hc_mode': tenant.hard_currency_mode,
        'hc_currency': hc_cur,
        'hc_currency_symbol': currency_symbol(hc_cur),
        'local_currency': local_cur,
        'local_currency_symbol': currency_symbol(local_cur),
        'exchange_rate': tenant.exchange_rate if tenant.hard_currency_mode else None,
        'currency_symbols_json': CURRENCY_SYMBOLS,
        'stats': {
            'total': total,
            'active': active,
            'inactive': total - active,
            'out_of_stock': out_of_stock,
        },
    }
    return render(request, 'items/item_list.html', context)


# ============================================================
# صفحة التصنيفات
# ============================================================

@login_required
@require_permission('view_categories')
def category_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Category.objects.for_tenant(tenant)
    total = qs.count()
    root_count = qs.filter(parent=None).count()

    context = {
        'form': CategoryForm(tenant=tenant),
        'stats': {
            'total': total,
            'root_count': root_count,
            'active': qs.filter(is_active=True).count(),
        },
    }
    return render(request, 'items/category_list.html', context)


# ============================================================
# صفحة وحدات القياس
# ============================================================

@login_required
@require_permission('view_items')
def unit_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Unit.objects.for_tenant(tenant)
    context = {
        'form': UnitForm(tenant=tenant),
        'stats': {
            'total': qs.count(),
            'active': qs.filter(is_active=True).count(),
        },
    }
    return render(request, 'items/unit_list.html', context)


# ============================================================
# Items: جدول API
# ============================================================

@login_required
@require_permission('view_items')
def item_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '').strip()
    category_id = request.GET.get('category', '').strip()
    supplier_id = request.GET.get('supplier', '').strip()

    from .models import ItemUnit
    qs = Item.objects.for_tenant(tenant).select_related('category', 'supplier').prefetch_related('item_units')
    records_total = qs.count()

    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(is_active=False)

    if category_id:
        qs = qs.filter(category_id=category_id)

    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)

    if search_value:
        qs = qs.filter(
            Q(name__icontains=search_value) |
            Q(sku__icontains=search_value) |
            Q(barcode__icontains=search_value) |
            Q(name_en__icontains=search_value) |
            Q(generic_name__icontains=search_value)
        )

    records_filtered = qs.count()

    order_col = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    col_name = request.GET.get(f'columns[{order_col}][data]', 'name')
    allowed = {
        'sku': 'sku', 'name': 'name', 'barcode': 'barcode',
        'selling_price': 'selling_price', 'cost_price': 'cost_price',
        'is_active': 'is_active', 'created_at': 'created_at',
    }
    order_field = allowed.get(col_name, 'name')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    qs = qs.order_by(order_field)[start:start + length]

    def _unit_label(item):
        units = list(item.item_units.order_by('factor'))
        if not units:
            return item.base_unit_name or '-'
        if len(units) == 1:
            return units[0].name
        return '-'   # multi-unit: show — per user request

    hc_mode = tenant.hard_currency_mode
    hc_currency = tenant.hard_currency if hc_mode else ''
    data = [
        {
            'id': item.id,
            'sku': item.sku,
            'name': item.name,
            'item_type': item.item_type,
            'barcode': item.barcode or '-',
            'category': item.category.name if item.category else '-',
            'supplier': item.supplier.name if item.supplier else '-',
            'unit': _unit_label(item),
            'has_multiple_units': item.has_multiple_units,
            'cost_price': str(item.cost_price),
            'selling_price': str(item.selling_price),
            'selling_price_hc': str(item.selling_price_hc) if (hc_mode and item.selling_price_hc) else None,
            'hc_currency': hc_currency,
            'track_expiry': item.track_expiry,
            'track_serial': item.track_serial,
            'requires_prescription': item.requires_prescription,
            'is_controlled_substance': item.is_controlled_substance,
            'is_insurance_excluded': item.is_insurance_excluded,
            'is_active': item.is_active,
        }
        for item in qs
    ]

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': data,
    })


# ============================================================
# Onboarding from the shared master catalog — the fast path meant to replace
# entering every product by hand. See apps/catalog for the master data these
# views read from.
# ============================================================

@login_required
@require_permission('view_items')
def catalog_picker(request):
    from apps.stocks.models import Stock
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name')
    return render(request, 'items/catalog_picker.html', {'stocks': stocks})


@login_required
@require_permission('view_items')
def catalog_search_api(request):
    """
    Powers the catalog picker. With no `q`, browses the catalog alphabetically
    (so the tenant isn't forced to already know a drug name to see anything);
    with `q`, filters by trade name / manufacturer / generic name. `offset`/
    `limit` page through results — `has_more` tells the picker whether to
    show a "load more" button.
    """
    from django.db.models import Q
    from apps.catalog.matching import normalize_text
    from apps.catalog.models import MasterDrugAlias

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'results': [], 'has_more': False})

    q = normalize_text(request.GET.get('q', ''))
    dosage_form = request.GET.get('dosage_form', '').strip()
    agent = request.GET.get('agent', '').strip()
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except ValueError:
        offset = 0
    limit = min(50, max(1, int(request.GET.get('limit', 30) or 30)))

    aliases_qs = MasterDrugAlias.objects.filter(
        master_drug__status='active'
    ).select_related('master_drug', 'master_drug__category').order_by('trade_name_normalized')
    if q:
        aliases_qs = aliases_qs.filter(
            Q(trade_name_normalized__icontains=q)
            | Q(manufacturer__icontains=q)
            | Q(master_drug__generic_name_normalized__icontains=q)
        )
    if dosage_form:
        aliases_qs = aliases_qs.filter(master_drug__dosage_form=dosage_form)
    if agent:
        aliases_qs = aliases_qs.filter(sudan_agent=agent)

    page = list(aliases_qs[offset:offset + limit + 1])
    has_more = len(page) > limit
    page = page[:limit]

    def _row(alias, drug):
        return {
            'master_drug_id': drug.id,
            'alias_id': alias.id if alias else None,
            'display_name': f'{alias.trade_name} ({alias.manufacturer})' if alias and alias.manufacturer
                             else (alias.trade_name if alias else drug.generic_name),
            'trade_name': alias.trade_name if alias else '',
            'generic_name': drug.generic_name,
            'dosage_form': drug.dosage_form,
            'strength': drug.strength,
            'manufacturer': alias.manufacturer if alias else '',
            'country_of_origin': alias.country_of_origin if alias else '',
            'sudan_agent': alias.sudan_agent if alias else '',
            'pack_size': alias.pack_size if alias else '',
            'barcode': alias.barcode if alias else '',
            'category_name': drug.category.name if drug.category_id else '',
            'requires_prescription': drug.requires_prescription,
            'is_controlled_substance': drug.is_controlled_substance,
        }

    results = [_row(a, a.master_drug) for a in page]
    return JsonResponse({'results': results, 'has_more': has_more, 'next_offset': offset + limit})


@login_required
@require_permission('view_items')
def catalog_filters_api(request):
    """Distinct values to populate the catalog picker's filter dropdowns."""
    from apps.catalog.models import MasterDrug, MasterDrugAlias

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'dosage_forms': [], 'agents': []})

    dosage_forms = list(
        MasterDrug.objects.filter(status='active').exclude(dosage_form='')
        .order_by('dosage_form').values_list('dosage_form', flat=True).distinct()
    )
    agents = list(
        MasterDrugAlias.objects.filter(master_drug__status='active').exclude(sudan_agent='')
        .order_by('sudan_agent').values_list('sudan_agent', flat=True).distinct()
    )
    return JsonResponse({'dosage_forms': dosage_forms, 'agents': agents})


def _resolve_tenant_category(tenant, name: str, user):
    name = (name or '').strip()
    if not name:
        return None
    existing = Category.objects.for_tenant(tenant).filter(name__iexact=name).first()
    if existing:
        return existing
    return Category.objects.create(tenant=tenant, name=name, created_by=user, updated_by=user)


@login_required
@require_permission('add_items')
def item_create_from_catalog(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    master_drug_id = request.GET.get('master_drug_id') or request.POST.get('master_drug_id')
    alias_id = request.GET.get('alias_id') or request.POST.get('alias_id')
    drug = get_object_or_404(MasterDrug, pk=master_drug_id) if master_drug_id else None
    alias = MasterDrugAlias.objects.filter(pk=alias_id).first() if alias_id else None

    if request.method != 'POST':
        from apps.stocks.models import Stock
        stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)
        capabilities = _get_capabilities(tenant)
        return render(request, 'items/catalog_create.html', {
            'drug': drug, 'alias': alias, 'stocks': stocks,
            'tenant': tenant, 'capabilities': capabilities,
            'item_type_choices': Item.ITEM_TYPE_CHOICES,
        })

    if not drug:
        return JsonResponse({'success': False, 'message': 'الدواء غير موجود في الكتالوج'}, status=400)

    from apps.stocks.models import Stock
    from .dedup import find_existing_item_match
    from .services import apply_opening_stock

    name = request.POST.get('name') or (alias.trade_name if alias else drug.generic_name)
    barcode = request.POST.get('barcode', '') or (alias.barcode if alias else '')

    dup = find_existing_item_match(tenant, name=name, generic_name=drug.generic_name,
                                    dosage_form=drug.dosage_form, strength=drug.strength, barcode=barcode)
    if dup and dup['match_type'] in ('barcode', 'exact'):
        return JsonResponse({
            'success': False,
            'message': f"يوجد صنف مطابق بالفعل: {dup['item'].name}",
            'existing_item_id': dup['item'].id,
        }, status=400)

    category = _resolve_tenant_category(tenant, request.POST.get('category') or (drug.category.name if drug.category_id else ''), request.user)
    allowed_item_types = {v for v, _ in Item.ITEM_TYPE_CHOICES}
    item_type = request.POST.get('item_type') or drug.item_type
    if item_type not in allowed_item_types:
        item_type = 'product'

    # كل حقل موجود في Item إما متاح من الكتالوج (يُملأ هنا تلقائياً) أو
    # مُدخل من المشترك عبر النموذج — ما فيش حقل بيتجاهل بصمت.
    item = Item(
        tenant=tenant, created_by=request.user, updated_by=request.user,
        name=name, name_en=request.POST.get('name_en', '').strip(),
        sku=request.POST.get('sku', '').strip(),
        barcode=barcode, item_type=item_type, category=category,
        generic_name=drug.generic_name, manufacturer=alias.manufacturer if alias else '',
        country_of_origin=alias.country_of_origin if alias else '',
        sudan_agent=alias.sudan_agent if alias else '',
        pack_size=alias.pack_size if alias else '',
        dosage_form=drug.dosage_form, strength=drug.strength,
        requires_prescription='requires_prescription' in request.POST,
        is_controlled_substance='is_controlled_substance' in request.POST,
        is_insurance_excluded='is_insurance_excluded' in request.POST,
        description=request.POST.get('description', '').strip() or drug.description,
        cost_price=safe_decimal_or_zero(request.POST.get('cost_price')),
        selling_price=safe_decimal_or_zero(request.POST.get('selling_price')),
        min_selling_price=safe_decimal_or_zero(request.POST.get('min_selling_price')),
        tax_rate=safe_decimal_or_zero(request.POST.get('tax_rate')),
        min_quantity=safe_decimal_or_zero(request.POST.get('min_quantity')),
        max_quantity=safe_decimal_or_zero(request.POST.get('max_quantity')),
        track_expiry='track_expiry' in request.POST,
        track_batch='track_batch' in request.POST,
        track_serial='track_serial' in request.POST,
        is_sellable='is_sellable' in request.POST,
        is_purchasable='is_purchasable' in request.POST,
        master_drug=drug, is_active=True,
    )
    if request.FILES.get('image'):
        item.image = request.FILES['image']
    elif drug.image:
        from django.core.files.base import ContentFile
        drug.image.open('rb')
        item.image.save(drug.image.name.split('/')[-1], ContentFile(drug.image.read()), save=False)
        drug.image.close()

    if tenant.hard_currency_mode:
        item.cost_price_hc = safe_decimal_or_zero(request.POST.get('cost_price_hc'))
        item.selling_price_hc = safe_decimal_or_zero(request.POST.get('selling_price_hc'))
        item.min_selling_price_hc = safe_decimal_or_zero(request.POST.get('min_selling_price_hc'))
        _apply_hc_prices(item, tenant)
    item.save()

    base_unit = request.POST.get('base_unit_name', '').strip() or drug.default_unit_name
    large_unit = request.POST.get('large_unit_name', '').strip()
    large_unit_count = safe_decimal_or_zero(request.POST.get('large_unit_count'))
    if base_unit:
        import json as _json
        units_data = [{'name': base_unit, 'factor': 1}]
        if large_unit and large_unit_count > 0:
            units_data.append({'name': large_unit, 'factor': float(large_unit_count)})
        _save_item_units(item, _json.dumps(units_data), tenant)

    stock_id = request.POST.get('stock_id')
    opening_qty = safe_decimal_or_zero(request.POST.get('opening_quantity'))
    if stock_id and opening_qty:
        stock = Stock.objects.for_tenant(tenant).filter(pk=stock_id, is_active=True).first()
        if stock:
            apply_opening_stock(
                tenant, item, stock, opening_qty,
                batch_number=request.POST.get('batch_number', '').strip(),
                expiry_date=_parse_date_or_none(request.POST.get('expiry_date')),
            )

    log_activity(request, 'إضافة منتج من الكتالوج الرئيسي',
                 f"المنتج: {item.name}\nمرتبط بـ: {drug}", 'create')
    return JsonResponse({'success': True, 'message': 'تم إضافة المنتج بنجاح', 'id': item.id})


def _parse_date_or_none(val):
    if not val:
        return None
    from apps.core.io_utils import safe_date
    return safe_date(val)


def safe_decimal_or_zero(val):
    from decimal import Decimal, InvalidOperation
    if val in (None, ''):
        return Decimal('0')
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return Decimal('0')


@login_required
@require_permission('add_items')
@require_POST
def bulk_create_from_catalog(request):
    """Accepts a JSON body: {stock_id, items: [{master_drug_id, alias_id, cost_price, selling_price, opening_quantity}]}"""
    import json as _json
    from apps.stocks.models import Stock
    from .dedup import find_existing_item_match
    from .services import apply_opening_stock

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        body = _json.loads(request.body or '{}')
    except _json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    stock = None
    if body.get('stock_id'):
        stock = Stock.objects.for_tenant(tenant).filter(pk=body['stock_id'], is_active=True).first()

    created = 0
    skipped = []
    for row in body.get('items', []):
        drug = MasterDrug.objects.filter(pk=row.get('master_drug_id')).first()
        if not drug:
            continue
        alias = MasterDrugAlias.objects.filter(pk=row.get('alias_id')).first() if row.get('alias_id') else None
        name = alias.trade_name if alias else drug.generic_name
        barcode = alias.barcode if alias else ''

        dup = find_existing_item_match(tenant, name=name, generic_name=drug.generic_name,
                                        dosage_form=drug.dosage_form, strength=drug.strength, barcode=barcode)
        if dup and dup['match_type'] in ('barcode', 'exact'):
            skipped.append({'name': name, 'reason': f"موجود بالفعل: {dup['item'].name}"})
            continue

        with transaction.atomic():
            category = _resolve_tenant_category(tenant, drug.category.name if drug.category_id else '', request.user)
            item = Item(
                tenant=tenant, created_by=request.user, updated_by=request.user,
                name=name, barcode=barcode, category=category,
                generic_name=drug.generic_name, manufacturer=alias.manufacturer if alias else '',
                dosage_form=drug.dosage_form, strength=drug.strength,
                requires_prescription=drug.requires_prescription,
                is_controlled_substance=drug.is_controlled_substance,
                is_insurance_excluded=drug.is_insurance_excluded,
                cost_price=safe_decimal_or_zero(row.get('cost_price')),
                selling_price=safe_decimal_or_zero(row.get('selling_price')),
                master_drug=drug,
            )
            if tenant.hard_currency_mode and tenant.exchange_rate:
                rate = Decimal(str(tenant.exchange_rate))
                if rate > 0:
                    item.cost_price_hc = (item.cost_price / rate).quantize(Decimal('0.0001'))
                    item.selling_price_hc = (item.selling_price / rate).quantize(Decimal('0.0001'))
            item.save()
            opening_qty = safe_decimal_or_zero(row.get('opening_quantity'))
            if stock and opening_qty:
                apply_opening_stock(tenant, item, stock, opening_qty)
        created += 1

    log_activity(request, 'إضافة منتجات من الكتالوج الرئيسي (دفعة)', f"عدد المنتجات: {created}", 'create')
    return JsonResponse({'success': True, 'created': created, 'skipped': skipped})


# ============================================================
# Helpers
# ============================================================

def _save_item_units(item, units_json_str, tenant):
    """Parse units_json and sync ItemUnit records for this item."""
    import json
    from decimal import Decimal, InvalidOperation
    from .models import ItemUnit

    if not units_json_str:
        return

    try:
        units_data = json.loads(units_json_str)
    except (ValueError, TypeError):
        return

    if not isinstance(units_data, list) or not units_data:
        return

    # Delete old and recreate
    ItemUnit.objects.filter(item=item).delete()

    has_multiple = len(units_data) > 1
    for row in units_data:
        name = str(row.get('name', '')).strip()
        if not name:
            continue
        try:
            factor = Decimal(str(row.get('factor', 1)))
        except InvalidOperation:
            factor = Decimal('1')
        ItemUnit.objects.create(tenant=tenant, item=item, name=name, factor=factor)

    item.has_multiple_units = has_multiple
    item.save(update_fields=['has_multiple_units'])


# ============================================================
# Items: إنشاء
# ============================================================

@login_required
@require_permission('add_items')
@transaction.atomic
def item_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    from django.db import IntegrityError
    form = ItemForm(request.POST, request.FILES, tenant=tenant, capabilities=_get_capabilities(tenant))
    if form.is_valid():
        item = form.save(commit=False)
        item.tenant = tenant
        item.created_by = request.user
        item.updated_by = request.user
        _apply_hc_prices(item, tenant)
        try:
            item.save()
        except IntegrityError:
            return JsonResponse({'success': False, 'message': 'رمز المنتج (SKU) مستخدم بالفعل، يرجى اختيار رمز آخر', 'errors': {'sku': ['رمز المنتج مستخدم بالفعل']}}, status=400)
        form.save_m2m()
        _save_item_units(item, request.POST.get('units_json', ''), tenant)
        log_activity(request, 'إضافة منتج جديد',
                     f"المنتج: {item.name}\nكود: {item.sku or '—'}\nالنوع: {item.get_item_type_display()}", 'create')
        return JsonResponse({'success': True, 'message': 'تم إضافة المنتج بنجاح', 'id': item.id})

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_errors(form),
    }, status=400)


# ============================================================
# Items: تفاصيل
# ============================================================

@login_required
@require_permission('view_items')
def item_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        item = Item.objects.for_tenant(tenant).select_related('category', 'unit', 'purchase_unit', 'supplier').get(pk=pk)
    except Item.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المنتج غير موجود'}, status=404)

    iu_list = list(item.item_units.order_by('factor').values('id', 'name', 'factor'))
    return JsonResponse({
        'success': True,
        'data': {
            'id': item.id,
            'name': item.name,
            'name_en': item.name_en,
            'sku': item.sku,
            'barcode': item.barcode,
            'item_type': item.item_type,
            'category': item.category_id,
            'category_name': item.category.name if item.category else '',
            'supplier': item.supplier_id,
            'supplier_name': item.supplier.name if item.supplier else '',
            'unit': item.unit_id,
            'unit_name': item.unit.name if item.unit else '',
            'purchase_unit': item.purchase_unit_id,
            'purchase_unit_name': item.purchase_unit.name if item.purchase_unit else '',
            'cost_price': str(item.cost_price),
            'selling_price': str(item.selling_price),
            'min_selling_price': str(item.min_selling_price),
            'cost_price_hc': str(item.cost_price_hc) if item.cost_price_hc is not None else '',
            'selling_price_hc': str(item.selling_price_hc) if item.selling_price_hc is not None else '',
            'min_selling_price_hc': str(item.min_selling_price_hc) if item.min_selling_price_hc is not None else '',
            'tax_rate': str(item.tax_rate),
            'min_quantity': str(item.min_quantity),
            'max_quantity': str(item.max_quantity),
            'track_expiry': item.track_expiry,
            'track_batch': item.track_batch,
            'track_serial': item.track_serial,
            'generic_name': item.generic_name,
            'manufacturer': item.manufacturer,
            'country_of_origin': item.country_of_origin,
            'sudan_agent': item.sudan_agent,
            'pack_size': item.pack_size,
            'dosage_form': item.dosage_form,
            'strength': item.strength,
            'requires_prescription': item.requires_prescription,
            'is_controlled_substance': item.is_controlled_substance,
            'is_insurance_excluded': item.is_insurance_excluded,
            'alternatives': [{'id': a.id, 'name': a.name} for a in item.alternatives.all()],
            'description': item.description,
            'is_active': item.is_active,
            'is_sellable': item.is_sellable,
            'is_purchasable': item.is_purchasable,
            'has_multiple_units': item.has_multiple_units,
            'item_units': [
                {'id': u['id'], 'name': u['name'], 'factor': str(u['factor'])}
                for u in iu_list
            ],
        },
    })


@login_required
@require_permission('view_items')
def item_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        item = Item.objects.for_tenant(tenant).get(pk=pk)
    except Item.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المنتج غير موجود'}, status=404)

    is_service = item.item_type == 'service'
    if is_service:
        current_qty = 0
        movements = []
    else:
        branch = getattr(request, 'branch', None)
        current_qty = (
            filter_by_branch_via(
                StockQuantity.objects.for_tenant(tenant).filter(item=item), branch, field='stock__branch',
            )
            .aggregate(total=Sum('quantity'))['total']
            or 0
        )

        movements = (
            filter_by_branch_via(
                StockMovement.objects.for_tenant(tenant).filter(item=item), branch, field='stock__branch',
            )
            .select_related('stock')
            .order_by('-id')[:200]
        )

    data = [
        {
            'movement_date': m.movement_date.strftime('%Y-%m-%d'),
            'stock': m.stock.name,
            'movement_type': m.movement_type,
            'movement_type_label': m.get_movement_type_display(),
            'direction': m.direction,
            'quantity': str(m.quantity),
            'balance_after': str(m.balance_after),
            'reference_type': m.reference_type or '—',
            'notes': m.notes or '—',
            'is_reversal': m.is_reversal,
        }
        for m in movements
    ]

    return JsonResponse({
        'success': True,
        'data': {
            'item_id': item.id,
            'item_name': item.name,
            'item_sku': item.sku,
            'item_type': item.item_type,
            'is_service': is_service,
            'current_qty': str(current_qty),
            'movements': data,
        }
    })


# ============================================================
# Items: تعديل
# ============================================================

@login_required
@require_permission('change_items')
@transaction.atomic
def item_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        item = Item.objects.for_tenant(tenant).get(pk=pk)
    except Item.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المنتج غير موجود'}, status=404)

    form = ItemForm(request.POST, request.FILES, instance=item, tenant=tenant, capabilities=_get_capabilities(tenant))
    if form.is_valid():
        updated = form.save(commit=False)
        updated.updated_by = request.user
        _apply_hc_prices(updated, tenant)
        updated.save()
        form.save_m2m()
        _save_item_units(item, request.POST.get('units_json', ''), tenant)
        log_activity(request, 'تعديل منتج', updated.name, 'update')
        return JsonResponse({'success': True, 'message': 'تم تحديث المنتج بنجاح'})

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول',
        'errors': _serialize_errors(form),
    }, status=400)


# ============================================================
# Items: حذف
# ============================================================

@login_required
@require_permission('delete_items')
@require_permission('change_items')
def item_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        item = Item.objects.for_tenant(tenant).get(pk=pk)
    except Item.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المنتج غير موجود'}, status=404)

    name = item.name
    try:
        # BOMLine.component uses PROTECT; safe to remove before deletion
        # (BOM lines are recipe definitions, not transactional records)
        item.used_in_bom.all().delete()
        item.delete()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error('item_delete_api error pk=%s: %s', pk, e, exc_info=True)
        return JsonResponse({'success': False, 'message': 'تعذر الحذف: المنتج مرتبط بسجلات لا يمكن حذفها (فواتير، تحويلات، جرد)'}, status=400)
    log_activity(request, 'حذف منتج', name, 'delete')
    return JsonResponse({'success': True, 'message': f'تم حذف المنتج "{name}" بنجاح'})


# ============================================================
# API: بحث سريع (للاستخدام في فواتير البيع/الشراء)
# ============================================================

@login_required
@require_permission('view_items')
def item_search_api(request):
    """
    بحث سريع عن المنتجات - يُستخدم مستقبلاً في فواتير البيع والشراء.
    يرجع أول 15 نتيجة مطابقة.
    """
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    q = request.GET.get('q', '').strip()
    try:
        min_chars = int(request.GET.get('min_chars', 2))
    except (TypeError, ValueError):
        min_chars = 2
    min_chars = max(0, min(min_chars, 5))

    if len(q) < min_chars:
        return JsonResponse({'results': []})

    from .models import ItemUnit as ItemUnitModel
    limit = 100 if not q else 15
    types_param = request.GET.get('types', '').strip()
    type_list = [t.strip() for t in types_param.split(',') if t.strip()] if types_param else []
    if type_list:
        base_qs = Item.objects.for_tenant(tenant).filter(is_active=True, item_type__in=type_list)
    else:
        base_qs = Item.objects.for_tenant(tenant).filter(is_active=True, is_sellable=True)
    if q:
        qs = base_qs.filter(
            Q(name__icontains=q) |
            Q(barcode__icontains=q) |
            Q(sku__icontains=q) |
            Q(generic_name__icontains=q)
        ).prefetch_related('item_units')[:limit]
    else:
        qs = base_qs.order_by('name').prefetch_related('item_units')[:limit]

    results = []
    for item in qs:
        # Use per-product ItemUnit if defined, else fall back to legacy unit/purchase_unit
        iu_qs = list(item.item_units.order_by('factor'))
        if iu_qs:
            units    = [{'id': iu.id, 'name': iu.name, 'factor': str(iu.factor)} for iu in iu_qs]
            base_iu  = iu_qs[0]
            unit_id   = base_iu.id
            unit_name = base_iu.name
            unit_factor = '1'
        else:
            units = []
            unit_id   = None
            unit_name = ''
            unit_factor = '1'

        results.append({
            'id': item.id,
            'name': item.name,
            'sku': item.sku,
            'barcode': item.barcode,
            'item_type': item.item_type,
            'item_type_label': item.get_item_type_display(),
            'cost_price': str(item.cost_price),
            'selling_price': str(item.selling_price),
            'unit_id': unit_id,
            'unit_name': unit_name,
            'unit_factor': unit_factor,
            'units': units,
            'track_expiry': item.track_expiry,
            'track_batch': item.track_batch,
            'track_serial': item.track_serial,
        })
    return JsonResponse({'results': results})


# ============================================================
# Categories: CRUD APIs
# ============================================================

@login_required
@require_permission('view_categories')
def category_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 50))
    search_value = request.GET.get('search[value]', '').strip()

    qs = Category.objects.for_tenant(tenant).select_related('parent')
    records_total = qs.count()

    if search_value:
        qs = qs.filter(Q(name__icontains=search_value))

    records_filtered = qs.count()
    qs = qs[start:start + length]

    data = [
        {
            'id': c.id,
            'name': c.name,
            'full_path': c.full_path,
            'parent': c.parent.name if c.parent else '-',
            'icon': c.icon or 'fa-tag',
            'is_active': c.is_active,
            'display_order': c.display_order,
        }
        for c in qs
    ]
    return JsonResponse({
        'draw': draw, 'recordsTotal': records_total,
        'recordsFiltered': records_filtered, 'data': data,
    })


@login_required
@require_permission('view_categories')
def category_options_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    qs = Category.objects.for_tenant(tenant).order_by('display_order', 'name')
    if request.GET.get('all') != '1':
        qs = qs.filter(is_active=True)
    cats = qs.values('id', 'name', 'parent_id', 'icon', 'is_active', 'display_order')
    return JsonResponse({'success': True, 'categories': list(cats)})


@login_required
@require_permission('add_categories')
@transaction.atomic
def category_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    post_data = request.POST.copy()
    if not post_data.get('display_order'):
        post_data['display_order'] = '0'
    form = CategoryForm(post_data, tenant=tenant)
    if form.is_valid():
        cat = form.save(commit=False)
        cat.tenant = tenant
        cat.created_by = request.user
        cat.updated_by = request.user
        cat.display_order = cat.display_order or 0
        cat.save()
        return JsonResponse({'success': True, 'message': 'تم إضافة التصنيف بنجاح', 'id': cat.id})

    return JsonResponse({'success': False, 'message': 'خطأ في البيانات', 'errors': _serialize_errors(form)}, status=400)


@login_required
@require_permission('view_categories')
def category_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        cat = Category.objects.for_tenant(tenant).get(pk=pk)
    except Category.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'التصنيف غير موجود'}, status=404)

    return JsonResponse({'success': True, 'data': {
        'id': cat.id, 'name': cat.name, 'parent': cat.parent_id,
        'icon': cat.icon, 'description': cat.description,
        'display_order': cat.display_order, 'is_active': cat.is_active,
    }})


@login_required
@require_permission('change_categories')
@transaction.atomic
def category_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        cat = Category.objects.for_tenant(tenant).get(pk=pk)
    except Category.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'التصنيف غير موجود'}, status=404)

    form = CategoryForm(request.POST, instance=cat, tenant=tenant)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.updated_by = request.user
        updated.save()
        return JsonResponse({'success': True, 'message': 'تم تحديث التصنيف بنجاح'})

    return JsonResponse({'success': False, 'message': 'خطأ في البيانات', 'errors': _serialize_errors(form)}, status=400)


@login_required
@require_permission('delete_categories')
@require_permission('change_categories')
def category_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        cat = Category.objects.for_tenant(tenant).get(pk=pk)
    except Category.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'التصنيف غير موجود'}, status=404)

    item_count = Item.objects.for_tenant(tenant).filter(category=cat).count()
    if item_count:
        return JsonResponse({
            'success': False,
            'message': f'لا يمكن حذف هذا التصنيف لوجود {item_count} منتج مرتبط به.',
        }, status=400)

    name = cat.name
    cat.delete()
    return JsonResponse({'success': True, 'message': f'تم حذف التصنيف "{name}" بنجاح'})


# ============================================================
# Units: CRUD APIs
# ============================================================

@login_required
@require_permission('view_items')
def unit_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 50))
    search_value = request.GET.get('search[value]', '').strip()

    qs = Unit.objects.for_tenant(tenant).select_related('base_unit')
    records_total = qs.count()

    if search_value:
        qs = qs.filter(Q(name__icontains=search_value) | Q(abbreviation__icontains=search_value))

    records_filtered = qs.count()
    qs = qs[start:start + length]

    data = [
        {
            'id': u.id,
            'name': u.name,
            'abbreviation': u.abbreviation or '-',
            'base_unit': u.base_unit.name if u.base_unit else '-',
            'conversion_factor': str(u.conversion_factor),
            'is_active': u.is_active,
        }
        for u in qs
    ]
    return JsonResponse({
        'draw': draw, 'recordsTotal': records_total,
        'recordsFiltered': records_filtered, 'data': data,
    })


@login_required
@require_permission('view_items')
def unit_options_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    units = (
        Unit.objects.for_tenant(tenant)
        .filter(is_active=True)
        .order_by('name')
        .values('id', 'name')
    )
    return JsonResponse({'success': True, 'units': list(units)})


@login_required
@require_permission('add_items')
@transaction.atomic
def unit_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = UnitForm(request.POST, tenant=tenant)
    if form.is_valid():
        unit = form.save(commit=False)
        unit.tenant = tenant
        unit.created_by = request.user
        unit.updated_by = request.user
        unit.save()
        return JsonResponse({'success': True, 'message': 'تم إضافة وحدة القياس بنجاح', 'id': unit.id})

    return JsonResponse({'success': False, 'message': 'خطأ في البيانات', 'errors': _serialize_errors(form)}, status=400)


@login_required
@require_permission('view_items')
def unit_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        unit = Unit.objects.for_tenant(tenant).get(pk=pk)
    except Unit.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'وحدة القياس غير موجودة'}, status=404)

    return JsonResponse({'success': True, 'data': {
        'id': unit.id, 'name': unit.name, 'abbreviation': unit.abbreviation,
        'base_unit': unit.base_unit_id, 'conversion_factor': str(unit.conversion_factor),
        'is_active': unit.is_active,
    }})


@login_required
@require_permission('change_items')
@transaction.atomic
def unit_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        unit = Unit.objects.for_tenant(tenant).get(pk=pk)
    except Unit.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'وحدة القياس غير موجودة'}, status=404)

    form = UnitForm(request.POST, instance=unit, tenant=tenant)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.updated_by = request.user
        updated.save()
        return JsonResponse({'success': True, 'message': 'تم تحديث وحدة القياس بنجاح'})

    return JsonResponse({'success': False, 'message': 'خطأ في البيانات', 'errors': _serialize_errors(form)}, status=400)


@login_required
@require_permission('delete_items')
@require_permission('change_items')
def unit_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        unit = Unit.objects.for_tenant(tenant).get(pk=pk)
    except Unit.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'وحدة القياس غير موجودة'}, status=404)

    item_count = Item.objects.for_tenant(tenant).filter(
        Q(unit=unit) | Q(purchase_unit=unit)
    ).count()
    if item_count:
        return JsonResponse({
            'success': False,
            'message': f'لا يمكن حذف هذه الوحدة لوجود {item_count} منتج مرتبط بها.',
        }, status=400)

    name = unit.name
    unit.delete()
    return JsonResponse({'success': True, 'message': f'تم حذف وحدة القياس "{name}" بنجاح'})


# ══════════════════════════════════════════════════════
# IMPORT / EXPORT
# ══════════════════════════════════════════════════════

@login_required
@require_permission('export_items')
def item_export_api(request):
    from apps.core.io_utils import csv_response, csv_writer
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    response = csv_response('items.csv')
    writer = csv_writer(response)
    writer.writerow([
        'الاسم', 'الاسم الإنجليزي', 'الرمز (SKU)', 'الباركود',
        'التصنيف', 'الوحدة', 'وحدة الشراء',
        'سعر التكلفة', 'سعر البيع', 'أدنى سعر بيع',
        'الحد الأدنى للمخزون', 'الحد الأقصى للمخزون',
        'تتبع الصلاحية', 'تتبع الدفعات', 'تتبع السيريال',
        'للبيع', 'للشراء', 'نشط',
    ])
    qs = Item.objects.for_tenant(tenant).select_related('category').prefetch_related('item_units').order_by('name')
    for item in qs:
        writer.writerow([
            item.name,
            item.name_en or '',
            item.sku or '',
            item.barcode or '',
            item.category.name if item.category else '',
            item.base_unit_name,
            '',
            item.cost_price,
            item.selling_price,
            item.min_selling_price,
            item.min_quantity,
            item.max_quantity,
            'نعم' if item.track_expiry else 'لا',
            'نعم' if item.track_batch else 'لا',
            'نعم' if item.track_serial else 'لا',
            'نعم' if item.is_sellable else 'لا',
            'نعم' if item.is_purchasable else 'لا',
            'نعم' if item.is_active else 'لا',
        ])
    return response


# ============================================================
# استيراد / تصدير التصنيفات
# ============================================================

@login_required
@require_permission('view_items')
def category_export_api(request):
    from apps.core.io_utils import csv_response, csv_writer
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    response = csv_response('categories_export.csv')
    writer = csv_writer(response)
    writer.writerow(['الاسم', 'التصنيف الرئيسي', 'الوصف', 'الترتيب', 'الحالة'])

    qs = Category.objects.for_tenant(tenant).select_related('parent').order_by('display_order', 'name')
    for cat in qs:
        writer.writerow([
            cat.name,
            cat.parent.name if cat.parent else '',
            cat.description or '',
            cat.display_order,
            'نعم' if cat.is_active else 'لا',
        ])
    return response


_CATEGORY_FIELD_SCHEMA = [
    {"field": "name",          "description": "اسم التصنيف أو القسم أو الفئة", "required": True},
    {"field": "parent",        "description": "التصنيف الرئيسي أو الأب أو التصنيف الأعلى"},
    {"field": "description",   "description": "وصف التصنيف أو تفاصيله"},
    {"field": "display_order", "description": "ترتيب العرض أو الأولوية"},
    {"field": "is_active",     "description": "هل التصنيف نشط أو مفعّل"},
]


@login_required
@require_permission('add_items')
def category_import_api(request):
    from apps.core.io_utils import parse_uploaded_file, smart_get, bool_from_str
    from apps.ai.services import smart_map_headers

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'success': False, 'message': 'الرجاء اختيار ملف'}, status=400)

    rows, err = parse_uploaded_file(f)
    if err:
        return JsonResponse({'success': False, 'message': err}, status=400)

    if not rows:
        return JsonResponse({'success': False, 'message': 'الملف فارغ أو لا يحتوي على بيانات'}, status=400)

    actual_headers = list(rows[0].keys())
    try:
        mapping = smart_map_headers(actual_headers, _CATEGORY_FIELD_SCHEMA)
    except Exception:
        mapping = {}

    imported, errors = 0, []
    for i, row in enumerate(rows, start=2):
        name = smart_get(row, 'name', mapping, 'الاسم', 'اسم التصنيف', 'التصنيف', 'name')
        if not name:
            errors.append(f'الصف {i}: اسم التصنيف مطلوب')
            continue
        try:
            parent_name = smart_get(row, 'parent', mapping, 'التصنيف الرئيسي', 'الأب', 'parent')
            parent = None
            if parent_name:
                parent = Category.objects.for_tenant(tenant).filter(name=parent_name).first()

            is_active = bool_from_str(smart_get(row, 'is_active', mapping, 'الحالة', 'is_active', default='نعم'))
            try:
                display_order = int(smart_get(row, 'display_order', mapping, 'الترتيب', 'display_order', default='0'))
            except (ValueError, TypeError):
                display_order = 0

            description = smart_get(row, 'description', mapping, 'الوصف', 'description')

            cat, created = Category.objects.for_tenant(tenant).get_or_create(
                name=name,
                defaults={
                    'tenant': tenant,
                    'parent': parent,
                    'description': description,
                    'display_order': display_order,
                    'is_active': is_active,
                },
            )
            if not created:
                if parent is not None:
                    cat.parent = parent
                cat.is_active = is_active
                cat.display_order = display_order
                if description:
                    cat.description = description
                cat.save()
            imported += 1
        except Exception as exc:
            import logging, traceback
            logging.getLogger('items').error('category import row %d: %s\n%s', i, exc, traceback.format_exc())
            errors.append(f'الصف {i}: {exc}')

    msg = f'تم استيراد {imported} تصنيف بنجاح'
    if errors:
        msg += f'. {len(errors)} أخطاء'
    return JsonResponse({'success': True, 'message': msg, 'imported': imported, 'errors': errors[:10]})


@login_required
@require_permission('add_categories')
def category_download_template(request):
    from apps.core.io_utils import csv_response, csv_writer
    response = csv_response('categories_template.csv')
    writer = csv_writer(response)
    writer.writerow(['الاسم', 'التصنيف الرئيسي', 'الوصف', 'الترتيب', 'الحالة'])
    writer.writerow(['إلكترونيات', '', 'منتجات إلكترونية', '1', 'نعم'])
    writer.writerow(['هواتف', 'إلكترونيات', 'الهواتف الذكية', '1', 'نعم'])
    return response


# ============================================================
# BOM RECIPES — وصفات التصنيع
# ============================================================

@login_required
@require_permission('view_items')
def bom_recipe_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    recipes = BOMRecipe.objects.filter(tenant=tenant).select_related('item').order_by('item__name')
    return render(request, 'items/bom_list.html', {'recipes': recipes})


@login_required
@require_permission('view_items')
def bom_recipe_api(request):
    """DataTable JSON for BOM recipe list."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()

    qs = BOMRecipe.objects.filter(tenant=tenant).select_related('item')
    records_total = qs.count()

    if search_value:
        qs = qs.filter(Q(item__name__icontains=search_value))

    records_filtered = qs.count()
    qs = qs[start:start + length]

    data = [
        {
            'id': r.id,
            'item_name': r.item.name,
            'item_sku': r.item.sku,
            'lines_count': r.lines.count(),
            'is_active': r.is_active,
            'total_cost': str(r.total_cost),
        }
        for r in qs
    ]
    return JsonResponse({'draw': draw, 'recordsTotal': records_total, 'recordsFiltered': records_filtered, 'data': data})


@login_required
@require_permission('view_items')
def bom_recipe_detail(request, pk):
    """GET: show recipe detail. POST (AJAX): save/delete a BOM line."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    recipe = BOMRecipe.objects.filter(pk=pk, tenant=tenant).select_related('item').first()
    if not recipe:
        from django.http import Http404
        raise Http404

    if request.method == 'POST':
        import json as _json
        try:
            payload = _json.loads(request.body)
        except Exception:
            payload = {}

        action = payload.get('action', '')

        if action == 'add_line':
            component_id = payload.get('component_id')
            quantity = payload.get('quantity', '1')
            unit_id = payload.get('unit_id') or None
            notes = payload.get('notes', '')
            try:
                from .models import ItemUnit
                component = Item.objects.get(pk=component_id, tenant=tenant)
                unit = ItemUnit.objects.get(pk=unit_id, item=component) if unit_id else None
                from decimal import Decimal as D
                line = BOMLine.objects.create(
                    tenant=tenant,
                    recipe=recipe,
                    component=component,
                    quantity=D(str(quantity)),
                    unit=unit,
                    notes=notes,
                )
                recipe.item.cost_price = recipe.total_cost
                recipe.item.save(update_fields=['cost_price'])
                return JsonResponse({'success': True, 'id': line.id, 'message': 'تم إضافة المكوّن'})
            except Exception as e:
                return JsonResponse({'success': False, 'message': str(e)}, status=400)

        elif action == 'delete_line':
            line_id = payload.get('line_id')
            try:
                line = BOMLine.objects.get(pk=line_id, recipe=recipe, tenant=tenant)
                line.delete()
                recipe.item.cost_price = recipe.total_cost
                recipe.item.save(update_fields=['cost_price'])
                return JsonResponse({'success': True, 'message': 'تم حذف المكوّن'})
            except BOMLine.DoesNotExist:
                return JsonResponse({'success': False, 'message': 'المكوّن غير موجود'}, status=404)

        return JsonResponse({'success': False, 'message': 'إجراء غير معروف'}, status=400)

    lines = recipe.lines.select_related('component', 'unit').order_by('id')
    return render(request, 'items/bom_detail.html', {
        'recipe': recipe,
        'lines': lines,
    })


@login_required
@require_permission('add_items')
def bom_recipe_create_ajax(request):
    """Create a BOM recipe for an item (AJAX POST)."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    import json as _json
    try:
        payload = _json.loads(request.body)
    except Exception:
        payload = {}

    item_id = payload.get('item_id')
    try:
        item = Item.objects.get(pk=item_id, tenant=tenant)
    except Item.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المنتج غير موجود'}, status=404)

    if hasattr(item, 'bom_recipe'):
        return JsonResponse({'success': False, 'message': 'توجد وصفة بالفعل لهذا المنتج', 'id': item.bom_recipe.pk})

    recipe = BOMRecipe.objects.create(
        tenant=tenant,
        item=item,
        notes=payload.get('notes', ''),
        is_active=True,
        created_by=request.user,
        updated_by=request.user,
    )
    return JsonResponse({'success': True, 'id': recipe.pk, 'message': 'تم إنشاء الوصفة'})


@login_required
@require_permission('delete_items')
@require_POST
def bom_recipe_delete_ajax(request, pk):
    """حذف وصفة تصنيع (AJAX POST)."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    try:
        recipe = BOMRecipe.objects.get(pk=pk, tenant=tenant)
        recipe.delete()
        return JsonResponse({'success': True, 'message': 'تم حذف الوصفة بنجاح'})
    except BOMRecipe.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الوصفة غير موجودة'}, status=404)


# ============================================================
# ITEM META API — معلومات المنتج للنماذج الديناميكية
# ============================================================

@login_required
@require_permission('view_items')
def item_meta_api(request, pk):
    """Returns item tracking flags for dynamic form behavior"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    from django.shortcuts import get_object_or_404
    item = get_object_or_404(Item, pk=pk, tenant=tenant)
    iu_qs = list(item.item_units.order_by('factor'))
    if iu_qs:
        units     = [{'id': u.id, 'name': u.name, 'factor': str(u.factor)} for u in iu_qs]
        unit_id   = iu_qs[0].id
        unit_name = iu_qs[0].name
    else:
        units     = []
        unit_id   = None
        unit_name = item.base_unit_name
    return JsonResponse({
        'id': item.id,
        'name': item.name,
        'track_expiry': item.track_expiry,
        'track_batch': item.track_batch,
        'track_serial': item.track_serial,
        'cost_price': str(item.cost_price),
        'selling_price': str(item.selling_price),
        'unit_id': unit_id,
        'unit_name': unit_name,
        'units': units,
    })


# ============================================================
# ITEM BATCHES — دفعات المنتج
# ============================================================

@login_required
@require_permission('view_items')
def item_batches(request, pk):
    """قائمة دفعات منتج معين"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    from django.shortcuts import get_object_or_404
    item = get_object_or_404(Item, pk=pk, tenant=tenant)
    batches = ItemBatch.objects.filter(tenant=tenant, item=item).select_related('stock').order_by('expiry_date')
    return render(request, 'items/item_batches.html', {'item': item, 'batches': batches})

