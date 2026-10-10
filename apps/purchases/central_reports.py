"""
تقرير المشتريات المركزية حسب المورد (النمط الهجين، للإدارة فقط) — ضمن «تقارير المشتريات»:
فواتير الشراء على المخزن المركزي ومرتجعاتها خلال فترة، مجمّعة حسب المورد.
"""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.shortcuts import render

from apps.accounts.decorators import require_permission
from apps.stocks.central_reports import central_scope_or_404, csv_response, report_period

from .models import PurchaseInvoice, PurchaseReturn


def central_purchases_data(tenant, start, end):
    invoices = (PurchaseInvoice.objects.filter(
        tenant=tenant, stock__is_central=True, status__in=PurchaseInvoice.EFFECTIVE_STATUSES,
        invoice_date__gte=start, invoice_date__lte=end)
        .values('supplier_id', 'supplier__name')
        .annotate(total=Sum('grand_total'), count=Count('id')).order_by('-total'))
    returns = {
        r['original_invoice__supplier_id']: r['t'] or Decimal('0') for r in PurchaseReturn.objects.filter(
            tenant=tenant, original_invoice__stock__is_central=True, status='confirmed',
            return_date__gte=start, return_date__lte=end)
        .values('original_invoice__supplier_id').annotate(t=Sum('total_returned'))
    }
    rows = []
    totals = {'total': Decimal('0'), 'returns': Decimal('0'), 'count': 0}
    for r in invoices:
        total = r['total'] or Decimal('0')
        ret = returns.pop(r['supplier_id'], Decimal('0'))
        rows.append({'supplier': r['supplier__name'] or 'بدون مورد', 'count': r['count'],
                     'total': total, 'returns': ret, 'net': total - ret})
        totals['total'] += total
        totals['returns'] += ret
        totals['count'] += r['count']
    # مرتجعات خلال الفترة لموردين بلا فواتير في الفترة نفسها
    if returns:
        from apps.suppliers.models import Supplier
        names = dict(Supplier.objects.filter(tenant=tenant, pk__in=[k for k in returns if k])
                     .values_list('id', 'name'))
        for supplier_id, ret in returns.items():
            rows.append({'supplier': names.get(supplier_id, 'بدون مورد'), 'count': 0,
                         'total': Decimal('0'), 'returns': ret, 'net': -ret})
            totals['returns'] += ret
    totals['net'] = totals['total'] - totals['returns']
    return {'rows': rows, 'totals': totals}


@login_required
@require_permission('view_central_purchases_report')
def central_purchases_report(request):
    tenant = central_scope_or_404(request)
    start, end = report_period(request)
    return render(request, 'purchases/reports/central_by_supplier.html', {
        'report': central_purchases_data(tenant, start, end), 'section': 'purchases_reports',
        'start_date': start, 'end_date': end})


@login_required
@require_permission('view_central_purchases_report')
def central_purchases_report_export(request):
    tenant = central_scope_or_404(request)
    start, end = report_period(request)
    rows = central_purchases_data(tenant, start, end)['rows']
    return csv_response('central_purchases_by_supplier',
                        ['المورد', 'عدد الفواتير', 'المشتريات', 'المرتجعات', 'الصافي'],
                        [[r['supplier'], r['count'], r['total'], r['returns'], r['net']] for r in rows])
