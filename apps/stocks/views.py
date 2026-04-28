"""
Stocks Views - عمليات CRUD للمخازن
كل العمليات عبر JSON API (AJAX) + صفحة واحدة للعرض
"""
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render

from .forms import StockForm
from .models import Stock, StockQuantity


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _serialize_errors(form):
    return {f: [str(e) for e in errs] for f, errs in form.errors.items()}


# ============================================================
# صفحة العرض الرئيسية
# ============================================================

@login_required
def stock_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Stock.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()

    # هل يُسمح بإضافة مخزن جديد؟ (حسب الـ version_type و max_stocks)
    can_add = active < tenant.max_stocks

    context = {
        'form': StockForm(),
        'stats': {
            'total': total,
            'active': active,
            'inactive': total - active,
        },
        'can_add': can_add,
        'max_stocks': tenant.max_stocks,
        'version_type': tenant.version_type,
    }
    return render(request, 'stocks/stock_list.html', context)


# ============================================================
# API: جدول البيانات (DataTables)
# ============================================================

@login_required
def stock_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '').strip()

    qs = Stock.objects.for_tenant(tenant)
    records_total = qs.count()

    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'inactive':
        qs = qs.filter(is_active=False)

    if search_value:
        qs = qs.filter(
            Q(name__icontains=search_value) |
            Q(code__icontains=search_value) |
            Q(address__icontains=search_value)
        )

    records_filtered = qs.count()

    order_col = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    col_name = request.GET.get(f'columns[{order_col}][data]', 'name')
    allowed = {'code': 'code', 'name': 'name', 'stock_type': 'stock_type',
               'is_active': 'is_active', 'created_at': 'created_at'}
    order_field = allowed.get(col_name, 'name')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    qs = qs.order_by(order_field)[start:start + length]

    data = [
        {
            'id': s.id,
            'code': s.code,
            'name': s.name,
            'stock_type': s.get_stock_type_display(),
            'stock_type_raw': s.stock_type,
            'address': s.address or '-',
            'is_default': s.is_default,
            'is_active': s.is_active,
        }
        for s in qs
    ]

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': data,
    })


# ============================================================
# API: إنشاء مخزن
# ============================================================

@login_required
@transaction.atomic
def stock_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # التحقق من الحد المسموح به حسب الباقة
    current_active = Stock.objects.for_tenant(tenant).filter(is_active=True).count()
    if current_active >= tenant.max_stocks:
        return JsonResponse({
            'success': False,
            'message': f'لقد وصلت للحد الأقصى المسموح به ({tenant.max_stocks} مخازن). يرجى ترقية الباقة.',
        }, status=403)

    form = StockForm(request.POST)
    if form.is_valid():
        stock = form.save(commit=False)
        stock.tenant = tenant
        stock.created_by = request.user
        stock.updated_by = request.user

        # إذا تم تعيينه كافتراضي، احذف القديم
        # كلتا العمليتين في نفس الـ transaction
        if stock.is_default:
            Stock.objects.for_tenant(tenant).filter(is_default=True).update(is_default=False)

        stock.save()
        # بعد الـ save يُطلق الـ signal الذي يُنشئ StockQuantity تلقائياً
        return JsonResponse({
            'success': True,
            'message': 'تم إضافة المخزن بنجاح',
            'id': stock.id,
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_errors(form),
    }, status=400)


# ============================================================
# API: تفاصيل مخزن
# ============================================================

@login_required
def stock_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        stock = Stock.objects.for_tenant(tenant).get(pk=pk)
    except Stock.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=404)

    return JsonResponse({
        'success': True,
        'data': {
            'id': stock.id,
            'name': stock.name,
            'code': stock.code,
            'stock_type': stock.stock_type,
            'address': stock.address,
            'notes': stock.notes,
            'is_active': stock.is_active,
            'is_default': stock.is_default,
        },
    })


# ============================================================
# API: تعديل مخزن
# ============================================================

@login_required
@transaction.atomic
def stock_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        stock = Stock.objects.for_tenant(tenant).get(pk=pk)
    except Stock.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=404)

    form = StockForm(request.POST, instance=stock)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.updated_by = request.user

        # تغيير الافتراضي + حفظ المخزن في transaction واحدة
        if updated.is_default:
            Stock.objects.for_tenant(tenant).exclude(pk=pk).filter(is_default=True).update(is_default=False)

        updated.save()
        return JsonResponse({'success': True, 'message': 'تم تحديث المخزن بنجاح'})

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول',
        'errors': _serialize_errors(form),
    }, status=400)


# ============================================================
# API: حذف مخزن
# ============================================================

@login_required
def stock_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        stock = Stock.objects.for_tenant(tenant).get(pk=pk)
    except Stock.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=404)

    # لا نحذف المخزن الوحيد أو الافتراضي
    if stock.is_default:
        return JsonResponse({
            'success': False,
            'message': 'لا يمكن حذف المخزن الافتراضي. قم بتعيين مخزن آخر كافتراضي أولاً.',
        }, status=400)

    active_count = Stock.objects.for_tenant(tenant).filter(is_active=True).count()
    if active_count <= 1:
        return JsonResponse({
            'success': False,
            'message': 'لا يمكن حذف المخزن الوحيد في النظام.',
        }, status=400)

    stock_name = stock.name
    stock.delete()
    return JsonResponse({'success': True, 'message': f'تم حذف المخزن "{stock_name}" بنجاح'})


# ============================================================
# API: تعيين مخزن كافتراضي
# ============================================================

@login_required
@transaction.atomic
def stock_set_default_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        stock = Stock.objects.for_tenant(tenant).get(pk=pk)
    except Stock.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=404)

    # عمليتان في transaction واحدة: إلغاء الافتراضي القديم + تعيين الجديد
    Stock.objects.for_tenant(tenant).filter(is_default=True).update(is_default=False)
    stock.is_default = True
    stock.save(update_fields=['is_default'])

    return JsonResponse({'success': True, 'message': f'تم تعيين "{stock.name}" كمخزن افتراضي'})
