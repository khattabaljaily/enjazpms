"""
تقارير المخزن المركزي والشحنات (النمط الهجين) — للإدارة المركزية فقط:
  1) أرصدة المخزن المركزي وقيمتها بتكلفة الصنف.
  2) الشحنات والمرتجعات التي ما زالت في الطريق (وعمرها بالأيام).
  3) فروقات الاستلام (ما أُرسل وما استُلم فعلاً) خلال فترة.
  4) ملخص المشتريات المركزية خلال فترة (حسب المورد) ومرتجعاتها.
يمكن تصدير كل قسم CSV بـ ?export=valuation|transit|differences|purchases.
"""
import csv
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.decorators import require_permission
from apps.purchases.models import PurchaseInvoice, PurchaseReturn

from .models import Shipment, ShipmentLine, Stock, StockQuantity

_MONEY = DecimalField(max_digits=18, decimal_places=2)


def _date(value, default):
    from datetime import datetime
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


def central_report_data(tenant, start, end):
    central = Stock.objects.filter(tenant=tenant, is_central=True).first()

    valuation, total_value = [], Decimal('0')
    if central:
        rows = (StockQuantity.objects.filter(tenant=tenant, stock=central, quantity__gt=0)
                .select_related('item').order_by('item__name'))
        for sq in rows:
            value = (sq.quantity * (sq.item.cost_price or Decimal('0'))).quantize(Decimal('0.01'))
            total_value += value
            valuation.append({'item': sq.item.name, 'quantity': sq.quantity,
                              'unit_cost': sq.item.cost_price or Decimal('0'), 'value': value})

    today = timezone.localdate()
    transit = []
    in_transit = (Shipment.objects.filter(tenant=tenant, status='in_transit')
                  .select_related('branch', 'from_stock', 'to_stock').order_by('sent_at'))
    for sh in in_transit:
        value = sum((ln.quantity_sent * ln.unit_cost for ln in sh.lines.all()), Decimal('0'))
        sent_day = sh.sent_at.date() if sh.sent_at else sh.shipment_date
        transit.append({
            'id': sh.id, 'number': sh.shipment_number, 'kind': 'مرتجع' if sh.direction == 'to_central' else 'توزيع',
            'branch': sh.branch.name, 'date': sh.shipment_date, 'days': (today - sent_day).days,
            'lines': sh.lines.count(), 'value': value.quantize(Decimal('0.01')),
        })

    from datetime import datetime, time
    # نطاق زمني صريح (لا __date) حتى لا نعتمد على جداول المناطق الزمنية في MySQL.
    start_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end, time.max))
    differences = []
    diff_lines = (ShipmentLine.objects.filter(
        tenant=tenant, shipment__has_difference=True, shipment__received_at__gte=start_dt,
        shipment__received_at__lte=end_dt)
        .select_related('shipment__branch', 'item').order_by('-shipment__received_at'))
    for ln in diff_lines:
        diff = ln.difference
        if diff is None or diff == 0:
            continue
        differences.append({
            'number': ln.shipment.shipment_number,
            'kind': 'مرتجع' if ln.shipment.direction == 'to_central' else 'توزيع',
            'branch': ln.shipment.branch.name, 'item': ln.item.name,
            'sent': ln.quantity_sent, 'received': ln.quantity_received, 'difference': diff,
            'value': (diff * ln.unit_cost).quantize(Decimal('0.01')),
        })

    purchases = []
    inv_qs = (PurchaseInvoice.objects.filter(
        tenant=tenant, stock__is_central=True, status__in=PurchaseInvoice.EFFECTIVE_STATUSES,
        invoice_date__gte=start, invoice_date__lte=end)
        .values('supplier__name').annotate(total=Sum('grand_total')).order_by('-total'))
    returns_by_supplier = {
        r['original_invoice__supplier__name']: r['t'] for r in PurchaseReturn.objects.filter(
            tenant=tenant, original_invoice__stock__is_central=True, status='confirmed',
            return_date__gte=start, return_date__lte=end)
        .values('original_invoice__supplier__name').annotate(t=Sum('total_returned'))
    }
    purchases_total = returns_total = Decimal('0')
    for r in inv_qs:
        name = r['supplier__name'] or 'بدون مورد'
        ret = returns_by_supplier.get(r['supplier__name']) or Decimal('0')
        purchases_total += r['total'] or 0
        returns_total += ret
        purchases.append({'supplier': name, 'total': r['total'] or Decimal('0'), 'returns': ret,
                          'net': (r['total'] or Decimal('0')) - ret})

    return {
        'central': central, 'valuation': valuation, 'total_value': total_value,
        'transit': transit, 'differences': differences, 'purchases': purchases,
        'purchases_total': purchases_total, 'returns_total': returns_total,
        'purchases_net': purchases_total - returns_total,
    }


_EXPORTS = {
    'valuation': ('أرصدة المخزن المركزي', ['الصنف', 'الكمية', 'تكلفة الوحدة', 'القيمة'],
                  lambda d: [[r['item'], r['quantity'], r['unit_cost'], r['value']] for r in d['valuation']]),
    'transit': ('الشحنات في الطريق', ['الرقم', 'النوع', 'الفرع', 'التاريخ', 'الأيام', 'البنود', 'القيمة'],
                lambda d: [[r['number'], r['kind'], r['branch'], r['date'], r['days'], r['lines'], r['value']] for r in d['transit']]),
    'differences': ('فروقات الاستلام', ['الرقم', 'النوع', 'الفرع', 'الصنف', 'المرسل', 'المستلم', 'الفرق', 'القيمة'],
                    lambda d: [[r['number'], r['kind'], r['branch'], r['item'], r['sent'], r['received'], r['difference'], r['value']] for r in d['differences']]),
    'purchases': ('المشتريات المركزية', ['المورد', 'المشتريات', 'المرتجعات', 'الصافي'],
                  lambda d: [[r['supplier'], r['total'], r['returns'], r['net']] for r in d['purchases']]),
}


@login_required
@require_permission('view_central_reports')
def central_reports(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return redirect('core:no_tenant')
    if not tenant.is_hybrid_purchasing() or getattr(request, 'branch', None) is not None:
        raise Http404
    today = timezone.localdate()
    start = _date(request.GET.get('start_date'), today - timedelta(days=30))
    end = _date(request.GET.get('end_date'), today)
    data = central_report_data(tenant, start, end)

    export = request.GET.get('export')
    if export in _EXPORTS:
        title, headers, rows = _EXPORTS[export]
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="central_{export}.csv"'
        response.write('﻿')
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows(data))
        return response

    return render(request, 'stocks/central_reports.html', {**data, 'start_date': start, 'end_date': end})
