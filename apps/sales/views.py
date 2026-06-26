"""
وحدة المبيعات — Views
======================

الصفحات:
  invoice_list          قائمة الفواتير مع إحصائيات
  invoice_table_api     DataTable API
  invoice_create        إنشاء فاتورة جديدة (صفحة كاملة)
  invoice_detail        عرض/طباعة فاتورة
  invoice_edit          تعديل فاتورة مؤكدة
  invoice_confirm       تأكيد فاتورة (AJAX POST)
  invoice_cancel        إلغاء فاتورة (AJAX POST)
  return_list           قائمة المرتجعات
  return_table_api      DataTable API للمرتجعات
  return_create         إنشاء مرتجع (صفحة كاملة)
  return_confirm        تأكيد مرتجع (AJAX POST)
  return_cancel         إلغاء مرتجع مؤكد (AJAX POST)
  record_payment        تسجيل دفعة (AJAX POST)
  
  — AJAX Helpers —
  item_info_api         بيانات المنتج (سعر، مخزون، ضريبة)
  customer_info_api     بيانات العميل (رصيد، حد ائتمان)
"""

import json
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, date

from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from django.db import transaction
from django.db.models import Q, Sum, Count, DecimalField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.customers.models import Customer
from apps.items.models import Item
from apps.stocks.models import Stock, StockQuantity

from .models import (
    CustomerLedger,
    SaleInvoice,
    SaleInvoiceLine,
    SalePayment,
    SaleReturn,
    SaleReturnLine,
    StockMovement,
    SaleQuote,
    SaleQuoteLine,
)
from .services import (
    build_invoice_from_post,
    cancel_sale_invoice,
    cancel_sale_return,
    confirm_sale_invoice,
    confirm_sale_return,
    deliver_sale_invoice,
    edit_confirmed_invoice,
    record_customer_payment,
    build_quote_from_post,
    mark_quote_sent,
    mark_quote_accepted,
    mark_quote_rejected,
    cancel_sale_quote,
    convert_quote_to_invoice,
)
from .reports import SalesReportGenerator
from apps.accounts.activity_service import log_activity


# ─────────────────────────────────────────────
#   HELPERS
# ─────────────────────────────────────────────

def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _json_error(msg, status=400):
    return JsonResponse({'success': False, 'message': msg}, status=status, json_dumps_params={'ensure_ascii': False})


def _parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value

    value = str(value).strip()
    for fmt in (
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%Y/%m/%d',
        '%d-%m-%Y',
        '%d %B %Y',
        '%d %b %Y',
        '%d %B، %Y',
        '%d %b، %Y',
    ):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    month_names = {
        'يناير': 1, 'فبراير': 2, 'مارس': 3, 'أبريل': 4, 'ابريل': 4, 'مايو': 5,
        'يونيو': 6, 'يوليو': 7, 'أغسطس': 8, 'اغسطس': 8, 'سبتمبر': 9,
        'أكتوبر': 10, 'اكتوبر': 10, 'نوفمبر': 11, 'ديسمبر': 12,
        'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5,
        'June': 6, 'July': 7, 'August': 8, 'September': 9,
        'October': 10, 'November': 11, 'December': 12,
    }

    match = re.match(r'^\s*(\d{1,2})\s+([^\d,،]+)[,،]?\s+(\d{4})\s*$', value)
    if match:
        day = int(match.group(1))
        month_name = match.group(2).strip()
        year = int(match.group(3))
        month = month_names.get(month_name)
        if month:
            return date(year, month, day)

    raise ValueError(f"Unrecognized date format: {value}")


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data:
        payload.update(data)
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


# ─────────────────────────────────────────────
#   INVOICE LIST
# ─────────────────────────────────────────────

@login_required
@require_permission('view_sales')
def invoice_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = SaleInvoice.objects.for_tenant(tenant)
    total = qs.count()
    confirmed = qs.filter(status='confirmed').count()
    pending_delivery = qs.filter(status='pending_delivery').count()
    draft = qs.filter(status='draft').count()
    cancelled = qs.filter(status='cancelled').count()
    returned = qs.filter(status__in=['returned', 'partially_returned']).count()

    active_statuses = ['confirmed', 'partially_returned']
    # إجمالي المبيعات الفعلية = مؤكدة + (مرتجعة جزئياً بعد خصم المرتجع)
    returned_sq = (
        SaleReturn.objects
        .filter(original_invoice=OuterRef('pk'), status='confirmed')
        .values('original_invoice')
        .annotate(t=Sum('total_returned'))
        .values('t')
    )
    active_qs = (
        qs.filter(status__in=active_statuses)
        .annotate(returned_amt=Coalesce(Subquery(returned_sq, output_field=DecimalField(max_digits=14, decimal_places=2)), Decimal('0')))
    )
    agg = active_qs.aggregate(
        s=Sum('grand_total'),
        r=Sum('returned_amt'),
        p=Sum('paid_amount'),
    )
    grand_total_sum = (agg['s'] or Decimal('0')) - (agg['r'] or Decimal('0'))
    paid_total = agg['p'] or Decimal('0')
    unpaid_total = grand_total_sum - paid_total

    # للفلترة في الـ DataTable
    customers = Customer.objects.for_tenant(tenant).filter(is_active=True).values('id', 'name')
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True).values('id', 'name')
    from apps.agents.models import Agent as _Agent
    agents_qs = _Agent.objects.filter(tenant=tenant, is_active=True).values('id', 'name') if tenant.plan_allows('agents') else []

    context = {
        'stats': {
            'total': total,
            'confirmed': confirmed,
            'pending_delivery': pending_delivery,
            'draft': draft,
            'cancelled': cancelled,
            'returned': returned,
            'grand_total_sum': grand_total_sum,
            'paid_total': paid_total,
            'unpaid_total': unpaid_total,
        },
        'customers': list(customers),
        'stocks': list(stocks),
        'agents': list(agents_qs),
        'active_agent_id': request.GET.get('agent', ''),
    }
    return render(request, 'sales/invoice_list.html', context)


@login_required
@require_permission('view_sales')
def invoice_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '')
    customer_filter = request.GET.get('customer_id', '')
    payment_filter = request.GET.get('payment_method', '')
    agent_filter = request.GET.get('agent', '')

    _returned_sq = (
        SaleReturn.objects
        .filter(original_invoice=OuterRef('pk'), status='confirmed')
        .values('original_invoice')
        .annotate(t=Sum('total_returned'))
        .values('t')
    )

    qs = SaleInvoice.objects.for_tenant(tenant).select_related('customer', 'stock')
    total = qs.count()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if customer_filter:
        qs = qs.filter(customer_id=customer_filter)
    if payment_filter:
        qs = qs.filter(payment_method=payment_filter)
    if agent_filter:
        qs = qs.filter(agent_id=agent_filter)

    if search_value:
        qs = qs.filter(
            Q(invoice_number__icontains=search_value)
            | Q(customer__name__icontains=search_value)
            | Q(reference_number__icontains=search_value)
        )

    filtered_total = qs.count()

    order_col = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'desc')
    col_map = {
        '0': 'invoice_number',
        '1': 'invoice_date',
        '2': 'customer__name',
        '3': 'stock__name',
        '4': 'grand_total',
        '5': 'status',
    }
    order_field = col_map.get(order_col, 'invoice_date')
    if order_dir == 'desc':
        order_field = f'-{order_field}'
    qs = qs.order_by(order_field).annotate(
        returned_amt=Coalesce(Subquery(_returned_sq, output_field=DecimalField(max_digits=14, decimal_places=2)), Decimal('0'))
    )

    page_qs = qs[start: start + length]

    STATUS_LABELS = {
        'draft': ('مسودة', 'secondary'),
        'pending_delivery': ('قيد التسليم', 'pending'),
        'confirmed': ('مؤكدة', 'success'),
        'cancelled': ('ملغاة', 'danger'),
        'returned': ('مرتجعة', 'warning'),
        'partially_returned': ('مرتجعة جزئياً', 'warning'),
    }
    PAYMENT_LABELS = {
        'cash': 'نقداً',
        'bank': 'بنكي',
        'credit': 'آجل',
        'mixed': 'مختلط',
    }

    data = []
    for inv in page_qs:
        label, color = STATUS_LABELS.get(inv.status, (inv.status, 'secondary'))
        returned_amt = getattr(inv, 'returned_amt', Decimal('0')) or Decimal('0')
        net_total = inv.grand_total - returned_amt
        data.append({
            'id': inv.id,
            'invoice_number': inv.invoice_number,
            'invoice_date': inv.invoice_date.strftime('%Y-%m-%d'),
            'customer': inv.customer.name if inv.customer else '—',
            'stock': inv.stock.name,
            'payment_method': PAYMENT_LABELS.get(inv.payment_method, inv.payment_method),
            'grand_total': str(inv.grand_total),
            'returned_amount': str(returned_amt),
            'net_total': str(net_total),
            'paid_amount': str(inv.paid_amount),
            'remaining': str(inv.remaining_amount),
            'status': inv.status,
            'status_label': label,
            'status_color': color,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered_total,
        'data': data,
    }, json_dumps_params={'ensure_ascii': False})


@login_required
# ─────────────────────────────────────────────
#   INVOICE CREATE / EDIT
# ─────────────────────────────────────────────

@login_required
@require_permission('add_sales')
def invoice_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    customers = Customer.objects.for_tenant(tenant).filter(is_active=True)
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)
    items = Item.objects.for_tenant(tenant).filter(is_active=True, is_sellable=True)
    from apps.agents.models import Agent as _Agent
    agents = _Agent.objects.filter(tenant=tenant, is_active=True).order_by('name') if tenant.plan_allows('agents') else []

    # default stock
    default_stock = stocks.filter(is_default=True).first() or stocks.first()

    if request.method == 'POST':
        error = _process_invoice_post(request, tenant, invoice=None)
        if isinstance(error, SaleInvoice):
            inv = error
            customer = inv.customer.name if inv.customer else 'بدون عميل'
            log_activity(request, 'إنشاء فاتورة مبيعات',
                         f"الفاتورة: {inv.invoice_number}\nالعميل: {customer}\nالإجمالي: {inv.grand_total}", 'create')
            return redirect('sales:invoice_detail', pk=error.id)
        # إذا رجع dict فهو للـ AJAX
        if isinstance(error, JsonResponse):
            return error
        # خطأ عادي (لو حدث)
        return redirect('sales:invoice_create')

    context = {
        'customers': customers,
        'stocks': stocks,
        'items': items,
        'agents': agents,
        'default_stock': default_stock,
        'today': timezone.localdate().isoformat(),
        'action': 'create',
    }
    return render(request, 'sales/invoice_form.html', context)


@login_required
@require_permission('change_sales')
def invoice_edit(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)

    if invoice.status not in ('draft', 'confirmed'):
        return redirect('sales:invoice_detail', pk=pk)

    customers = Customer.objects.for_tenant(tenant).filter(is_active=True)
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)
    items = Item.objects.for_tenant(tenant).filter(is_active=True, is_sellable=True)
    from apps.agents.models import Agent as _Agent
    agents = _Agent.objects.filter(tenant=tenant, is_active=True).order_by('name') if tenant.plan_allows('agents') else []

    if request.method == 'POST':
        result = _process_invoice_post(request, tenant, invoice=invoice)
        if isinstance(result, SaleInvoice):
            return redirect('sales:invoice_detail', pk=result.id)
        if isinstance(result, JsonResponse):
            return result
        return redirect('sales:invoice_edit', pk=pk)

    existing_lines = []
    for line in invoice.lines.select_related('item').prefetch_related('item__item_units'):
        iu_list = list(line.item.item_units.order_by('factor'))
        units = [{'id': u.id, 'name': u.name, 'factor': str(u.factor)} for u in iu_list]
        unit_id = ''
        if iu_list:
            matched = next(
                (u for u in iu_list if abs(float(u.factor) - float(line.unit_factor or 1)) < 0.0001),
                iu_list[0]
            )
            unit_id = matched.id
        existing_lines.append({
            'item_id': line.item_id,
            'item_name': line.item.name,
            'item_sku': line.item.sku,
            'quantity': str(line.quantity),
            'unit_price': str(line.unit_price),
            'discount_percent': str(line.discount_percent),
            'tax_rate': str(line.tax_rate),
            'cost_price_snapshot': str(line.cost_price_snapshot),
            'batch_number': line.batch_number,
            'serial_number': line.serial_number,
            'expiry_date': line.expiry_date.isoformat() if line.expiry_date else '',
            'line_total': str(line.line_total),
            'unit_id': unit_id,
            'unit_name': iu_list[0].name if iu_list else '',
            'unit_factor': str(line.unit_factor) if line.unit_factor else '1',
            'units': units,
            'track_batch': line.item.track_batch,
            'track_serial': line.item.track_serial,
            'track_expiry': line.item.track_expiry,
        })

    context = {
        'invoice': invoice,
        'existing_lines': json.dumps(existing_lines, ensure_ascii=False),
        'customers': customers,
        'stocks': stocks,
        'items': items,
        'agents': agents,
        'today': timezone.localdate().isoformat(),
        'action': 'edit',
    }
    return render(request, 'sales/invoice_form.html', context)


def _process_invoice_post(request, tenant, invoice):
    """
    يعالج POST لإنشاء أو تعديل فاتورة.
    يُرجع SaleInvoice عند النجاح أو JsonResponse عند الخطأ.
    """
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_error('بيانات غير صالحة')

    header = body.get('header', {})
    lines_raw = body.get('lines', [])
    action = body.get('action', 'save_draft')  # save_draft | confirm

    if not lines_raw:
        return _json_error('لا يمكن حفظ فاتورة بدون بنود')

    lines_data = []
    for ld in lines_raw:
        try:
            lines_data.append({
                'item_id': int(ld['item_id']),
                'quantity': Decimal(str(ld['quantity'])),
                'unit_price': Decimal(str(ld['unit_price'] or '0')),
                'discount_percent': Decimal(str(ld.get('discount_percent') or '0')),
                'tax_rate': Decimal(str(ld.get('tax_rate') or '0')),
                'cost_price_snapshot': Decimal(str(ld.get('cost_price_snapshot', 0))),
                'batch_number': ld.get('batch_number', ''),
                'serial_number': ld.get('serial_number', ''),
                'expiry_date': ld.get('expiry_date') or None,
                'unit_id': int(ld['unit_id']) if ld.get('unit_id') else None,
                'unit_factor': Decimal(str(ld.get('unit_factor', 1) or 1)),
            })
        except (KeyError, InvalidOperation, ValueError) as e:
            return _json_error(f'خطأ في بيانات البنود: {e}')

    try:
        stock = Stock.objects.get(id=header.get('stock_id'), tenant=tenant)
    except Stock.DoesNotExist:
        return _json_error('المخزن المحدد غير موجود')

    try:
        with transaction.atomic():
            if invoice is None:
                inv = build_invoice_from_post(tenant, stock, header, lines_data, request.user)
            else:
                if invoice.status == 'confirmed':
                    confirmed_header = {
                        'invoice_date': header.get('invoice_date') or invoice.invoice_date,
                        'due_date': header.get('due_date') or None,
                        'payment_method': header.get('payment_method') or invoice.payment_method,
                        'invoice_discount_type': header.get('invoice_discount_type') or invoice.invoice_discount_type,
                        'invoice_discount_value': Decimal(str(header.get('invoice_discount_value') or 0)),
                        'cash_amount': Decimal(str(header.get('cash_amount') or 0)),
                        'bank_amount': Decimal(str(header.get('bank_amount') or 0)),
                        'bank_reference': header.get('bank_reference', ''),
                        'notes': header.get('notes', ''),
                        'reference_number': header.get('reference_number', ''),
                    }

                    # FK mapping for service layer (expects objects, not *_id keys)
                    customer_raw = header.get('customer_id')
                    if customer_raw:
                        confirmed_header['customer'] = Customer.objects.get(id=customer_raw, tenant=tenant)
                    else:
                        confirmed_header['customer'] = None

                    stock_raw = header.get('stock_id')
                    if stock_raw:
                        confirmed_header['stock'] = Stock.objects.get(id=stock_raw, tenant=tenant)

                    agent_raw = header.get('agent_id')
                    if agent_raw:
                        from apps.agents.models import Agent as _Agent
                        try:
                            confirmed_header['agent'] = _Agent.objects.get(id=agent_raw, tenant=tenant, is_active=True)
                        except _Agent.DoesNotExist:
                            confirmed_header['agent'] = None
                    else:
                        confirmed_header['agent'] = None

                    inv = edit_confirmed_invoice(
                        invoice, confirmed_header, lines_data, request.user
                    )
                    # إعادة التأكيد تتم داخل edit_confirmed_invoice
                    return JsonResponse({'success': True, 'redirect': f'/sales/{inv.id}/'})
                else:
                    # draft edit
                    invoice.lines.all().delete()
                    from apps.items.models import Item as _Item
                    for ld in lines_data:
                        item = _Item.objects.get(id=ld['item_id'], tenant=tenant)
                        line = SaleInvoiceLine(
                            tenant=tenant, invoice=invoice, item=item,
                            quantity=ld['quantity'], unit_price=ld['unit_price'],
                            discount_percent=ld['discount_percent'], tax_rate=ld['tax_rate'],
                            cost_price_snapshot=ld['cost_price_snapshot'],
                        )
                        line.calculate()
                        line.save()
                    # تحديث هيدر (تعيين صريح لتجنب أخطاء FK مثل customer)
                    if 'customer_id' in header:
                        customer_raw = header.get('customer_id')
                        invoice.customer_id = int(customer_raw) if customer_raw else None

                    if 'invoice_date' in header:
                        invoice.invoice_date = header.get('invoice_date') or invoice.invoice_date
                    if 'due_date' in header:
                        invoice.due_date = header.get('due_date') or None
                    if 'payment_method' in header:
                        invoice.payment_method = header.get('payment_method') or invoice.payment_method
                    if 'delivery_type' in header:
                        invoice.delivery_type = header.get('delivery_type') or invoice.delivery_type
                    if 'invoice_discount_type' in header:
                        invoice.invoice_discount_type = header.get('invoice_discount_type') or invoice.invoice_discount_type
                    if 'invoice_discount_value' in header:
                        invoice.invoice_discount_value = Decimal(str(header.get('invoice_discount_value') or 0))
                    if 'cash_amount' in header:
                        invoice.cash_amount = Decimal(str(header.get('cash_amount') or 0))
                    if 'bank_amount' in header:
                        invoice.bank_amount = Decimal(str(header.get('bank_amount') or 0))
                    if 'bank_reference' in header:
                        invoice.bank_reference = header.get('bank_reference', '')
                    if 'notes' in header:
                        invoice.notes = header.get('notes', '')

                    # المخزن يظل مطلوباً في التعديل
                    if 'stock_id' in header and header.get('stock_id'):
                        try:
                            invoice.stock = Stock.objects.get(id=header.get('stock_id'), tenant=tenant)
                        except Stock.DoesNotExist:
                            return _json_error('المخزن المحدد غير موجود')

                    # المندوب
                    if 'agent_id' in header:
                        agent_raw = header.get('agent_id')
                        if agent_raw:
                            from apps.agents.models import Agent as _Agent
                            try:
                                invoice.agent = _Agent.objects.get(id=agent_raw, tenant=tenant, is_active=True)
                            except _Agent.DoesNotExist:
                                invoice.agent = None
                        else:
                            invoice.agent = None

                    invoice.recalculate_totals()
                    invoice.save()
                    inv = invoice

            if action == 'confirm':
                confirm_sale_invoice(inv, request.user)

    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        return _json_error(f'حدث خطأ غير متوقع: {str(e)}')

    return JsonResponse({'success': True, 'redirect': f'/sales/{inv.id}/'})


# ─────────────────────────────────────────────
#   INVOICE DETAIL
# ─────────────────────────────────────────────

@login_required
@require_permission('view_sales')
def invoice_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(
        SaleInvoice.objects.select_related('customer', 'stock', 'agent', 'confirmed_by', 'cancelled_by'),
        pk=pk, tenant=tenant,
    )
    lines = list(invoice.lines.select_related('item').prefetch_related('item__item_units').all())
    for ln in lines:
        iu_list = list(ln.item.item_units.order_by('factor'))
        matched = next((u for u in iu_list if abs(float(u.factor) - float(ln.unit_factor or 1)) < 0.0001), iu_list[0] if iu_list else None)
        ln.unit_display = matched.name if matched else ln.item.base_unit_name
    payments = invoice.payments.all()
    returns = invoice.sale_returns.filter(status='confirmed').select_related('confirmed_by')

    from apps.core.models import Settings as TenantSettings
    settings_obj, _ = TenantSettings.objects.get_or_create(tenant=tenant)

    context = {
        'invoice': invoice,
        'lines': lines,
        'payments': payments,
        'returns': returns,
        'tenant': tenant,
        'settings_obj': settings_obj,
        'can_confirm': invoice.status == 'draft',
        'can_delete_draft': invoice.status == 'draft',
        'can_edit': invoice.status in ('draft', 'confirmed'),
        'can_cancel': invoice.status in ('confirmed', 'pending_delivery'),
        'can_deliver': invoice.status == 'pending_delivery',
        'can_return': invoice.status in ('confirmed', 'partially_returned'),
        'can_pay': (
            invoice.status in ('confirmed', 'partially_returned')
            and invoice.payment_method in ('credit', 'mixed')
            and invoice.remaining_amount > 0
        ),
    }

    return render(request, 'sales/invoice_detail.html', context)


# ─────────────────────────────────────────────
#   INVOICE ACTIONS (AJAX)
# ─────────────────────────────────────────────

@login_required
@require_permission('delete_sales')
@require_POST
def invoice_delete_draft_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)

    if invoice.status != 'draft':
        return _json_error('يمكن حذف الفاتورة إذا كانت مسودة فقط')

    if invoice.payments.exists():
        return _json_error('لا يمكن حذف المسودة لوجود دفعات مرتبطة بها')

    if invoice.sale_returns.exists():
        return _json_error('لا يمكن حذف المسودة لوجود مرتجعات مرتبطة بها')

    inv_num = invoice.invoice_number
    with transaction.atomic():
        invoice.delete()

    log_activity(request, 'حذف مسودة فاتورة مبيعات', inv_num, 'delete')
    return _json_ok(msg='تم حذف مسودة الفاتورة')


@login_required
@require_permission('change_sales')
@require_POST
def invoice_confirm_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)
    try:
        confirm_sale_invoice(invoice, request.user)
        cust = invoice.customer.name if invoice.customer else 'زبون عابر'
        log_activity(request, 'تأكيد فاتورة مبيعات', f'{invoice.invoice_number} — {cust}', 'create')
        return _json_ok(msg='تم تأكيد الفاتورة بنجاح')
    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        return _json_error(f'حدث خطأ: {str(e)}')


@login_required
@require_permission('delete_sales')
@require_POST
def invoice_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)
    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        body = {}
    reason = body.get('reason', '')

    try:
        cancel_sale_invoice(invoice, request.user, reason)
        cust = invoice.customer.name if invoice.customer else 'زبون عابر'
        log_activity(request, 'إلغاء فاتورة مبيعات', f'{invoice.invoice_number} — {cust}', 'delete')
        return _json_ok(msg='تم إلغاء الفاتورة')
    except ValueError as e:
        return _json_error(str(e))


@login_required
@require_permission('change_sales')
@require_POST
def invoice_deliver_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)
    try:
        deliver_sale_invoice(invoice, request.user)
        cust = invoice.customer.name if invoice.customer else 'زبون عابر'
        log_activity(request, 'تسليم فاتورة مبيعات', f'{invoice.invoice_number} — {cust}', 'other')
        return _json_ok(msg='تم تسليم الفاتورة بنجاح')
    except ValueError as e:
        return _json_error(str(e))


@login_required
@require_permission('change_sales')
@require_POST
def record_payment_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)
    try:
        body = json.loads(request.body)
        amount = Decimal(str(body['amount']))
        method = body.get('method', 'cash')
        date = body.get('date') or timezone.localdate().isoformat()
        reference = body.get('reference', '')
        notes = body.get('notes', '')
    except (KeyError, InvalidOperation, json.JSONDecodeError) as e:
        return _json_error(f'بيانات الدفعة غير صالحة: {e}')

    try:
        record_customer_payment(invoice, amount, method, date, reference, notes, request.user)
        return _json_ok(
            data={
                'paid_amount': str(invoice.paid_amount),
                'remaining': str(invoice.remaining_amount),
            },
            msg='تم تسجيل الدفعة بنجاح',
        )
    except ValueError as e:
        return _json_error(str(e))


# ─────────────────────────────────────────────
#   SALE RETURNS
# ─────────────────────────────────────────────

@login_required
@require_permission('view_sales_returns')
def return_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = SaleReturn.objects.for_tenant(tenant)
    total = qs.count()
    confirmed = qs.filter(status='confirmed').count()
    draft = qs.filter(status='draft').count()
    cancelled = qs.filter(status='cancelled').count()
    total_value = (
        qs.filter(status='confirmed')
        .aggregate(s=Sum('total_returned'))['s'] or Decimal('0')
    )

    context = {
        'stats': {
            'total': total,
            'confirmed': confirmed,
            'draft': draft,
            'cancelled': cancelled,
            'total_value': total_value,
        }
    }
    return render(request, 'sales/return_list.html', context)


@login_required
@require_permission('view_sales_returns')
def return_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '')

    qs = SaleReturn.objects.for_tenant(tenant).select_related(
        'original_invoice', 'original_invoice__customer'
    )
    total = qs.count()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if search_value:
        qs = qs.filter(
            Q(return_number__icontains=search_value)
            | Q(original_invoice__invoice_number__icontains=search_value)
            | Q(original_invoice__customer__name__icontains=search_value)
        )

    filtered = qs.count()
    qs = qs.order_by('-return_date', '-created_at')
    page_qs = qs[start: start + length]

    STATUS_LABELS = {
        'draft': ('مسودة', 'secondary'),
        'confirmed': ('مؤكد', 'success'),
        'cancelled': ('ملغي', 'danger'),
    }

    data = []
    for r in page_qs:
        label, color = STATUS_LABELS.get(r.status, (r.status, 'secondary'))
        data.append({
            'id': r.id,
            'return_number': r.return_number,
            'return_date': r.return_date.strftime('%Y-%m-%d'),
            'invoice_number': r.original_invoice.invoice_number,
            'customer': r.original_invoice.customer.name if r.original_invoice.customer else '—',
            'total_returned': str(r.total_returned),
            'refund_method': r.get_refund_method_display(),
            'status': r.status,
            'status_label': label,
            'status_color': color,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered,
        'data': data,
    }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_sales_returns')
def return_lines_api(request, return_pk):
    """API: جلب بنود المرتجع (للمودال)"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    sale_return = get_object_or_404(SaleReturn, pk=return_pk, tenant=tenant)
    lines = sale_return.lines.select_related('item').all()

    data = []
    for line in lines:
        data.append({
            'item_name': line.item.name,
            'returned_quantity': str(line.returned_quantity),
            'unit_price': str(line.unit_price),
            'line_total': str(line.line_total),
        })

    return JsonResponse({'success': True, 'lines': data}, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_sales_returns')
def return_create(request, invoice_pk):
    """إنشاء مرتجع لفاتورة محددة."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(
        SaleInvoice, pk=invoice_pk, tenant=tenant,
        status__in=['confirmed', 'partially_returned']
    )
    lines = invoice.lines.select_related('item').all()
    returnable_lines = [l for l in lines if l.returnable_quantity > 0]

    if request.method == 'POST':
        result = _process_return_post(request, tenant, invoice)
        if isinstance(result, SaleReturn):
            customer = result.customer.name if result.customer else '—'
            log_activity(request, 'إنشاء مرتجع مبيعات',
                         f"المرتجع: {result.return_number}\nالفاتورة الأصلية: {invoice.invoice_number}\nالعميل: {customer}\nالمبلغ المسترد: {result.total_returned}", 'create')
            return redirect('sales:return_detail', pk=result.id)
        if isinstance(result, JsonResponse):
            return result
        return redirect('sales:return_create', invoice_pk=invoice_pk)

    context = {
        'invoice': invoice,
        'returnable_lines': returnable_lines,
        'today': timezone.localdate().isoformat(),
    }
    return render(request, 'sales/return_form.html', context)


def _process_return_post(request, tenant, invoice):
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_error('بيانات غير صالحة')

    header = body.get('header', {})
    lines_raw = body.get('lines', [])
    action = body.get('action', 'save_draft')

    if not lines_raw:
        return _json_error('يجب تحديد بنود الإرجاع')

    try:
        with transaction.atomic():
            sale_return = SaleReturn(
                tenant=tenant,
                return_date=header.get('return_date') or timezone.localdate(),
                original_invoice=invoice,
                refund_method=header.get('refund_method', 'cash'),
                reason=header.get('reason', ''),
                notes=header.get('notes', ''),
                created_by=request.user,
            )
            sale_return.save()

            for ld in lines_raw:
                inv_line = get_object_or_404(
                    SaleInvoiceLine, pk=ld['invoice_line_id'], invoice=invoice
                )
                qty = Decimal(str(ld['returned_quantity']))
                if qty <= 0:
                    continue
                SaleReturnLine.objects.create(
                    tenant=tenant,
                    sale_return=sale_return,
                    invoice_line=inv_line,
                    item=inv_line.item,
                    returned_quantity=qty,
                    unit_price=inv_line.unit_price,
                    created_by=request.user,
                )

            sale_return.total_returned = sum(
                Decimal(str(ld.get('returned_quantity', 0))) * inv_line.unit_price
                for ld in lines_raw
                for inv_line in [SaleInvoiceLine.objects.get(pk=ld['invoice_line_id'])]
                if Decimal(str(ld.get('returned_quantity', 0))) > 0
            )
            sale_return.save(update_fields=['total_returned', 'updated_at'])

            if action == 'confirm':
                confirm_sale_return(sale_return, request.user)

    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        return _json_error(f'حدث خطأ: {str(e)}')

    return JsonResponse({'success': True, 'redirect': f'/sales/returns/{sale_return.id}/'})


@login_required
@require_permission('view_sales_returns')
def return_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    sale_return = get_object_or_404(
        SaleReturn.objects.select_related('original_invoice', 'original_invoice__customer'),
        pk=pk, tenant=tenant
    )
    return_lines = sale_return.lines.select_related('item', 'invoice_line')

    context = {
        'sale_return': sale_return,
        'return_lines': return_lines,
        'can_confirm': sale_return.status == 'draft',
        'can_cancel': sale_return.status == 'confirmed',
    }
    return render(request, 'sales/return_detail.html', context)


@login_required
@require_permission('add_sales_returns')
@require_POST
def return_confirm_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    sale_return = get_object_or_404(SaleReturn, pk=pk, tenant=tenant)
    try:
        confirm_sale_return(sale_return, request.user)
        log_activity(request, 'تأكيد مرتجع مبيعات', f'{sale_return.return_number}', 'create')
        return _json_ok(msg='تم تأكيد المرتجع بنجاح')
    except ValueError as e:
        return _json_error(str(e))


@login_required
@require_permission('add_sales_returns')
@require_POST
def return_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    sale_return = get_object_or_404(SaleReturn, pk=pk, tenant=tenant)
    try:
        cancel_sale_return(sale_return, request.user)
        log_activity(request, 'إلغاء مرتجع مبيعات', f'{sale_return.return_number}', 'delete')
        return _json_ok(msg='تم إلغاء المرتجع')
    except ValueError as e:
        return _json_error(str(e))


# ─────────────────────────────────────────────
#   AJAX HELPERS
# ─────────────────────────────────────────────

@login_required
@require_permission('view_items')
def item_info_api(request):
    """
    يُعيد بيانات المنتج لنموذج إنشاء الفاتورة:
      سعر البيع، سعر التكلفة، نسبة الضريبة، الكمية المتاحة في المخزن المحدد.
    """
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    item_id = request.GET.get('item_id')
    stock_id = request.GET.get('stock_id')

    if not item_id:
        return _json_error('item_id مطلوب')

    try:
        item = Item.objects.get(id=item_id, tenant=tenant, is_active=True)
    except Item.DoesNotExist:
        return _json_error('المنتج غير موجود', status=404)

    is_service = item.item_type == 'service'
    available_qty = None if is_service else 0
    if stock_id and not is_service:
        try:
            sq = StockQuantity.objects.get(tenant=tenant, stock_id=stock_id, item=item)
            available_qty = float(sq.available_quantity)
        except StockQuantity.DoesNotExist:
            available_qty = 0

    iu_qs = list(item.item_units.order_by('factor'))
    units = [{'id': u.id, 'name': u.name, 'factor': str(u.factor)} for u in iu_qs]
    base_unit_id   = iu_qs[0].id   if iu_qs else None
    base_unit_name = iu_qs[0].name if iu_qs else ''

    return JsonResponse({
        'success': True,
        'item': {
            'id': item.id,
            'name': item.name,
            'sku': item.sku,
            'selling_price': str(item.selling_price),
            'min_selling_price': str(item.min_selling_price),
            'cost_price': str(item.cost_price),
            'tax_rate': str(item.tax_rate),
            'track_batch': item.track_batch,
            'track_serial': item.track_serial,
            'track_expiry': item.track_expiry,
            'item_type': item.item_type,
            'is_service': is_service,
            'available_qty': available_qty,
            'unit_id': base_unit_id,
            'unit_name': base_unit_name,
            'unit_factor': '1',
            'units': units,
        }
    })


@login_required
@require_permission('view_customers')
def customer_info_api(request):
    """يُعيد رصيد العميل وحد الائتمان."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    customer_id = request.GET.get('customer_id')
    if not customer_id:
        return _json_error('customer_id مطلوب')

    try:
        customer = Customer.objects.get(id=customer_id, tenant=tenant)
    except Customer.DoesNotExist:
        return _json_error('العميل غير موجود', status=404)

    # حساب الرصيد الجاري من CustomerLedger
    total = (
        CustomerLedger.objects
        .filter(tenant=tenant, customer=customer)
        .aggregate(s=Sum('amount'))['s'] or Decimal('0')
    )
    # إضافة الرصيد الافتتاحي من نموذج العميل
    current_balance = total + (customer.opening_balance or Decimal('0'))

    return JsonResponse({
        'success': True,
        'customer': {
            'id': customer.id,
            'name': customer.name,
            'phone': customer.phone,
            'credit_limit': str(customer.credit_limit),
            'current_balance': str(current_balance),
            'opening_balance': str(customer.opening_balance),
        }
    })


@login_required
@require_permission('view_items')
def stock_items_api(request):
    """يُعيد كميات المنتجات المتاحة في مخزن معين (لتلميح الكميات)."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    stock_id = request.GET.get('stock_id')
    search = request.GET.get('q', '').strip()

    if not stock_id:
        return _json_error('stock_id مطلوب')

    qs = (
        Item.objects
        .filter(tenant=tenant, is_active=True, is_sellable=True)
        .select_related('category', 'unit')
    )
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search) | Q(barcode__icontains=search))

    # Important for MySQL compatibility:
    # avoid using a sliced queryset directly inside __in (LIMIT in subquery).
    item_ids = list(qs.values_list('id', flat=True)[:50])
    if not item_ids:
        return JsonResponse({'success': True, 'items': []}, json_dumps_params={'ensure_ascii': False})

    items = list(
        Item.objects
        .filter(id__in=item_ids)
        .select_related('category', 'unit')
    )
    items_map = {it.id: it for it in items}
    ordered_items = [items_map[iid] for iid in item_ids if iid in items_map]

    sq_map = {
        sq.item_id: sq.available_quantity
        for sq in StockQuantity.objects.filter(
            tenant=tenant, stock_id=stock_id,
            item_id__in=item_ids
        )
    }

    data = []
    for item in ordered_items:
        is_service = item.item_type == 'service'
        data.append({
            'id': item.id,
            'name': item.name,
            'sku': item.sku,
            'selling_price': str(item.selling_price),
            'cost_price': str(item.cost_price),
            'tax_rate': str(item.tax_rate),
            'item_type': item.item_type,
            'is_service': is_service,
            'available_qty': None if is_service else float(sq_map.get(item.id, 0)),
        })

    return JsonResponse({'success': True, 'items': data}, json_dumps_params={'ensure_ascii': False})


# ═══════════════════════════════════════════════════════════
#   SALE QUOTE VIEWS  — عروض الأسعار
# ═══════════════════════════════════════════════════════════

@login_required
@require_permission('view_quotes')
def quote_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = SaleQuote.objects.for_tenant(tenant)
    customers = Customer.objects.for_tenant(tenant).filter(is_active=True).values('id', 'name')
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True).values('id', 'name')

    stats = {
        'total':     qs.count(),
        'draft':     qs.filter(status='draft').count(),
        'sent':      qs.filter(status='sent').count(),
        'accepted':  qs.filter(status='accepted').count(),
        'converted': qs.filter(status='converted').count(),
        'rejected':  qs.filter(status='rejected').count(),
        'cancelled': qs.filter(status='cancelled').count(),
        'grand_total_sum': (
            qs.exclude(status__in=['cancelled'])
            .aggregate(s=Sum('grand_total'))['s'] or Decimal('0')
        ),
    }

    return render(request, 'sales/quote_list.html', {
        'stats': stats,
        'customers': list(customers),
        'stocks': list(stocks),
    })


@login_required
@require_permission('view_quotes')
def quote_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '')
    customer_filter = request.GET.get('customer_id', '')

    qs = SaleQuote.objects.for_tenant(tenant).select_related('customer', 'stock')
    total = qs.count()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if customer_filter:
        qs = qs.filter(customer_id=customer_filter)

    if search_value:
        qs = qs.filter(
            Q(quote_number__icontains=search_value) |
            Q(customer__name__icontains=search_value) |
            Q(reference_number__icontains=search_value)
        )

    filtered = qs.count()
    order_col_idx = int(request.GET.get('order[0][column]', 0))
    order_dir = request.GET.get('order[0][dir]', 'desc')
    col_map = {0: 'quote_number', 1: 'quote_date', 2: 'customer__name', 3: 'grand_total', 4: 'status'}
    order_field = col_map.get(order_col_idx, 'quote_date')
    if order_dir == 'desc':
        order_field = f'-{order_field}'
    qs = qs.order_by(order_field)[start:start + length]

    STATUS_COLOR = {
        'draft': 'muted', 'sent': 'primary', 'accepted': 'success',
        'rejected': 'danger', 'converted': 'info', 'expired': 'warning', 'cancelled': 'danger',
    }
    STATUS_LABEL = dict(SaleQuote.STATUS_CHOICES)

    rows = []
    for q in qs:
        rows.append({
            'id': q.id,
            'quote_number': q.quote_number,
            'quote_date': str(q.quote_date),
            'expiry_date': str(q.expiry_date) if q.expiry_date else None,
            'customer': q.customer.name if q.customer else 'زبون عابر',
            'customer_id': q.customer_id,
            'stock': q.stock.name,
            'grand_total': str(q.grand_total),
            'status': q.status,
            'status_label': STATUS_LABEL.get(q.status, q.status),
            'status_color': STATUS_COLOR.get(q.status, 'muted'),
            'converted_invoice_id': q.converted_invoice_id,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered,
        'data': rows,
    }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_quotes')
def quote_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    customers = Customer.objects.for_tenant(tenant).filter(is_active=True)
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, TypeError):
            body = {}

        lines_data = body.pop('lines', [])
        if not lines_data:
            return _json_error('أضف بنداً واحداً على الأقل')

        try:
            quote = build_quote_from_post(
                tenant=tenant,
                user=request.user,
                post_data=body,
                lines_data=lines_data,
            )
        except Exception as e:
            return _json_error(str(e))

        customer = quote.customer.name if quote.customer else 'بدون عميل'
        log_activity(request, 'إنشاء عرض سعر',
                     f"عرض السعر: {quote.quote_number}\nالعميل: {customer}\nالإجمالي: {quote.grand_total}", 'create')
        return _json_ok({'redirect': f'/sales/quotes/{quote.pk}/'}, 'تم حفظ عرض السعر')

    return render(request, 'sales/quote_form.html', {
        'customers': customers,
        'stocks': stocks,
        'quote': None,
        'today': timezone.localdate().isoformat(),
        'lines_json': '[]',
    })


@login_required
@require_permission('change_quotes')
def quote_edit(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    if quote.status not in ('draft', 'sent', 'accepted'):
        return redirect('sales:quote_detail', pk=pk)

    customers = Customer.objects.for_tenant(tenant).filter(is_active=True)
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, TypeError):
            body = {}

        lines_data = body.pop('lines', [])
        if not lines_data:
            return _json_error('أضف بنداً واحداً على الأقل')

        try:
            quote = build_quote_from_post(
                tenant=tenant,
                user=request.user,
                post_data=body,
                lines_data=lines_data,
                instance=quote,
            )
        except Exception as e:
            return _json_error(str(e))

        return _json_ok({'redirect': f'/sales/quotes/{quote.pk}/'}, 'تم تحديث عرض السعر')

    lines_json = []
    for ql in quote.quote_lines.select_related('item').prefetch_related('item__item_units').all():
        iu_list = list(ql.item.item_units.order_by('factor'))
        units = [{'id': u.id, 'name': u.name, 'factor': str(u.factor)} for u in iu_list]
        lines_json.append({
            'item_id': ql.item_id,
            'item_name': ql.item.name,
            'item_sku': ql.item.sku or '',
            'quantity': str(ql.quantity),
            'unit_price': str(ql.unit_price),
            'discount_percent': str(ql.discount_percent),
            'tax_rate': str(ql.tax_rate),
            'line_total': str(ql.line_total),
            'units': units,
            'unit_id': iu_list[0].id if iu_list else '',
            'unit_name': iu_list[0].name if iu_list else '',
            'unit_factor': '1',
        })

    return render(request, 'sales/quote_form.html', {
        'customers': customers,
        'stocks': stocks,
        'quote': quote,
        'lines_json': json.dumps(lines_json),
        'today': timezone.localdate().isoformat(),
    })


@login_required
@require_permission('view_quotes')
def quote_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    quote = get_object_or_404(
        SaleQuote.objects.select_related('customer', 'stock', 'converted_invoice', 'converted_by'),
        pk=pk, tenant=tenant
    )
    lines = list(quote.quote_lines.select_related('item').prefetch_related('item__item_units').all())
    for ln in lines:
        iu_list = list(ln.item.item_units.order_by('factor'))
        ln.unit_display = iu_list[0].name if iu_list else ln.item.base_unit_name

    from apps.core.models import Settings as TenantSettings
    settings_obj, _ = TenantSettings.objects.get_or_create(tenant=tenant)

    return render(request, 'sales/quote_detail.html', {
        'quote': quote,
        'lines': lines,
        'settings_obj': settings_obj,
        'tenant': tenant,
        'can_edit': quote.status == 'draft',
        'can_send': quote.can_send,
        'can_accept': quote.status == 'sent',
        'can_reject': quote.status in ('sent', 'accepted'),
        'can_convert': quote.can_convert,
        'can_cancel': quote.can_cancel,
    })


@login_required
@require_permission('change_quotes')
@require_POST
def quote_send_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        mark_quote_sent(quote, request.user)
        qcust = quote.customer.name if quote.customer else 'بدون عميل'
        log_activity(request, 'إرسال عرض سعر', f'{quote.quote_number} — {qcust}', 'other')
        return _json_ok(msg='تم تغيير حالة العرض إلى مُرسَل')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_permission('change_quotes')
@require_POST
def quote_accept_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        mark_quote_accepted(quote, request.user)
        qcust = quote.customer.name if quote.customer else 'بدون عميل'
        log_activity(request, 'قبول عرض سعر', f'{quote.quote_number} — {qcust}', 'other')
        return _json_ok(msg='تم قبول العرض')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_permission('change_quotes')
@require_POST
def quote_reject_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        mark_quote_rejected(quote, request.user)
        qcust = quote.customer.name if quote.customer else 'بدون عميل'
        log_activity(request, 'رفض عرض سعر', f'{quote.quote_number} — {qcust}', 'delete')
        return _json_ok(msg='تم رفض العرض')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_permission('change_quotes')
@require_POST
def quote_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        cancel_sale_quote(quote, request.user)
        qcust = quote.customer.name if quote.customer else 'بدون عميل'
        log_activity(request, 'إلغاء عرض سعر', f'{quote.quote_number} — {qcust}', 'delete')
        return _json_ok(msg='تم إلغاء عرض السعر')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_permission('delete_quotes')
@require_POST
def quote_delete_draft_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    if quote.status != 'draft':
        return _json_error('لا يمكن حذف إلا المسودات')
    q_num = quote.quote_number
    quote.delete()
    log_activity(request, 'حذف مسودة عرض سعر', q_num, 'delete')
    return _json_ok(msg='تم حذف مسودة العرض')


@login_required
@require_permission('change_quotes')
@require_POST
def quote_convert_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        body = json.loads(request.body) if request.body else {}
    except Exception:
        body = {}

    payment_method = body.get('payment_method', 'cash')
    cash_amount = body.get('cash_amount')
    bank_amount = body.get('bank_amount')
    bank_reference = body.get('bank_reference', '')

    try:
        invoice = convert_quote_to_invoice(
            quote=quote,
            user=request.user,
            payment_method=payment_method,
            cash_amount=cash_amount,
            bank_amount=bank_amount,
            bank_reference=bank_reference,
        )
        log_activity(request, 'تحويل عرض سعر لفاتورة', f'{quote.quote_number} ← {invoice.invoice_number}', 'create')
        return _json_ok(
            {'invoice_url': f'/sales/{invoice.pk}/'},
            msg=f'تم إنشاء الفاتورة {invoice.invoice_number} من عرض السعر'
        )
    except Exception as e:
        return _json_error(str(e))


# ─────────────────────────────────────────────
#   SALES REPORTS
# ─────────────────────────────────────────────


@login_required
@require_permission('view_sales_summary_report')
def sales_summary_report(request):
    """تقرير ملخص المبيعات"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    
    # Get date range from request
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))

    if not start_date:
        start_date = (timezone.localdate() - timedelta(days=30))
    if not end_date:
        end_date = timezone.localdate()
    
    # Generate report
    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_summary_report()

    # Chart data: daily trend for the selected period
    import json
    trend = generator.get_by_date_report(group_by='day')
    chart_labels = json.dumps([d['label'] for d in trend['data']], ensure_ascii=False)
    chart_amounts = json.dumps([float(str(d['total_amount']).replace(',', '')) for d in trend['data']])

    return render(request, 'sales/reports/summary.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'sales_reports',
        'report_type': 'summary',
        'chart_labels': chart_labels,
        'chart_amounts': chart_amounts,
    })


@login_required
@require_permission('view_sales_summary_report')
def sales_summary_report_export(request):
    """تصدير تقرير ملخص المبيعات"""
    import csv
    from django.http import HttpResponse
    
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    
    # Get date range
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    
    if not start_date:
        start_date = (timezone.localdate() - timedelta(days=30))
    if not end_date:
        end_date = timezone.localdate()
    
    # Generate report
    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_summary_report()
    
    # Create CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_summary_{end_date}.csv"'
    response.write('\ufeff')
    
    writer = csv.writer(response)
    writer.writerow(['تقرير ملخص المبيعات'])
    writer.writerow([])
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])
    
    writer.writerow(['البيان', 'القيمة'])
    writer.writerow(['عدد الفواتير', report_data['summary']['invoice_count']])
    writer.writerow(['إجمالي الكمية', report_data['summary']['total_quantity']])
    writer.writerow(['إجمالي المبيعات', report_data['summary']['total_amount']])
    writer.writerow(['إجمالي الضريبة', report_data['summary']['total_tax']])
    writer.writerow(['متوسط الفاتورة', report_data['summary']['avg_invoice_amount']])
    
    return response


@login_required
@require_permission('view_sales_by_customer_report')
def sales_by_customer_report(request):
    """تقرير المبيعات حسب العميل"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    
    # Get date range
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    
    if not start_date:
        start_date = (timezone.localdate() - timedelta(days=30))
    if not end_date:
        end_date = timezone.localdate()
    
    # Generate report
    # Optional customer filter
    customer_id = request.GET.get('customer_id')

    # Generate report
    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_customer_report(customer_id=customer_id)

    # customers list for filter
    from apps.customers.models import Customer
    customers = Customer.objects.filter(tenant=tenant).order_by('name')
    selected_customer = None
    if customer_id:
        try:
            selected_customer = Customer.objects.get(tenant=tenant, id=customer_id)
        except Customer.DoesNotExist:
            selected_customer = None

    return render(request, 'sales/reports/by_customer.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'customers': customers,
        'selected_customer_id': customer_id,
        'selected_customer': selected_customer,
        'section': 'sales_reports',
        'report_type': 'by_customer',
    })


@login_required
@require_permission('view_sales_by_customer_report')
def sales_by_customer_report_export(request):
    """تصدير تقرير المبيعات حسب العميل"""
    import csv
    from django.http import HttpResponse
    
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    
    # Get date range
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    
    if not start_date:
        start_date = (timezone.localdate() - timedelta(days=30))
    if not end_date:
        end_date = timezone.localdate()
    
    # Generate report
    # Optional customer filter
    customer_id = request.GET.get('customer_id')

    # Generate report
    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_customer_report(customer_id=customer_id)
    # fetch selected customer for header if available
    selected_customer = None
    if customer_id:
        from apps.customers.models import Customer
        try:
            selected_customer = Customer.objects.get(tenant=tenant, id=customer_id)
        except Customer.DoesNotExist:
            selected_customer = None

    # Create CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_by_customer_{end_date}.csv"'
    response.write('\ufeff')
    
    writer = csv.writer(response)
    writer.writerow(['تقرير المبيعات حسب العميل'])
    writer.writerow([])
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    # If customer filter provided, export invoice-level details
    if customer_id and report_data.get('customer'):
        cust = selected_customer
        if cust:
            writer.writerow([f"العميل: {cust.name}"])
            if cust.phone:
                writer.writerow([f"هاتف: {cust.phone}"])
            if cust.email:
                writer.writerow([f"بريد إلكتروني: {cust.email}"])
        else:
            writer.writerow([f"العميل: {report_data['customer']['name']}"])
        writer.writerow([])
        writer.writerow(['رقم الفاتورة', 'تاريخ الفاتورة', 'عدد الأصناف', 'قيمة الفاتورة'])
        for row in report_data['data']:
            writer.writerow([
                row.get('invoice_number'),
                row.get('invoice_date'),
                row.get('item_count') or row.get('total_quantity'),
                row.get('grand_total'),
            ])
    else:
        writer.writerow(['اسم العميل', 'عدد الفواتير', 'إجمالي الكمية', 'إجمالي المبيعات', 'متوسط الفاتورة'])
        for item in report_data['data']:
            writer.writerow([
                item['customer_name'],
                item['invoice_count'],
                item['total_quantity'],
                item['total_amount'],
                item['avg_invoice_amount'],
            ])
    
    return response


@login_required
@require_permission('view_sales_by_item_report')
def sales_by_item_report(request):
    """تقرير المبيعات حسب المنتج"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_item_report(item_id=item_id)

    items = Item.objects.filter(
        tenant=tenant,
        sale_lines__invoice__status='confirmed',
    ).distinct().order_by('name')

    return render(request, 'sales/reports/by_item.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'items': items,
        'selected_item_id': item_id,
        'section': 'sales_reports',
        'report_type': 'by_item',
    })


@login_required
@require_permission('view_sales_by_item_report')
def sales_by_item_report_export(request):
    """تصدير تقرير المبيعات حسب المنتج"""
    import csv
    from django.http import HttpResponse

    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_item_report(item_id=item_id)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_by_item_{end_date}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    if report_data.get('item'):
        writer.writerow([f'المنتج: {report_data["item"]["name"]}'])
        writer.writerow([])
        writer.writerow(['رقم الفاتورة', 'تاريخ الفاتورة', 'العميل', 'الكمية', 'سعر الوحدة', 'الإجمالي'])
        for row in report_data['data']:
            writer.writerow([row['invoice_number'], row['invoice_date'], row['customer_name'], row['quantity'], row['unit_price'], row['line_total']])
    else:
        writer.writerow(['اسم المنتج', 'الوحدة', 'الكمية المباعة', 'إجمالي المبيعات', 'متوسط السعر'])
        for item in report_data['data']:
            writer.writerow([item['item_name'], item['unit'], item['quantity_sold'], item['total_amount'], item['avg_unit_price']])

    return response


@login_required
@require_permission('view_sales_by_date_report')
def sales_by_date_report(request):
    """تقرير المبيعات حسب التاريخ"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    
    # Get date range
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    group_by = request.GET.get('group_by', 'day')
    
    if not start_date:
        start_date = (timezone.localdate() - timedelta(days=30))
    if not end_date:
        end_date = timezone.localdate()
    
    # Generate report
    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_date_report(group_by)
    
    return render(request, 'sales/reports/by_date.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'group_by': group_by,
        'section': 'sales_reports',
        'report_type': 'by_date',
    })


@login_required
@require_permission('view_sales_by_date_report')
def sales_by_date_report_export(request):
    """تصدير تقرير المبيعات حسب التاريخ"""
    import csv
    from django.http import HttpResponse
    
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    
    # Get date range
    start_date = _parse_date(request.GET.get('start_date'))
    end_date = _parse_date(request.GET.get('end_date'))
    group_by = request.GET.get('group_by', 'day')
    
    if not start_date:
        start_date = (timezone.localdate() - timedelta(days=30))
    if not end_date:
        end_date = timezone.localdate()
    
    # Generate report
    generator = SalesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_date_report(group_by)
    
    # Create CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_by_date_{end_date}.csv"'
    response.write('\ufeff')
    
    writer = csv.writer(response)
    writer.writerow(['تقرير المبيعات حسب التاريخ'])
    writer.writerow([])
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([f'التجميع: {group_by}'])
    writer.writerow([])
    
    writer.writerow(['التاريخ', 'عدد الفواتير', 'إجمالي الكمية', 'إجمالي المبيعات'])
    for item in report_data['data']:
        writer.writerow([
            item['label'],
            item['invoice_count'],
            item['total_quantity'],
            item['total_amount'],
        ])
    
    return response


# ─────────────────────────────────────────────────────────────────
#   NEW REPORTS: Customer Statement / Balances / Payments / Returns
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_sales_customer_statement_report')
def sales_customer_statement(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    customer_id = request.GET.get('customer_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report = generator.get_customer_statement(customer_id) if customer_id else None
    customers = Customer.objects.filter(tenant=tenant).order_by('name')

    return render(request, 'sales/reports/customer_statement.html', {
        'report': report,
        'customers': customers,
        'selected_customer_id': customer_id,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_sales_customer_statement_report')
def sales_customer_statement_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    customer_id = request.GET.get('customer_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report = generator.get_customer_statement(customer_id) if customer_id else None

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="customer_statement_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)

    if report:
        writer.writerow([f'كشف حساب: {report["customer"].name}'])
        writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
        writer.writerow([])
        writer.writerow(['التاريخ', 'نوع القيد', 'المبلغ', 'المديونية التراكمية', 'ملاحظات'])
        for row in report['data']:
            writer.writerow([row['entry_date'], row['entry_type'], row['amount'], row['running_balance'], row['notes']])
    return response


@login_required
@require_permission('view_sales_customer_balances_report')
def sales_customer_balances(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    generator = SalesReportGenerator(tenant)
    report = generator.get_customer_balances()

    return render(request, 'sales/reports/customer_balances.html', {
        'report': report,
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_sales_customer_balances_report')
def sales_customer_balances_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = SalesReportGenerator(tenant).get_customer_balances()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="customer_balances.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الكود', 'اسم العميل', 'الهاتف', 'الحد الائتماني', 'المديونية'])
    for row in report['data']:
        writer.writerow([row['code'], row['name'], row['phone'], row['credit_limit'], row['balance']])
    return response


@login_required
@require_permission('view_sales_payments_report')
def sales_payments_report(request):
    from apps.customers.models import Customer
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    customer_id = request.GET.get('customer_id') or None

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report = generator.get_payments_report(customer_id=customer_id)
    customers = Customer.objects.filter(tenant=tenant, is_active=True).order_by('name')

    return render(request, 'sales/reports/payments.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'customers': customers,
        'selected_customer_id': customer_id or '',
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_sales_payments_report')
def sales_payments_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = SalesReportGenerator(tenant, start_date, end_date).get_payments_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_payments_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['تاريخ الدفع', 'رقم الفاتورة', 'العميل', 'طريقة الدفع', 'المبلغ', 'رقم مرجعي'])
    for row in report['data']:
        writer.writerow([row['payment_date'], row['invoice_number'], row['customer_name'], row['payment_method'], row['amount'], row['reference_number']])
    return response


@login_required
@require_permission('view_sales_returns_report')
def sales_returns_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report = generator.get_returns_report()

    return render(request, 'sales/reports/returns.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_sales_returns_report')
def sales_returns_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = SalesReportGenerator(tenant, start_date, end_date).get_returns_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_returns_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['تاريخ المرتجع', 'رقم المرتجع', 'رقم الفاتورة', 'العميل', 'اسم المنتج', 'الكمية المرتجعة', 'سعر الوحدة', 'إجمالي السطر', 'طريقة الاسترداد'])
    for row in report['data']:
        writer.writerow([row['return_date'], row['return_number'], row['invoice_number'], row['customer_name'], row['item_name'], row['returned_quantity'], row['unit_price'], row['line_total'], row['refund_method']])
    return response


# ─────────────────────────────────────────────────────────────────
#   BY USER
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_sales_by_user_report')
def sales_by_user_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    user_id = request.GET.get('user_id') or None

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report = generator.get_by_user_report(user_id=user_id)

    from django.contrib.auth import get_user_model
    from .models import SaleInvoice
    user_ids = SaleInvoice.objects.filter(tenant=tenant, status='confirmed').values_list('created_by', flat=True).distinct()
    users = get_user_model().objects.filter(pk__in=user_ids).order_by('first_name', 'last_name')

    return render(request, 'sales/reports/by_user.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'users': users,
        'selected_user_id': user_id,
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_sales_by_user_report')
def sales_by_user_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    user_id = request.GET.get('user_id') or None

    report = SalesReportGenerator(tenant, start_date, end_date).get_by_user_report(user_id=user_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_by_user_{end_date}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    if report.get('user'):
        writer.writerow([f'المستخدم: {report["user"]["name"]}'])
        writer.writerow([])
        writer.writerow(['رقم الفاتورة', 'تاريخ الفاتورة', 'العميل', 'الكمية الإجمالية', 'الإجمالي'])
        for row in report['data']:
            writer.writerow([row['invoice_number'], row['invoice_date'], row['customer_name'], row['total_quantity'], row['grand_total']])
    else:
        writer.writerow(['المستخدم', 'عدد الفواتير', 'إجمالي المبيعات', 'متوسط الفاتورة'])
        for row in report['data']:
            writer.writerow([row['user_name'], row['invoice_count'], row['total_amount'], row['avg_invoice_amount']])

    return response


# ─────────────────────────────────────────────────────────────────
#   INCOME STATEMENT (P&L)
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_income_statement_report')
def income_statement_report(request):
    from .reports import IncomeStatementGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = IncomeStatementGenerator(tenant, start_date, end_date).get_report()

    return render(request, 'sales/reports/income_statement.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_income_statement_report')
def income_statement_report_export(request):
    import csv
    from django.http import HttpResponse
    from .reports import IncomeStatementGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = IncomeStatementGenerator(tenant, start_date, end_date).get_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="income_statement_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['قائمة الدخل'])
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])
    writer.writerow(['البيان', 'المبلغ'])
    writer.writerow(['إجمالي المبيعات', report['revenue']['gross_revenue']])
    writer.writerow(['المرتجعات', report['revenue']['total_returns']])
    writer.writerow(['صافي المبيعات', report['revenue']['net_revenue']])
    writer.writerow(['تكلفة البضاعة المباعة', report['cost']['total_purchases']])
    writer.writerow(['مجمل الربح', report['cost']['gross_profit']])
    writer.writerow(['إجمالي المصروفات', report['expenses']['total_expenses']])
    writer.writerow(['صافي الربح', report['bottom_line']['net_profit']])
    return response


# ─────────────────────────────────────────────────────────────────
#   PROFIT MARGIN REPORT
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_sales_profit_margin_report')
def sales_profit_margin_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    generator = SalesReportGenerator(tenant, start_date, end_date)
    report = generator.get_profit_margin_report(item_id=item_id)

    items = Item.objects.filter(
        tenant=tenant,
        sale_lines__invoice__status='confirmed',
    ).distinct().order_by('name')

    return render(request, 'sales/reports/profit_margin.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'items': items,
        'selected_item_id': item_id,
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_sales_profit_margin_report')
def sales_profit_margin_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    report = SalesReportGenerator(tenant, start_date, end_date).get_profit_margin_report(item_id=item_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="profit_margin_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    if report.get('item'):
        writer.writerow([f'المنتج: {report["item"]["name"]}'])
        writer.writerow([])
        writer.writerow(['الفاتورة', 'التاريخ', 'العميل', 'الكمية', 'سعر البيع', 'تكلفة الوحدة', 'الإيراد', 'التكلفة', 'الربح', 'الهامش%'])
        for row in report['data']:
            writer.writerow([row['invoice_number'], row['invoice_date'], row['customer_name'],
                             row['quantity'], row['unit_price'], row['unit_cost'],
                             row['revenue'], row['cogs'], row['profit'], row['margin']])
    else:
        writer.writerow(['المنتج', 'الوحدة', 'الكمية', 'الإيراد', 'التكلفة', 'الربح الإجمالي', 'هامش الربح%'])
        for row in report['data']:
            writer.writerow([row['item_name'], row['unit'], row['total_qty'],
                             row['total_revenue'], row['total_cogs'], row['gross_profit'], row['gross_margin']])
    return response


# ─────────────────────────────────────────────────────────────────
#   BY PAYMENT METHOD REPORT
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_sales_by_payment_method_report')
def sales_by_payment_method_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = SalesReportGenerator(tenant, start_date, end_date).get_by_payment_method_report()

    return render(request, 'sales/reports/by_payment_method.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'sales_reports',
    })


@login_required
@require_permission('view_sales_by_payment_method_report')
def sales_by_payment_method_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = SalesReportGenerator(tenant, start_date, end_date).get_by_payment_method_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="sales_by_payment_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])
    writer.writerow(['طريقة الدفع', 'عدد المدفوعات', 'الإجمالي المحصل'])
    for row in report['data']:
        writer.writerow([row['method_label'], row['payment_count'], row['total_amount']])
    writer.writerow([])
    writer.writerow(['إجمالي الفواتير', report['summary']['total_invoiced']])
    writer.writerow(['إجمالي المحصل', report['summary']['total_paid']])
    writer.writerow(['المتبقي', report['summary']['outstanding']])
    return response


# ═══════════════════════════════════════════════════════════════
#   نقطة البيع  (POS)
# ═══════════════════════════════════════════════════════════════

@login_required
@require_permission('add_sales')
def pos_view(request):
    """صفحة نقطة البيع الرئيسية."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    stocks = Stock.objects.filter(tenant=tenant, is_active=True).order_by('-is_default', 'name')
    default_stock = stocks.filter(is_default=True).first() or stocks.first()

    from apps.items.models import Category
    categories = Category.objects.filter(tenant=tenant, parent=None).order_by('display_order', 'name')
    customers = Customer.objects.filter(tenant=tenant, is_active=True).order_by('name').values('id', 'name')

    return render(request, 'sales/pos.html', {
        'stocks': stocks,
        'default_stock': default_stock,
        'categories': categories,
        'customers': list(customers),
        'section': 'pos',
    })


@login_required
@require_permission('view_items')
def pos_items_api(request):
    """يُعيد قائمة المنتجات مع الكميات للـ POS."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    stock_id = request.GET.get('stock_id')
    category_id = request.GET.get('category_id') or None
    item_type = request.GET.get('item_type') or None
    search = (request.GET.get('q') or '').strip()

    qs = Item.objects.filter(tenant=tenant, is_active=True).select_related('unit', 'category')

    if item_type:
        qs = qs.filter(item_type=item_type)

    if category_id:
        from apps.items.models import Category
        try:
            cat = Category.objects.get(id=category_id, tenant=tenant)
            child_ids = list(cat.children.values_list('id', flat=True))
            cat_ids = [cat.id] + child_ids
            qs = qs.filter(category_id__in=cat_ids)
        except Category.DoesNotExist:
            pass

    if search:
        qs = qs.filter(
            Q(name__icontains=search) |
            Q(sku__icontains=search) |
            Q(barcode__icontains=search)
        )

    qs = list(qs.order_by('name')[:80])
    item_ids = [item.id for item in qs]

    stock_qty_map = {}
    if stock_id and item_ids:
        sqqs = StockQuantity.objects.filter(
            tenant=tenant, stock_id=stock_id, item_id__in=item_ids
        ).values('item_id', 'quantity', 'reserved_quantity')
        for sq in sqqs:
            available = float((sq['quantity'] or 0) - (sq['reserved_quantity'] or 0))
            stock_qty_map[sq['item_id']] = {
                'quantity': sq['quantity'],
                'reserved_quantity': sq['reserved_quantity'],
                'available_quantity': available,
            }

    items = []
    for item in qs:
        sq = stock_qty_map.get(item.id, {})
        image_url = item.image.url if item.image else None
        items.append({
            'id': item.id,
            'name': item.name,
            'sku': item.sku,
            'barcode': item.barcode,
            'category': item.category.name if item.category else '',
            'selling_price': str(item.selling_price or 0),
            'cost_price': str(item.cost_price or 0),
            'tax_rate': str(item.tax_rate or 0),
            'unit': item.base_unit_name,
            'item_type': item.item_type,
            'is_service': item.item_type == 'service',
            'available_qty': float(sq.get('available_quantity', 0)) if sq else None,
            'image_url': image_url,
        })

    return JsonResponse({'items': items}, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_sales')
@require_POST
def pos_checkout_api(request):
    """
    ينشئ فاتورة مؤكدة مباشرةً من سلة POS ويُسجِّل الدفعة.
    يُعيد {success, invoice_number, invoice_id}.
    """
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    try:
        body = json.loads(request.body)
    except (ValueError, KeyError):
        return _json_error('بيانات غير صالحة')

    lines_raw = body.get('lines', [])
    if not lines_raw:
        return _json_error('لا توجد منتجات في السلة')

    stock_id = body.get('stock_id')
    if not stock_id:
        return _json_error('المخزن مطلوب')

    try:
        stock = Stock.objects.get(id=stock_id, tenant=tenant)
    except Stock.DoesNotExist:
        return _json_error('المخزن غير موجود')

    payment_method = body.get('payment_method', 'cash')
    cash_amount = Decimal(str(body.get('cash_amount', 0) or 0))
    bank_amount = Decimal(str(body.get('bank_amount', 0) or 0))
    bank_reference = body.get('bank_reference', '')
    customer_id = body.get('customer_id') or None
    discount_type = body.get('discount_type', 'fixed')
    discount_value = Decimal(str(body.get('discount_value', 0) or 0))
    notes = body.get('notes', '')

    lines_data = []
    for ln in lines_raw:
        lines_data.append({
            'item_id': ln['item_id'],
            'quantity': ln['quantity'],
            'unit_price': ln['unit_price'],
            'discount_percent': ln.get('discount_percent', 0),
            'tax_rate': ln.get('tax_rate', 0),
            'cost_price_snapshot': ln.get('cost_price', 0),
        })

    invoice_data = {
        'invoice_date': timezone.localdate(),
        'payment_method': payment_method,
        'invoice_discount_type': discount_type,
        'invoice_discount_value': discount_value,
        'cash_amount': cash_amount,
        'bank_amount': bank_amount,
        'bank_reference': bank_reference,
        'notes': notes,
        'customer_id': customer_id,
    }
    if customer_id:
        invoice_data['due_date'] = timezone.localdate()

    try:
        with transaction.atomic():
            invoice = build_invoice_from_post(tenant, stock, invoice_data, lines_data, request.user)
            confirm_sale_invoice(invoice, request.user)

            if payment_method == 'credit' and customer_id and cash_amount + bank_amount > 0:
                partial = cash_amount + bank_amount
                if partial > 0:
                    record_customer_payment(
                        invoice=invoice,
                        amount=partial,
                        method='cash' if cash_amount >= bank_amount else 'bank',
                        date=timezone.localdate(),
                        reference=bank_reference,
                        user=request.user,
                    )

    except Exception as exc:
        return _json_error(str(exc))

    customer_name = '—'
    if customer_id:
        try:
            from apps.customers.models import Customer as _Cust
            customer_name = _Cust.objects.get(pk=customer_id, tenant=tenant).name
        except Exception:
            pass
    log_activity(request, 'إتمام عملية بيع من نقطة البيع',
                 f"الفاتورة: {invoice.invoice_number}\nالعميل: {customer_name}\nالإجمالي: {invoice.grand_total}", 'create')

    return JsonResponse({
        'success': True,
        'invoice_number': invoice.invoice_number,
        'invoice_id': invoice.id,
        'grand_total': str(invoice.grand_total),
    }, json_dumps_params={'ensure_ascii': False})


# ─────────────────────────────────────────────
#   INVOICE EMAIL (AJAX)
# ─────────────────────────────────────────────

@login_required
@require_permission('send_invoice_email')
@require_POST
def invoice_send_email_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)

    if invoice.status not in ('confirmed', 'partially_returned', 'returned'):
        return _json_error('يمكن إرسال الفاتورة المؤكدة فقط')

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return _json_error('طلب غير صالح')

    recipient = (body.get('email') or '').strip()
    if not recipient or '@' not in recipient:
        return _json_error('البريد الإلكتروني غير صالح')

    from .email_service import send_invoice_email
    success, error = send_invoice_email(invoice, recipient, request=request)

    if not success:
        return _json_error(f'فشل الإرسال: {error}')

    log_activity(
        request, 'إرسال فاتورة بالبريد الإلكتروني',
        f'الفاتورة: {invoice.invoice_number} — إلى: {recipient}', 'other',
    )
    return _json_ok(msg=f'تم إرسال الفاتورة إلى {recipient}')
