"""
Stocks Views - عمليات CRUD للمخازن
كل العمليات عبر JSON API (AJAX) + صفحة واحدة للعرض
"""
import json
import csv
from datetime import timedelta
from apps.accounts.activity_service import log_activity
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
from .models import Stock, StockQuantity, StockTransfer, StockTransferLine, Stocktake, StocktakeLine, ManufacturingOrder
from .reports import StocksReportGenerator
from .services import confirm_stock_transfer, cancel_stock_transfer, confirm_stocktake, confirm_manufacturing_order, cancel_manufacturing_order


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
    qs = qs.select_related('item').prefetch_related('item__item_units')
    qs = qs.order_by('item__name')[start:start + length]

    data = []
    for sq in qs:
        iu_list = list(sq.item.item_units.order_by('factor'))
        units = [{'id': u.id, 'name': u.name, 'factor': str(u.factor)} for u in iu_list]
        data.append({
            'item_id': sq.item_id,
            'item_name': sq.item.name,
            'sku': sq.item.sku or '—',
            'barcode': sq.item.barcode or '—',
            'current_qty': fmt_qty(sq.quantity),
            'current_qty_raw': str(sq.quantity or Decimal('0')),
            'opening_qty': fmt_qty(sq.opening_quantity),
            'opening_qty_raw': str(sq.opening_quantity or Decimal('0')),
            'reserved_qty': fmt_qty(sq.reserved_quantity),
            'available_qty': fmt_qty(sq.available_quantity),
            'units': units,
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

        if sq.item.item_type == 'service':
            continue

        old_opening = sq.opening_quantity or Decimal('0')

        if old_opening == new_qty:
            continue

        is_first_entry = old_opening == Decimal('0')
        current_qty = sq.quantity or Decimal('0')

        if is_first_entry:
            # First entry: user pre-fills with current qty and edits as needed.
            # Set stock directly to new_qty instead of adding delta, so existing
            # stock from purchases isn't double-counted.
            delta = new_qty - current_qty
            sq.quantity = new_qty
            movement_type = 'opening_in'
            notes = 'إدخال الكمية الافتتاحية'
        else:
            # Correction: shift stock by the difference from old opening qty.
            delta = new_qty - old_opening
            sq.quantity = current_qty + delta
            movement_type = 'opening_correction'
            notes = f'تصحيح الكمية الافتتاحية: {old_opening:g} ← {new_qty:g}'

        sq.opening_quantity = new_qty
        sq.save(update_fields=['quantity', 'opening_quantity'])

        if delta == 0:
            updated_count += 1
            continue

        direction = 'in' if delta > 0 else 'out'
        StockMovement.objects.create(
            tenant=tenant,
            item=sq.item,
            stock=stock,
            movement_type=movement_type,
            direction=direction,
            quantity=abs(delta),
            unit_cost=sq.item.cost_price or Decimal('0'),
            movement_date=timezone.localdate(),
            reference_type='opening_balance',
            reference_id=sq.id,
            balance_after=sq.quantity,
            notes=notes,
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

    def fmt(v):
        return str((v or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    if stock_id:
        # ── مخزن محدد ──────────────────────────────────────────────────
        qs = StockQuantity.objects.filter(
            tenant=tenant,
            item__is_active=True,
            item__item_type__in=['product', 'raw_material', 'semi_finished'],
            stock_id=stock_id,
        ).select_related('item', 'stock').prefetch_related('item__item_units')

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
            threshold = sq.min_quantity or sq.item.min_quantity
            is_low = threshold > 0 and sq.quantity <= threshold
            is_zero = sq.quantity == Decimal('0')
            data.append({
                'item_name': sq.item.name,
                'sku': sq.item.sku or '—',
                'barcode': sq.item.barcode or '—',
                'stock_name': sq.stock.name,
                'unit': sq.item.base_unit_name or '—',
                'quantity': fmt(sq.quantity),
                'reserved': fmt(sq.reserved_quantity),
                'available': fmt(sq.available_quantity),
                'is_low': is_low,
                'is_zero': is_zero,
            })

    else:
        # ── كل المخازن: تجميع الكميات لكل صنف ────────────────────────
        from django.db.models import Sum
        from apps.items.models import Item as ItemModel

        agg_qs = (
            StockQuantity.objects
            .filter(tenant=tenant, item__is_active=True,
                    item__item_type__in=['product', 'raw_material', 'semi_finished'])
            .values('item_id')
            .annotate(
                total_quantity=Sum('quantity'),
                total_reserved=Sum('reserved_quantity'),
            )
        )

        if search_value:
            agg_qs = agg_qs.filter(
                Q(item__name__icontains=search_value)
                | Q(item__sku__icontains=search_value)
                | Q(item__barcode__icontains=search_value)
            )

        total = filtered = agg_qs.count()
        agg_page = list(agg_qs.order_by('item__name')[start:start + length])

        item_ids = [r['item_id'] for r in agg_page]
        items_map = {
            item.id: item
            for item in ItemModel.objects.filter(id__in=item_ids).prefetch_related('item_units')
        }

        data = []
        for row in agg_page:
            item = items_map.get(row['item_id'])
            if not item:
                continue
            qty = row['total_quantity'] or Decimal('0')
            reserved = row['total_reserved'] or Decimal('0')
            available = qty - reserved
            threshold = item.min_quantity or Decimal('0')
            is_low = threshold > 0 and qty <= threshold
            is_zero = qty == Decimal('0')
            data.append({
                'item_name': item.name,
                'sku': item.sku or '—',
                'barcode': item.barcode or '—',
                'stock_name': 'إجمالي كل المخازن',
                'unit': item.base_unit_name or '—',
                'quantity': fmt(qty),
                'reserved': fmt(reserved),
                'available': fmt(available),
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



# ─────────────────────────────────────────────────────────────────
#   NEW REPORTS: Item Movement / Low Stock Alert
# ─────────────────────────────────────────────────────────────────

def _parse_date(value):
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@login_required
@require_permission('view_stocks_item_movement_report')
def stocks_item_movement_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    item_id = request.GET.get('item_id') or None
    stock_id = request.GET.get('stock_id') or None
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    generator = StocksReportGenerator(tenant, start_date, end_date)
    report = generator.get_item_movement_report(item_id=item_id, stock_id=stock_id)

    selected_item_name = ''
    if item_id:
        selected_item = next((i for i in report['items'] if str(i.id) == str(item_id)), None)
        if selected_item:
            selected_item_name = selected_item.name

    return render(request, 'stocks/reports/item_movement.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'selected_item_id': item_id,
        'selected_item_name': selected_item_name,
        'selected_stock_id': stock_id,
        'section': 'stocks_reports',
    })


@login_required
@require_permission('view_stocks_item_movement_report')
def stocks_item_movement_report_export(request):
    import csv
    from django.http import HttpResponse

    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    item_id = request.GET.get('item_id') or None
    stock_id = request.GET.get('stock_id') or None
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = StocksReportGenerator(tenant, start_date, end_date).get_item_movement_report(item_id=item_id, stock_id=stock_id)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="item_movement_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['تاريخ الحركة', 'المنتج', 'المخزن', 'نوع الحركة', 'دخول', 'خروج', 'الرصيد بعد'])
    for row in report['data']:
        writer.writerow([row['movement_date'], row['item_name'], row['stock_name'], row['movement_type'], row['quantity_in'], row['quantity_out'], row['balance_after']])
    return response


@login_required
@require_permission('view_stocks_low_stock_report')
def stocks_low_stock_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = StocksReportGenerator(tenant).get_low_stock_report()

    return render(request, 'stocks/reports/low_stock.html', {
        'report': report,
        'section': 'stocks_reports',
    })


@login_required
@require_permission('view_stocks_low_stock_report')
def stocks_low_stock_report_export(request):
    import csv
    from django.http import HttpResponse

    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = StocksReportGenerator(tenant).get_low_stock_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="low_stock_alert.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['المنتج', 'الوحدة', 'المخزن', 'الكمية', 'المتاح', 'الحد الأدنى', 'العجز'])
    for row in report['data']:
        writer.writerow([row['item_name'], row['item_unit'], row['stock_name'], row['quantity'], row['available'], row['min_quantity'], row['shortage']])
    return response


# ─────────────────────────────────────────────────────────────────
#   INVENTORY VALUATION REPORT
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_stocks_valuation_report')
def stocks_valuation_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = StocksReportGenerator(tenant).get_valuation_report()

    return render(request, 'stocks/reports/valuation.html', {
        'report': report,
        'section': 'stocks_reports',
    })


@login_required
@require_permission('view_stocks_valuation_report')
def stocks_valuation_report_export(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = StocksReportGenerator(tenant).get_valuation_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="inventory_valuation.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['المنتج', 'الوحدة', 'الفئة', 'الكمية', 'سعر التكلفة', 'القيمة الإجمالية'])
    for cat in report['categories']:
        for row in cat['items']:
            writer.writerow([row['item_name'], row['item_unit'], row['category_name'], row['total_qty'], row['cost_price'], row['total_value']])
        writer.writerow(['', '', f'إجمالي {cat["name"]}', '', '', cat['subtotal']])
        writer.writerow([])
    writer.writerow(['', '', 'الإجمالي الكلي', '', '', report['summary']['grand_total']])
    return response


# ─────────────────────────────────────────────────────────────────
#   NON-MOVING ITEMS REPORT
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_stocks_non_moving_report')
def stocks_non_moving_report(request):
    from datetime import date as date_type
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if start_date:
        try:
            from datetime import datetime
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        except ValueError:
            start_date = None
    if end_date:
        try:
            from datetime import datetime
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            end_date = None

    start_date = start_date or (timezone.localdate() - timedelta(days=30))
    end_date = end_date or timezone.localdate()

    report = StocksReportGenerator(tenant, start_date, end_date).get_non_moving_report()

    return render(request, 'stocks/reports/non_moving.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'stocks_reports',
    })


@login_required
@require_permission('view_stocks_non_moving_report')
def stocks_non_moving_report_export(request):
    from datetime import datetime
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')
    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None
    except ValueError:
        start_date = end_date = None

    start_date = start_date or (timezone.localdate() - timedelta(days=30))
    end_date = end_date or timezone.localdate()

    report = StocksReportGenerator(tenant, start_date, end_date).get_non_moving_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="non_moving_items.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow([f'الفترة المرجعية: {start_date} إلى {end_date}'])
    writer.writerow([])
    writer.writerow(['المنتج', 'الوحدة', 'الفئة', 'الكمية', 'سعر التكلفة', 'القيمة', 'آخر حركة', 'أيام الركود'])
    for row in report['data']:
        writer.writerow([row['item_name'], row['item_unit'], row['category_name'],
                         row['total_qty'], row['cost_price'], row['total_value'],
                         row['last_movement_date'] or '—', row['days_idle'] or '—'])
    return response


# ============================================================
# تحويلات المخزون
# ============================================================

@login_required
@require_permission('transfer_stocks')
def transfer_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = StockTransfer.objects.for_tenant(tenant)
    stats = {
        'total':     qs.count(),
        'draft':     qs.filter(status='draft').count(),
        'confirmed': qs.filter(status='confirmed').count(),
        'cancelled': qs.filter(status='cancelled').count(),
    }
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)
    return render(request, 'stocks/transfer_list.html', {'stats': stats, 'stocks': stocks})


@login_required
@require_permission('transfer_stocks')
def transfer_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'no tenant'}, status=400)

    draw     = int(request.GET.get('draw', 1))
    start    = int(request.GET.get('start', 0))
    length   = int(request.GET.get('length', 25))
    search   = request.GET.get('search[value]', '').strip()
    status_f = request.GET.get('status', '').strip()
    stock_f  = request.GET.get('stock_id', '').strip()

    qs = StockTransfer.objects.for_tenant(tenant).select_related('from_stock', 'to_stock')
    total = qs.count()

    if status_f:
        qs = qs.filter(status=status_f)
    if stock_f:
        qs = qs.filter(Q(from_stock_id=stock_f) | Q(to_stock_id=stock_f))
    if search:
        qs = qs.filter(
            Q(transfer_number__icontains=search) |
            Q(from_stock__name__icontains=search) |
            Q(to_stock__name__icontains=search) |
            Q(notes__icontains=search)
        )

    filtered = qs.count()
    qs = qs[start:start + length]

    STATUS_LABELS = {'draft': 'مسودة', 'confirmed': 'مؤكد', 'cancelled': 'ملغي'}
    STATUS_COLORS = {'draft': 'secondary', 'confirmed': 'success', 'cancelled': 'danger'}

    rows = []
    for t in qs:
        badge = (
            f'<span class="badge bg-{STATUS_COLORS.get(t.status,"secondary")}">'
            f'{STATUS_LABELS.get(t.status, t.status)}</span>'
        )
        rows.append({
            'DT_RowId': f'row_{t.id}',
            'transfer_number': t.transfer_number,
            'transfer_date': str(t.transfer_date),
            'from_stock': t.from_stock.name,
            'to_stock': t.to_stock.name,
            'status_badge': badge,
            'status': t.status,
            'total_lines': t.lines.count(),
            'id': t.id,
        })

    return JsonResponse({'draw': draw, 'recordsTotal': total, 'recordsFiltered': filtered, 'data': rows})


@login_required
@require_permission('transfer_stocks')
def transfer_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    from apps.items.models import Item
    stocks  = Stock.objects.for_tenant(tenant).filter(is_active=True)
    items   = Item.objects.for_tenant(tenant).filter(is_active=True, item_type__in=['product', 'material'])

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = request.POST.dict()

        from_id   = data.get('from_stock')
        to_id     = data.get('to_stock')
        tdate     = data.get('transfer_date') or str(timezone.localdate())
        notes     = data.get('notes', '')
        lines_raw = data.get('lines', [])

        errors = {}
        if not from_id:
            errors['from_stock'] = ['المخزن المرسِل مطلوب']
        if not to_id:
            errors['to_stock'] = ['المخزن المستلِم مطلوب']
        if from_id and to_id and from_id == to_id:
            errors['to_stock'] = ['يجب أن يكون المخزنان مختلفَين']
        if not lines_raw:
            errors['lines'] = ['أضف بنداً واحداً على الأقل']

        if errors:
            first_msg = next(iter(errors.values()))[0]
            return JsonResponse({'success': False, 'message': first_msg, 'errors': errors}, status=400)

        try:
            from_stock = Stock.objects.for_tenant(tenant).get(pk=from_id)
            to_stock   = Stock.objects.for_tenant(tenant).get(pk=to_id)
        except Stock.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'مخزن غير موجود'}, status=400)

        with transaction.atomic():
            transfer = StockTransfer.objects.create(
                tenant=tenant,
                from_stock=from_stock,
                to_stock=to_stock,
                transfer_date=tdate,
                notes=notes,
            )
            for ln in lines_raw:
                item_id = ln.get('item_id')
                qty     = Decimal(str(ln.get('quantity', 0)))
                if not item_id or qty <= 0:
                    continue
                try:
                    item = Item.objects.for_tenant(tenant).get(pk=item_id)
                except Item.DoesNotExist:
                    continue
                StockTransferLine.objects.create(
                    tenant=tenant, transfer=transfer, item=item, quantity=qty,
                    notes=ln.get('notes', ''),
                )

        log_activity(request, 'إنشاء تحويل مخزون',
                     f"التحويل: {transfer.transfer_number}\nمن: {from_stock.name}\nإلى: {to_stock.name}", 'create')
        return JsonResponse({'success': True, 'id': transfer.id,
                             'redirect': f'/stocks/transfers/{transfer.id}/'})

    context = {'stocks': stocks, 'items': items, 'today': str(timezone.localdate())}
    return render(request, 'stocks/transfer_form.html', context)


@login_required
@require_permission('transfer_stocks')
def transfer_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    try:
        transfer = (
            StockTransfer.objects.for_tenant(tenant)
            .select_related('from_stock', 'to_stock')
            .prefetch_related('lines__item')
            .get(pk=pk)
        )
    except StockTransfer.DoesNotExist:
        from django.http import Http404
        raise Http404

    return render(request, 'stocks/transfer_detail.html', {'transfer': transfer})


@login_required
@require_permission('transfer_stocks')
def transfer_confirm_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        transfer = StockTransfer.objects.for_tenant(tenant).get(pk=pk)
        confirm_stock_transfer(transfer)
        log_activity(request, 'تأكيد تحويل مخزون', f'{transfer.transfer_number}', 'create')
        return JsonResponse({'success': True, 'message': 'تم تأكيد التحويل بنجاح'})
    except (StockTransfer.DoesNotExist, StockQuantity.DoesNotExist):
        return JsonResponse({'success': False, 'message': 'التحويل أو الكمية غير موجودة'}, status=404)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


@login_required
@require_permission('transfer_stocks')
def transfer_cancel_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        transfer = StockTransfer.objects.for_tenant(tenant).get(pk=pk)
        cancel_stock_transfer(transfer)
        log_activity(request, 'إلغاء تحويل مخزون', f'{transfer.transfer_number}', 'delete')
        return JsonResponse({'success': True, 'message': 'تم إلغاء التحويل'})
    except (StockTransfer.DoesNotExist, StockQuantity.DoesNotExist):
        return JsonResponse({'success': False, 'message': 'التحويل غير موجود'}, status=404)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


@login_required
@require_permission('transfer_stocks')
def transfer_delete_draft_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        transfer = StockTransfer.objects.for_tenant(tenant).get(pk=pk, status='draft')
        transfer.delete()
        return JsonResponse({'success': True})
    except StockTransfer.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المسودة غير موجودة'}, status=404)


@login_required
@require_permission('transfer_stocks')
def transfer_items_api(request):
    """Returns available quantity for items in a given stock."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'no tenant'}, status=400)

    stock_id = request.GET.get('stock_id')
    search   = request.GET.get('q', '').strip()

    from apps.items.models import Item, ItemUnit
    qs = Item.objects.for_tenant(tenant).filter(
        is_active=True, item_type__in=['product', 'material']
    )
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search))

    qs = list(qs.prefetch_related('item_units').order_by('name')[:60])

    item_ids = [i.id for i in qs]

    qty_map = {}
    if stock_id and item_ids:
        for sq in StockQuantity.objects.filter(
            tenant=tenant, stock_id=stock_id, item_id__in=item_ids
        ).values('item_id', 'quantity', 'reserved_quantity'):
            avail = float((sq['quantity'] or 0) - (sq['reserved_quantity'] or 0))
            qty_map[sq['item_id']] = avail

    def _base_unit_name(item):
        units = list(item.item_units.order_by('factor'))
        if units:
            return units[0].name
        return item.base_unit_name

    data = []
    for item in qs:
        iu_list = list(item.item_units.order_by('factor'))
        units = [{'id': u.id, 'name': u.name, 'factor': str(u.factor)} for u in iu_list]
        data.append({
            'id': item.id,
            'name': item.name,
            'sku': item.sku or '',
            'unit': _base_unit_name(item),
            'units': units,
            'available_qty': qty_map.get(item.id, 0),
        })

    return JsonResponse({'items': data})


# ============================================================
# جرد المخزون (Stocktake)
# ============================================================

@login_required
@require_permission('adjust_stocks')
def stocktake_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Stocktake.objects.for_tenant(tenant)
    stats = {
        'total':     qs.count(),
        'draft':     qs.filter(status='draft').count(),
        'confirmed': qs.filter(status='confirmed').count(),
        'cancelled': qs.filter(status='cancelled').count(),
    }
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)
    return render(request, 'stocks/stocktake_list.html', {'stats': stats, 'stocks': stocks})


@login_required
@require_permission('adjust_stocks')
def stocktake_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'no tenant'}, status=400)

    draw     = int(request.GET.get('draw', 1))
    start    = int(request.GET.get('start', 0))
    length   = int(request.GET.get('length', 25))
    search   = request.GET.get('search[value]', '').strip()
    status_f = request.GET.get('status', '').strip()
    stock_f  = request.GET.get('stock_id', '').strip()

    qs = Stocktake.objects.for_tenant(tenant).select_related('stock')
    total = qs.count()

    if status_f:
        qs = qs.filter(status=status_f)
    if stock_f:
        qs = qs.filter(stock_id=stock_f)
    if search:
        qs = qs.filter(
            Q(stocktake_number__icontains=search) | Q(stock__name__icontains=search)
        )

    filtered = qs.count()
    qs = qs[start:start + length]

    STATUS_LABELS = {'draft': 'جاري الجرد', 'confirmed': 'مكتمل', 'cancelled': 'ملغي'}
    STATUS_COLORS = {'draft': 'warning', 'confirmed': 'success', 'cancelled': 'danger'}

    rows = []
    for t in qs:
        badge = (
            f'<span class="badge bg-{STATUS_COLORS.get(t.status,"secondary")}">'
            f'{STATUS_LABELS.get(t.status, t.status)}</span>'
        )
        rows.append({
            'DT_RowId': f'row_{t.id}',
            'stocktake_number': t.stocktake_number,
            'stocktake_date': str(t.stocktake_date),
            'stock': t.stock.name,
            'total_lines': t.lines.count(),
            'status_badge': badge,
            'status': t.status,
            'id': t.id,
        })

    return JsonResponse({'draw': draw, 'recordsTotal': total, 'recordsFiltered': filtered, 'data': rows})


@login_required
@require_permission('adjust_stocks')
def stocktake_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = request.POST.dict()

        stock_id = data.get('stock_id')
        tdate    = data.get('stocktake_date') or str(timezone.localdate())
        notes    = data.get('notes', '')

        if not stock_id:
            return JsonResponse({'success': False, 'errors': {'stock_id': ['المخزن مطلوب']}}, status=400)

        try:
            stock = Stock.objects.for_tenant(tenant).get(pk=stock_id)
        except Stock.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=400)

        with transaction.atomic():
            stocktake = Stocktake.objects.create(
                tenant=tenant, stock=stock,
                stocktake_date=tdate, notes=notes,
            )
            # Pre-populate lines with all items that have stock in this warehouse
            sq_qs = StockQuantity.objects.filter(
                tenant=tenant, stock=stock
            ).select_related('item').exclude(item__item_type='service')

            lines_to_create = []
            for sq in sq_qs:
                lines_to_create.append(StocktakeLine(
                    tenant=tenant,
                    stocktake=stocktake,
                    item=sq.item,
                    system_quantity=sq.quantity,
                    counted_quantity=sq.quantity,
                ))
            StocktakeLine.objects.bulk_create(lines_to_create)

        log_activity(request, 'إنشاء جرد مخزون',
                     f"الجرد: {stocktake.stocktake_number}\nالمخزن: {stock.name}\nعدد الأصناف: {len(lines_to_create)}", 'create')
        return JsonResponse({'success': True, 'id': stocktake.id,
                             'redirect': f'/stocks/stocktakes/{stocktake.id}/'})

    context = {'stocks': stocks, 'today': str(timezone.localdate())}
    return render(request, 'stocks/stocktake_form.html', context)


@login_required
@require_permission('adjust_stocks')
def stocktake_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    try:
        stocktake = (
            Stocktake.objects.for_tenant(tenant)
            .select_related('stock')
            .prefetch_related('lines__item__item_units')
            .get(pk=pk)
        )
    except Stocktake.DoesNotExist:
        from django.http import Http404
        raise Http404

    return render(request, 'stocks/stocktake_detail.html', {'stocktake': stocktake})


@login_required
@require_permission('adjust_stocks')
def stocktake_save_counts_ajax(request, pk):
    """Save counted quantities without confirming."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        stocktake = Stocktake.objects.for_tenant(tenant).get(pk=pk, status='draft')
    except Stocktake.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الجرد غير موجود أو مؤكد بالفعل'}, status=404)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    counts = data.get('counts', {})  # {line_id: counted_qty}
    with transaction.atomic():
        for line_id, qty_val in counts.items():
            try:
                qty = Decimal(str(qty_val))
                StocktakeLine.objects.filter(
                    pk=int(line_id), stocktake=stocktake
                ).update(counted_quantity=qty)
            except (InvalidOperation, ValueError):
                pass

    return JsonResponse({'success': True})


@login_required
@require_permission('adjust_stocks')
def stocktake_confirm_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        stocktake = Stocktake.objects.for_tenant(tenant).get(pk=pk)
        confirm_stocktake(stocktake)
        return JsonResponse({'success': True, 'message': 'تم تأكيد الجرد وتطبيق التعديلات'})
    except Stocktake.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الجرد غير موجود'}, status=404)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


@login_required
@require_permission('adjust_stocks')
def stocktake_cancel_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        stocktake = Stocktake.objects.for_tenant(tenant).get(pk=pk)
        if stocktake.status == 'confirmed':
            return JsonResponse({'success': False, 'message': 'لا يمكن إلغاء جرد مؤكد'}, status=400)
        stocktake.status = 'cancelled'
        stocktake.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'success': True})
    except Stocktake.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الجرد غير موجود'}, status=404)


# ============================================================
# MANUFACTURING ORDERS — أوامر التصنيع
# ============================================================

@login_required
@require_permission('view_stocks')
def manufacturing_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    qs = ManufacturingOrder.objects.filter(tenant=tenant)
    stats = {
        'total': qs.count(),
        'draft': qs.filter(status='draft').count(),
        'confirmed': qs.filter(status='confirmed').count(),
        'cancelled': qs.filter(status='cancelled').count(),
    }
    return render(request, 'stocks/manufacturing_list.html', {'stats': stats})


@login_required
@require_permission('view_stocks')
def manufacturing_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '').strip()

    qs = ManufacturingOrder.objects.filter(tenant=tenant).select_related('recipe__item', 'stock')
    records_total = qs.count()

    if status_filter:
        qs = qs.filter(status=status_filter)

    if search_value:
        qs = qs.filter(
            Q(order_number__icontains=search_value) |
            Q(recipe__item__name__icontains=search_value)
        )

    records_filtered = qs.count()
    qs = qs.order_by('-order_date', '-id')[start:start + length]

    data = [
        {
            'id': o.id,
            'order_number': o.order_number,
            'item_name': o.recipe.item.name,
            'stock_name': o.stock.name,
            'quantity': str(o.quantity),
            'order_date': o.order_date.strftime('%Y-%m-%d'),
            'status': o.status,
            'status_display': o.get_status_display(),
            'cost': str(o.cost),
        }
        for o in qs
    ]
    return JsonResponse({'draw': draw, 'recordsTotal': records_total, 'recordsFiltered': records_filtered, 'data': data})


@login_required
@require_permission('add_stocks')
def manufacturing_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    if request.method == 'POST':
        import json as _json
        try:
            payload = _json.loads(request.body)
        except Exception:
            payload = request.POST.dict()

        from apps.items.models import BOMRecipe
        try:
            recipe = BOMRecipe.objects.get(pk=payload.get('recipe_id'), tenant=tenant)
            stock = Stock.objects.get(pk=payload.get('stock_id'), tenant=tenant)
            from datetime import datetime as _dt
            order_date = _dt.strptime(payload.get('order_date', ''), '%Y-%m-%d').date()
            quantity = Decimal(str(payload.get('quantity', '1')))
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'بيانات غير صالحة: {e}'}, status=400)

        order = ManufacturingOrder.objects.create(
            tenant=tenant,
            recipe=recipe,
            stock=stock,
            quantity=quantity,
            order_date=order_date,
            notes=payload.get('notes', ''),
            created_by=request.user,
            updated_by=request.user,
        )
        log_activity(request, 'إنشاء أمر تصنيع',
                     f"الأمر: {order.order_number}\nالمنتج: {recipe.item.name}\nالكمية: {quantity}\nالمخزن: {stock.name}", 'create')
        return JsonResponse({'success': True, 'id': order.id, 'order_number': order.order_number})

    from apps.items.models import BOMRecipe
    recipes = BOMRecipe.objects.filter(tenant=tenant, is_active=True).select_related('item')
    stocks = Stock.objects.filter(tenant=tenant, is_active=True)
    return render(request, 'stocks/manufacturing_form.html', {
        'recipes': recipes,
        'stocks': stocks,
        'today': timezone.localdate().strftime('%Y-%m-%d'),
    })


@login_required
@require_permission('view_stocks')
def manufacturing_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    from django.shortcuts import get_object_or_404
    from django.db.models import F, ExpressionWrapper, DecimalField as DBDecimalField
    order = get_object_or_404(ManufacturingOrder, pk=pk, tenant=tenant)
    lines = order.recipe.lines.select_related('component', 'unit').annotate(
        total_qty=ExpressionWrapper(
            F('quantity') * order.quantity,
            output_field=DBDecimalField(max_digits=16, decimal_places=4)
        ),
        total_cost=ExpressionWrapper(
            F('quantity') * order.quantity * F('component__cost_price'),
            output_field=DBDecimalField(max_digits=16, decimal_places=4)
        )
    )
    return render(request, 'stocks/manufacturing_detail.html', {'order': order, 'lines': lines})


@login_required
@require_permission('add_stocks')
def manufacturing_confirm_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        order = ManufacturingOrder.objects.get(pk=pk, tenant=tenant)
        confirm_manufacturing_order(order)
        return JsonResponse({'success': True, 'message': 'تم تأكيد أمر التصنيع بنجاح'})
    except ManufacturingOrder.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الأمر غير موجود'}, status=404)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'خطأ غير متوقع: {e}'}, status=500)


@login_required
@require_permission('add_stocks')
def manufacturing_cancel_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        order = ManufacturingOrder.objects.get(pk=pk, tenant=tenant)
        cancel_manufacturing_order(order)
        return JsonResponse({'success': True, 'message': 'تم إلغاء أمر التصنيع'})
    except ManufacturingOrder.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الأمر غير موجود'}, status=404)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'خطأ غير متوقع: {e}'}, status=500)


@login_required
@require_permission('add_stocks')
def manufacturing_delete_ajax(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        order = ManufacturingOrder.objects.get(pk=pk, tenant=tenant)
        if order.status != 'draft':
            return JsonResponse({'success': False, 'message': 'لا يمكن حذف إلا المسودات'}, status=400)
        order.delete()
        return JsonResponse({'success': True, 'message': 'تم حذف الأمر'})
    except ManufacturingOrder.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الأمر غير موجود'}, status=404)
