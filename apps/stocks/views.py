"""
Stocks Views - عمليات CRUD للمخازن
كل العمليات عبر JSON API (AJAX) + صفحة واحدة للعرض
"""
import json
import csv
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.sales.models import StockMovement

from .forms import StockForm
from .models import Stock, StockQuantity
from .reports import StocksReportGenerator


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _serialize_errors(form):
    return {f: [str(e) for e in errs] for f, errs in form.errors.items()}


# ============================================================
# صفحة العرض الرئيسية
# ============================================================

@login_required
@require_permission('view_stocks')
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
@require_permission('view_stocks')
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
@require_permission('add_stocks')
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
@require_permission('view_stocks')
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
@require_permission('change_stocks')
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
@require_permission('delete_stocks')
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

    if stock.is_system_default:
        return JsonResponse({
            'success': False,
            'message': 'لا يمكن حذف المخزن الافتراضي النظامي.',
        }, status=400)

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
@require_permission('change_stocks')
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


# ============================================================
# صفحة الكميات الافتتاحية
# ============================================================

@login_required
@require_permission('view_stocks')
def opening_balance_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name')
    default_stock = stocks.filter(is_default=True).first() or stocks.first()

    return render(request, 'stocks/opening_balance_list.html', {
        'stocks': stocks,
        'default_stock': default_stock,
    })


# ============================================================
# API: جدول الكميات الافتتاحية (DataTables)
# ============================================================

@login_required
@require_permission('view_stocks')
def opening_balance_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    def fmt_qty(value):
        return str((value or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    stock_id = request.GET.get('stock_id')
    if not stock_id:
        return JsonResponse({'draw': 1, 'recordsTotal': 0, 'recordsFiltered': 0, 'data': []})

    try:
        stock = Stock.objects.for_tenant(tenant).get(id=stock_id, is_active=True)
    except Stock.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=404)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()

    qs = StockQuantity.objects.filter(
        tenant=tenant,
        stock=stock,
        item__is_active=True,
        item__item_type__in=['product', 'raw_material', 'semi_finished'],
    ).select_related('item')

    total = qs.count()

    if search_value:
        qs = qs.filter(
            Q(item__name__icontains=search_value)
            | Q(item__sku__icontains=search_value)
            | Q(item__barcode__icontains=search_value)
        )

    filtered = qs.count()
    qs = qs.order_by('item__name')[start:start + length]

    data = []
    for sq in qs:
        data.append({
            'item_id': sq.item_id,
            'item_name': sq.item.name,
            'sku': sq.item.sku or '—',
            'barcode': sq.item.barcode or '—',
            'current_qty': fmt_qty(sq.quantity),
            'reserved_qty': fmt_qty(sq.reserved_quantity),
            'available_qty': fmt_qty(sq.available_quantity),
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered,
        'data': data,
    })


# ============================================================
# API: حفظ الكميات الافتتاحية (Bulk Update)
# ============================================================

@login_required
@require_permission('change_stocks')
@transaction.atomic
def opening_balance_save_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    stock_id = body.get('stock_id')
    rows = body.get('rows', [])

    if not stock_id:
        return JsonResponse({'success': False, 'message': 'يرجى اختيار المخزن'}, status=400)

    try:
        stock = Stock.objects.for_tenant(tenant).get(id=stock_id, is_active=True)
    except Stock.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=404)

    if not rows:
        return JsonResponse({'success': False, 'message': 'لا توجد تعديلات للحفظ'}, status=400)

    updated_count = 0
    for row in rows:
        try:
            item_id = int(row.get('item_id'))
            new_qty = Decimal(str(row.get('quantity', '0'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (TypeError, ValueError, InvalidOperation):
            return JsonResponse({'success': False, 'message': 'قيمة كمية غير صالحة'}, status=400)

        if new_qty < 0:
            return JsonResponse({'success': False, 'message': 'لا يمكن إدخال كمية سالبة'}, status=400)

        try:
            sq = StockQuantity.objects.select_for_update().select_related('item').get(
                tenant=tenant,
                stock=stock,
                item_id=item_id,
            )
        except StockQuantity.DoesNotExist:
            continue

        old_qty = sq.quantity or Decimal('0')
        if sq.item.item_type == 'service':
            continue

        if old_qty == new_qty:
            continue

        delta = new_qty - old_qty
        sq.quantity = new_qty
        sq.save(update_fields=['quantity'])

        # سجل حركة مخزون لتتبع تعديل الرصيد الافتتاحي/التسوية
        movement_type = 'opening_in' if old_qty == Decimal('0') and delta > 0 else ('adjustment_in' if delta > 0 else 'adjustment_out')
        direction = 'in' if delta > 0 else 'out'
        StockMovement.objects.create(
            tenant=tenant,
            item=sq.item,
            stock=stock,
            movement_type=movement_type,
            direction=direction,
            quantity=abs(delta),
            unit_cost=sq.item.cost_price or Decimal('0'),
            movement_date=timezone.now().date(),
            reference_type='opening_balance',
            reference_id=sq.id,
            balance_after=sq.quantity,
            notes='تحديث من شاشة الكميات الافتتاحية',
        )

        updated_count += 1

    return JsonResponse({
        'success': True,
        'message': f'تم حفظ {updated_count} بند بنجاح',
        'updated_count': updated_count,
    })


# ============================================================
# صفحة أرصدة المخزون (عرض فقط)
# ============================================================

@login_required
@require_permission('view_stock_quantities')
def stock_quantities_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name')
    default_stock = stocks.filter(is_default=True).first() or stocks.first()

    return render(request, 'stocks/stock_quantities_list.html', {
        'stocks': stocks,
        'default_stock': default_stock,
    })


@login_required
@require_permission('view_stock_quantities')
def stock_quantities_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    stock_id = request.GET.get('stock_id', '')
    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()

    qs = StockQuantity.objects.filter(
        tenant=tenant,
        item__is_active=True,
        item__item_type__in=['product', 'raw_material', 'semi_finished'],
    ).select_related('item', 'stock')

    if stock_id:
        qs = qs.filter(stock_id=stock_id)

    total = qs.count()

    if search_value:
        qs = qs.filter(
            Q(item__name__icontains=search_value)
            | Q(item__sku__icontains=search_value)
            | Q(item__barcode__icontains=search_value)
            | Q(stock__name__icontains=search_value)
        )

    filtered = qs.count()
    qs = qs.order_by('stock__name', 'item__name')[start:start + length]

    def fmt(v):
        return str((v or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    data = []
    for sq in qs:
        threshold = sq.min_quantity or sq.item.min_quantity
        is_low = threshold > 0 and sq.quantity <= threshold
        is_zero = sq.quantity == Decimal('0')
        data.append({
            'item_name': sq.item.name,
            'sku': sq.item.sku or '—',
            'barcode': sq.item.barcode or '—',
            'stock_name': sq.stock.name,
            'quantity': fmt(sq.quantity),
            'reserved': fmt(sq.reserved_quantity),
            'available': fmt(sq.available_quantity),
            'is_low': is_low,
            'is_zero': is_zero,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered,
        'data': data,
    })


# ============================================================
# REPORTS - تقارير المخزن
# ============================================================

@login_required
@require_permission('view_stocks_summary_report')
def stocks_summary_report(request):
    """تقرير ملخص المخزن"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_summary_report()

    return render(request, 'stocks/reports/summary.html', {
        'report': report_data,
        'section': 'stocks_reports',
        'report_type': 'summary',
    })


@login_required
@require_permission('view_stocks_summary_report')
def stocks_summary_report_export(request):
    """تصدير تقرير ملخص المخزن"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_summary_report()

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stocks_summary.csv"'
    response.write('\ufeff')  # BOM for Excel

    writer = csv.writer(response)
    writer.writerow(['البند', 'القيمة'])
    
    for key, value in report_data['summary'].items():
        writer.writerow([key, value])

    return response


@login_required
@require_permission('view_stocks_by_item_report')
def stocks_by_item_report(request):
    """تقرير المخزن حسب المنتج"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_by_item_report()

    return render(request, 'stocks/reports/by_item.html', {
        'report': report_data,
        'section': 'stocks_reports',
        'report_type': 'by_item',
    })


@login_required
@require_permission('view_stocks_by_item_report')
def stocks_by_item_report_export(request):
    """تصدير تقرير المخزن حسب المنتج"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_by_item_report()

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stocks_by_item.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(['المنتج', 'الوحدة', 'الكمية المتاحة', 'الكمية المحجوزة', 'إجمالي الكمية', 'القيمة الإجمالية'])

    for item in report_data['data']:
        writer.writerow([
            item['item_name'],
            item['item_unit'],
            item['total_available'],
            item['total_reserved'],
            item['total_quantity'],
            item['total_value']
        ])

    return response


@login_required
@require_permission('view_stocks_by_category_report')
def stocks_by_category_report(request):
    """تقرير المخزن حسب الفئة"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_by_category_report()

    return render(request, 'stocks/reports/by_category.html', {
        'report': report_data,
        'section': 'stocks_reports',
        'report_type': 'by_category',
    })


@login_required
@require_permission('view_stocks_by_category_report')
def stocks_by_category_report_export(request):
    """تصدير تقرير المخزن حسب الفئة"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_by_category_report()

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stocks_by_category.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(['الفئة', 'عدد المنتجات', 'الكمية المتاحة', 'الكمية المحجوزة', 'إجمالي الكمية', 'القيمة الإجمالية'])

    for category in report_data['data']:
        writer.writerow([
            category['category_name'],
            category['item_count'],
            category['total_available'],
            category['total_reserved'],
            category['total_quantity'],
            category['total_value']
        ])

    return response


@login_required
@require_permission('view_stocks_by_location_report')
def stocks_by_stock_report(request):
    """تقرير المخزن حسب الموقع"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    # Optional stock filter
    stock_id = request.GET.get('stock_id')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_by_stock_report(stock_id=stock_id)

    # stocks list for dropdown
    stocks_list = Stock.objects.filter(tenant=tenant, is_active=True).order_by('name')

    return render(request, 'stocks/reports/by_stock.html', {
        'report': report_data,
        'section': 'stocks_reports',
        'report_type': 'by_stock',
        'stocks': stocks_list,
        'selected_stock_id': stock_id,
    })


@login_required
@require_permission('view_stocks_by_location_report')
def stocks_by_stock_report_export(request):
    """تصدير تقرير المخزن حسب الموقع"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    stock_id = request.GET.get('stock_id')

    generator = StocksReportGenerator(tenant)
    report_data = generator.get_by_stock_report(stock_id=stock_id)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stocks_by_location.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(['المخزن', 'النوع', 'عدد المنتجات', 'الكمية المتاحة', 'الكمية المحجوزة', 'إجمالي الكمية', 'القيمة الإجمالية'])

    for stock in report_data['data']:
        writer.writerow([
            stock['stock_name'],
            stock['stock_type'],
            stock['item_count'],
            stock['total_available'],
            stock['total_reserved'],
            stock['total_quantity'],
            stock['total_value']
        ])

    return response

