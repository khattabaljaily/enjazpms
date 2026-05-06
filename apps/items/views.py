"""
Items Views - عمليات CRUD للمنتجات والتصنيفات والوحدات
كل العمليات عبر JSON API (AJAX) + صفحة واحدة لكل قسم
"""
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render

from apps.accounts.decorators import require_permission
from .forms import CategoryForm, ItemForm, ItemVariantForm, UnitForm
from .models import Category, Item, ItemVariant, Unit
from apps.sales.models import StockMovement
from apps.stocks.models import StockQuantity


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _serialize_errors(form):
    return {f: [str(e) for e in errs] for f, errs in form.errors.items()}


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

    # هل النظام صيدلية أو ما يحتاج expiry tracking؟
    business_features = {}
    if tenant.business_type:
        business_features = tenant.business_type.features or {}

    context = {
        'item_form': ItemForm(tenant=tenant),
        'stats': {
            'total': total,
            'active': active,
            'inactive': total - active,
            'out_of_stock': out_of_stock,
        },
        'business_features': business_features,
        'version_type': tenant.version_type,
        'business_type_slug': tenant.business_type.slug if tenant.business_type else '',
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
@require_permission('view_units')
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

    qs = Item.objects.for_tenant(tenant).select_related('category', 'unit')
    records_total = qs.count()

    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(is_active=False)

    if category_id:
        qs = qs.filter(category_id=category_id)

    if search_value:
        qs = qs.filter(
            Q(name__icontains=search_value) |
            Q(sku__icontains=search_value) |
            Q(barcode__icontains=search_value) |
            Q(name_en__icontains=search_value)
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

    data = [
        {
            'id': item.id,
            'sku': item.sku,
            'name': item.name,
            'item_type': item.item_type,
            'barcode': item.barcode or '-',
            'category': item.category.name if item.category else '-',
            'unit': str(item.unit) if item.unit else '-',
            'cost_price': str(item.cost_price),
            'selling_price': str(item.selling_price),
            'track_expiry': item.track_expiry,
            'track_serial': item.track_serial,
            'has_variants': item.has_variants,
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

    form = ItemForm(request.POST, request.FILES, tenant=tenant)
    if form.is_valid():
        item = form.save(commit=False)
        item.tenant = tenant
        item.created_by = request.user
        item.updated_by = request.user
        item.save()
        # بعد الـ save يُطلق الـ signal الذي يُنشئ StockQuantity تلقائياً
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
        item = Item.objects.for_tenant(tenant).get(pk=pk)
    except Item.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المنتج غير موجود'}, status=404)

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
            'unit': item.unit_id,
            'purchase_unit': item.purchase_unit_id,
            'cost_price': str(item.cost_price),
            'selling_price': str(item.selling_price),
            'min_selling_price': str(item.min_selling_price),
            'tax_rate': str(item.tax_rate),
            'min_quantity': str(item.min_quantity),
            'max_quantity': str(item.max_quantity),
            'track_expiry': item.track_expiry,
            'track_batch': item.track_batch,
            'track_serial': item.track_serial,
            'has_variants': item.has_variants,
            'description': item.description,
            'is_active': item.is_active,
            'is_sellable': item.is_sellable,
            'is_purchasable': item.is_purchasable,
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
        current_qty = (
            StockQuantity.objects.for_tenant(tenant)
            .filter(item=item)
            .aggregate(total=Sum('quantity'))['total']
            or 0
        )

        movements = (
            StockMovement.objects.for_tenant(tenant)
            .filter(item=item)
            .select_related('stock')
            .order_by('-movement_date', '-created_at')[:200]
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

    form = ItemForm(request.POST, request.FILES, instance=item, tenant=tenant)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.updated_by = request.user
        updated.save()
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
    item.delete()
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
    min_chars = max(1, min(min_chars, 5))

    if len(q) < min_chars:
        return JsonResponse({'results': []})

    qs = Item.objects.for_tenant(tenant).filter(
        is_active=True,
        is_sellable=True
    ).filter(
        Q(name__icontains=q) |
        Q(barcode__icontains=q) |
        Q(sku__icontains=q)
    ).select_related('unit')[:15]

    results = [
        {
            'id': item.id,
            'name': item.name,
            'sku': item.sku,
            'barcode': item.barcode,
            'selling_price': str(item.selling_price),
            'unit': str(item.unit) if item.unit else '',
            'has_variants': item.has_variants,
        }
        for item in qs
    ]
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
@require_permission('add_categories')
@transaction.atomic
def category_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = CategoryForm(request.POST, tenant=tenant)
    if form.is_valid():
        cat = form.save(commit=False)
        cat.tenant = tenant
        cat.created_by = request.user
        cat.updated_by = request.user
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
@require_permission('view_units')
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
@require_permission('add_units')
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
@require_permission('view_units')
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
@require_permission('change_units')
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
@require_permission('delete_units')
@require_permission('change_units')
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
