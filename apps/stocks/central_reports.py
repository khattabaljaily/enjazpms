"""
تقارير شحنات المخزن المركزي (النمط الهجين، للإدارة فقط) — ضمن قسم «تقارير المخزن»:
  1) الشحنات والمرتجعات التي ما زالت في الطريق، وعمرها بالأيام.
  2) فروقات الاستلام (المرسل مقابل المستلَم) خلال فترة.
أرصدة المخزن المركزي وقيمته: تقارير المخزن المعتادة باختيار «المخزن المركزي» في فلتر الفرع.
المشتريات المركزية: apps/purchases/central_reports.py ضمن «تقارير المشتريات».
"""
import csv
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from apps.accounts.decorators import require_permission

from .models import Shipment, ShipmentLine

DIRECTION_LABELS = {'to_branch': 'توزيع', 'to_central': 'مرتجع'}


def central_scope_or_404(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant or not tenant.is_hybrid_purchasing() or getattr(request, 'branch', None) is not None:
        raise Http404
    return tenant


def report_period(request, days=30):
    def parse(value, default):
        try:
            return datetime.strptime(str(value), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return default
    today = timezone.localdate()
    return parse(request.GET.get('start_date'), today - timedelta(days=days)), parse(request.GET.get('end_date'), today)


def csv_response(filename, headers, rows):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(headers)
    writer.writerows(rows)
    return response


def shipments_in_transit_data(tenant):
    today = timezone.localdate()
    rows, total_value = [], Decimal('0')
    shipments = (Shipment.objects.filter(tenant=tenant, status='in_transit')
                 .select_related('branch').prefetch_related('lines').order_by('sent_at'))
    for sh in shipments:
        lines = list(sh.lines.all())
        value = sum((ln.quantity_sent * ln.unit_cost for ln in lines), Decimal('0')).quantize(Decimal('0.01'))
        sent_day = sh.sent_at.date() if sh.sent_at else sh.shipment_date
        total_value += value
        rows.append({
            'id': sh.id, 'number': sh.shipment_number, 'kind': DIRECTION_LABELS.get(sh.direction, ''),
            'branch': sh.branch.name, 'date': sh.shipment_date, 'days': (today - sent_day).days,
            'lines': len(lines), 'value': value,
        })
    return {'rows': rows, 'total_value': total_value,
            'distribution_count': sum(1 for r in rows if r['kind'] == 'توزيع'),
            'return_count': sum(1 for r in rows if r['kind'] == 'مرتجع')}


def shipment_differences_data(tenant, start, end):
    # نطاق زمني صريح (لا __date) حتى لا نعتمد على جداول المناطق الزمنية في MySQL.
    start_dt = timezone.make_aware(datetime.combine(start, time.min))
    end_dt = timezone.make_aware(datetime.combine(end, time.max))
    lines = (ShipmentLine.objects.filter(
        tenant=tenant, shipment__has_difference=True,
        shipment__received_at__gte=start_dt, shipment__received_at__lte=end_dt)
        .select_related('shipment__branch', 'item').order_by('-shipment__received_at'))
    rows, total_value = [], Decimal('0')
    for ln in lines:
        diff = ln.difference
        if not diff:
            continue
        value = (diff * ln.unit_cost).quantize(Decimal('0.01'))
        total_value += value
        rows.append({
            'shipment_id': ln.shipment_id, 'number': ln.shipment.shipment_number,
            'kind': DIRECTION_LABELS.get(ln.shipment.direction, ''), 'branch': ln.shipment.branch.name,
            'date': timezone.localtime(ln.shipment.received_at).date(), 'item': ln.item.name,
            'sent': ln.quantity_sent, 'received': ln.quantity_received, 'difference': diff, 'value': value,
        })
    return {'rows': rows, 'total_value': total_value,
            'shipment_count': len({r['shipment_id'] for r in rows})}


@login_required
@require_permission('view_central_reports')
def shipments_in_transit_report(request):
    tenant = central_scope_or_404(request)
    return render(request, 'stocks/reports/shipments_in_transit.html', {
        'report': shipments_in_transit_data(tenant), 'section': 'stocks_reports'})


@login_required
@require_permission('view_central_reports')
def shipments_in_transit_report_export(request):
    tenant = central_scope_or_404(request)
    rows = shipments_in_transit_data(tenant)['rows']
    return csv_response('shipments_in_transit',
                        ['الرقم', 'النوع', 'الفرع', 'التاريخ', 'الأيام', 'البنود', 'القيمة'],
                        [[r['number'], r['kind'], r['branch'], r['date'], r['days'], r['lines'], r['value']] for r in rows])


@login_required
@require_permission('view_central_reports')
def shipment_differences_report(request):
    tenant = central_scope_or_404(request)
    start, end = report_period(request)
    return render(request, 'stocks/reports/shipment_differences.html', {
        'report': shipment_differences_data(tenant, start, end), 'section': 'stocks_reports',
        'start_date': start, 'end_date': end})


@login_required
@require_permission('view_central_reports')
def shipment_differences_report_export(request):
    tenant = central_scope_or_404(request)
    start, end = report_period(request)
    rows = shipment_differences_data(tenant, start, end)['rows']
    return csv_response('shipment_differences',
                        ['الرقم', 'النوع', 'الفرع', 'تاريخ الاستلام', 'الصنف', 'المرسل', 'المستلم', 'الفرق', 'القيمة'],
                        [[r['number'], r['kind'], r['branch'], r['date'], r['item'], r['sent'], r['received'],
                          r['difference'], r['value']] for r in rows])
