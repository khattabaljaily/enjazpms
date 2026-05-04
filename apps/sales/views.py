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
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.customers.models import Customer
from apps.items.models import Item, ItemVariant
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
    edit_confirmed_invoice,
    record_customer_payment,
    build_quote_from_post,
    mark_quote_sent,
    mark_quote_accepted,
    mark_quote_rejected,
    cancel_sale_quote,
    convert_quote_to_invoice,
)


# ─────────────────────────────────────────────
#   HELPERS
# ─────────────────────────────────────────────

def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _json_error(msg, status=400):
    return JsonResponse({'success': False, 'message': msg}, status=status)


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data:
        payload.update(data)
    return JsonResponse(payload)


# ─────────────────────────────────────────────
#   INVOICE LIST
# ─────────────────────────────────────────────

@login_required
def invoice_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = SaleInvoice.objects.for_tenant(tenant)
    total = qs.count()
    confirmed = qs.filter(status='confirmed').count()
    draft = qs.filter(status='draft').count()
    cancelled = qs.filter(status='cancelled').count()
    returned = qs.filter(status__in=['returned', 'partially_returned']).count()

    grand_total_sum = (
        qs.filter(status='confirmed')
        .aggregate(s=Sum('grand_total'))['s'] or Decimal('0')
    )
    paid_total = (
        qs.filter(status='confirmed')
        .aggregate(s=Sum('paid_amount'))['s'] or Decimal('0')
    )
    unpaid_total = grand_total_sum - paid_total

    # للفلترة في الـ DataTable
    customers = Customer.objects.for_tenant(tenant).filter(is_active=True).values('id', 'name')
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True).values('id', 'name')

    context = {
        'stats': {
            'total': total,
            'confirmed': confirmed,
            'draft': draft,
            'cancelled': cancelled,
            'returned': returned,
            'grand_total_sum': grand_total_sum,
            'paid_total': paid_total,
            'unpaid_total': unpaid_total,
        },
        'customers': list(customers),
        'stocks': list(stocks),
    }
    return render(request, 'sales/invoice_list.html', context)


@login_required
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

    qs = SaleInvoice.objects.for_tenant(tenant).select_related('customer', 'stock')
    total = qs.count()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if customer_filter:
        qs = qs.filter(customer_id=customer_filter)
    if payment_filter:
        qs = qs.filter(payment_method=payment_filter)

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
    qs = qs.order_by(order_field)

    page_qs = qs[start: start + length]

    STATUS_LABELS = {
        'draft': ('مسودة', 'secondary'),
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
        data.append({
            'id': inv.id,
            'invoice_number': inv.invoice_number,
            'invoice_date': inv.invoice_date.strftime('%Y-%m-%d'),
            'customer': inv.customer.name if inv.customer else '—',
            'stock': inv.stock.name,
            'payment_method': PAYMENT_LABELS.get(inv.payment_method, inv.payment_method),
            'grand_total': str(inv.grand_total),
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
    })


@login_required
# ─────────────────────────────────────────────
#   INVOICE CREATE / EDIT
# ─────────────────────────────────────────────

@login_required
def invoice_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    customers = Customer.objects.for_tenant(tenant).filter(is_active=True)
    stocks = Stock.objects.for_tenant(tenant).filter(is_active=True)
    items = Item.objects.for_tenant(tenant).filter(is_active=True, is_sellable=True)

    # default stock
    default_stock = stocks.filter(is_default=True).first() or stocks.first()

    if request.method == 'POST':
        error = _process_invoice_post(request, tenant, invoice=None)
        if isinstance(error, SaleInvoice):
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
        'default_stock': default_stock,
        'today': timezone.now().date().isoformat(),
        'action': 'create',
    }
    return render(request, 'sales/invoice_form.html', context)


@login_required
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

    if request.method == 'POST':
        result = _process_invoice_post(request, tenant, invoice=invoice)
        if isinstance(result, SaleInvoice):
            return redirect('sales:invoice_detail', pk=result.id)
        if isinstance(result, JsonResponse):
            return result
        return redirect('sales:invoice_edit', pk=pk)

    existing_lines = []
    for line in invoice.lines.select_related('item', 'variant'):
        existing_lines.append({
            'item_id': line.item_id,
            'item_name': line.item.name,
            'item_sku': line.item.sku,
            'variant_id': line.variant_id or '',
            'quantity': str(line.quantity),
            'unit_price': str(line.unit_price),
            'discount_percent': str(line.discount_percent),
            'tax_rate': str(line.tax_rate),
            'cost_price_snapshot': str(line.cost_price_snapshot),
            'batch_number': line.batch_number,
            'serial_number': line.serial_number,
            'expiry_date': line.expiry_date.isoformat() if line.expiry_date else '',
            'line_total': str(line.line_total),
        })

    context = {
        'invoice': invoice,
        'existing_lines': json.dumps(existing_lines, ensure_ascii=False),
        'customers': customers,
        'stocks': stocks,
        'items': items,
        'today': timezone.now().date().isoformat(),
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
                'variant_id': int(ld['variant_id']) if ld.get('variant_id') else None,
                'quantity': Decimal(str(ld['quantity'])),
                'unit_price': Decimal(str(ld['unit_price'])),
                'discount_percent': Decimal(str(ld.get('discount_percent', 0))),
                'tax_rate': Decimal(str(ld.get('tax_rate', 0))),
                'cost_price_snapshot': Decimal(str(ld.get('cost_price_snapshot', 0))),
                'batch_number': ld.get('batch_number', ''),
                'serial_number': ld.get('serial_number', ''),
                'expiry_date': ld.get('expiry_date') or None,
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

                    inv = edit_confirmed_invoice(
                        invoice, confirmed_header, lines_data, request.user
                    )
                    # إعادة التأكيد تتم داخل edit_confirmed_invoice
                    return JsonResponse({'success': True, 'redirect': f'/sales/{inv.id}/'})
                else:
                    # draft edit
                    invoice.lines.all().delete()
                    from apps.items.models import Item as _Item, ItemVariant as _IV
                    for ld in lines_data:
                        item = _Item.objects.get(id=ld['item_id'], tenant=tenant)
                        variant = _IV.objects.get(id=ld['variant_id'], tenant=tenant) if ld.get('variant_id') else None
                        line = SaleInvoiceLine(
                            tenant=tenant, invoice=invoice, item=item, variant=variant,
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
def invoice_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(
        SaleInvoice.objects.select_related('customer', 'stock', 'confirmed_by', 'cancelled_by'),
        pk=pk, tenant=tenant,
    )
    lines = invoice.lines.select_related('item', 'variant').all()
    payments = invoice.payments.all()
    returns = invoice.sale_returns.filter(status='confirmed').select_related('confirmed_by')

    context = {
        'invoice': invoice,
        'lines': lines,
        'payments': payments,
        'returns': returns,
        'can_confirm': invoice.status == 'draft',
        'can_delete_draft': invoice.status == 'draft',
        'can_edit': invoice.status in ('draft', 'confirmed'),
        'can_cancel': invoice.status == 'confirmed',
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

    with transaction.atomic():
        invoice.delete()

    return _json_ok(msg='تم حذف مسودة الفاتورة')


@login_required
@require_POST
def invoice_confirm_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(SaleInvoice, pk=pk, tenant=tenant)
    try:
        confirm_sale_invoice(invoice, request.user)
        return _json_ok(msg='تم تأكيد الفاتورة بنجاح')
    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        return _json_error(f'حدث خطأ: {str(e)}')


@login_required
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
        return _json_ok(msg='تم إلغاء الفاتورة')
    except ValueError as e:
        return _json_error(str(e))


@login_required
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
        date = body.get('date') or timezone.now().date().isoformat()
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
    })


@login_required
def return_create(request, invoice_pk):
    """إنشاء مرتجع لفاتورة محددة."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(
        SaleInvoice, pk=invoice_pk, tenant=tenant,
        status__in=['confirmed', 'partially_returned']
    )
    lines = invoice.lines.select_related('item', 'variant').all()
    returnable_lines = [l for l in lines if l.returnable_quantity > 0]

    if request.method == 'POST':
        result = _process_return_post(request, tenant, invoice)
        if isinstance(result, SaleReturn):
            return redirect('sales:return_detail', pk=result.id)
        if isinstance(result, JsonResponse):
            return result
        return redirect('sales:return_create', invoice_pk=invoice_pk)

    context = {
        'invoice': invoice,
        'returnable_lines': returnable_lines,
        'today': timezone.now().date().isoformat(),
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
                return_date=header.get('return_date') or timezone.now().date(),
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
@require_POST
def return_confirm_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    sale_return = get_object_or_404(SaleReturn, pk=pk, tenant=tenant)
    try:
        confirm_sale_return(sale_return, request.user)
        return _json_ok(msg='تم تأكيد المرتجع بنجاح')
    except ValueError as e:
        return _json_error(str(e))


@login_required
@require_POST
def return_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    sale_return = get_object_or_404(SaleReturn, pk=pk, tenant=tenant)
    try:
        cancel_sale_return(sale_return, request.user)
        return _json_ok(msg='تم إلغاء المرتجع')
    except ValueError as e:
        return _json_error(str(e))


# ─────────────────────────────────────────────
#   AJAX HELPERS
# ─────────────────────────────────────────────

@login_required
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

    variants = []
    if item.has_variants:
        for v in item.variants.filter(is_active=True):
            variants.append({
                'id': v.id,
                'name': str(v),
                'selling_price': str(v.selling_price or item.selling_price),
            })

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
            'has_variants': item.has_variants,
            'track_batch': item.track_batch,
            'track_serial': item.track_serial,
            'track_expiry': item.track_expiry,
            'item_type': item.item_type,
            'is_service': is_service,
            'available_qty': available_qty,
            'variants': variants,
        }
    })


@login_required
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
        return JsonResponse({'success': True, 'items': []})

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
            'has_variants': item.has_variants,
        })

    return JsonResponse({'success': True, 'items': data})


# ═══════════════════════════════════════════════════════════
#   SALE QUOTE VIEWS  — عروض الأسعار
# ═══════════════════════════════════════════════════════════

@login_required
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
    })


@login_required
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

        return _json_ok({'redirect': f'/sales/quotes/{quote.pk}/'}, 'تم حفظ عرض السعر')

    return render(request, 'sales/quote_form.html', {
        'customers': customers,
        'stocks': stocks,
        'quote': None,
        'today': timezone.now().date().isoformat(),
        'lines_json': '[]',
    })


@login_required
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
    for ql in quote.quote_lines.select_related('item', 'variant').all():
        lines_json.append({
            'item_id': ql.item_id,
            'item_name': ql.item.name,
            'variant_id': ql.variant_id,
            'variant_name': str(ql.variant) if ql.variant else '',
            'quantity': str(ql.quantity),
            'unit_price': str(ql.unit_price),
            'discount_percent': str(ql.discount_percent),
            'tax_rate': str(ql.tax_rate),
            'line_total': str(ql.line_total),
        })

    return render(request, 'sales/quote_form.html', {
        'customers': customers,
        'stocks': stocks,
        'quote': quote,
        'lines_json': json.dumps(lines_json),
        'today': timezone.now().date().isoformat(),
    })


@login_required
def quote_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    quote = get_object_or_404(
        SaleQuote.objects.select_related('customer', 'stock', 'converted_invoice', 'converted_by'),
        pk=pk, tenant=tenant
    )
    lines = quote.quote_lines.select_related('item', 'variant').all()

    return render(request, 'sales/quote_detail.html', {
        'quote': quote,
        'lines': lines,
        'can_edit': quote.status == 'draft',
        'can_send': quote.can_send,
        'can_accept': quote.status == 'sent',
        'can_reject': quote.status in ('sent', 'accepted'),
        'can_convert': quote.can_convert,
        'can_cancel': quote.can_cancel,
    })


@login_required
@require_POST
def quote_send_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        mark_quote_sent(quote, request.user)
        return _json_ok(msg='تم تغيير حالة العرض إلى مُرسَل')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def quote_accept_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        mark_quote_accepted(quote, request.user)
        return _json_ok(msg='تم قبول العرض')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def quote_reject_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        mark_quote_rejected(quote, request.user)
        return _json_ok(msg='تم رفض العرض')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def quote_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    try:
        cancel_sale_quote(quote, request.user)
        return _json_ok(msg='تم إلغاء عرض السعر')
    except Exception as e:
        return _json_error(str(e))


@login_required
@require_POST
def quote_delete_draft_ajax(request, pk):
    tenant = _ensure_tenant(request)
    quote = get_object_or_404(SaleQuote, pk=pk, tenant=tenant)
    if quote.status != 'draft':
        return _json_error('لا يمكن حذف إلا المسودات')
    quote.delete()
    return _json_ok(msg='تم حذف مسودة العرض')


@login_required
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
        return _json_ok(
            {'invoice_url': f'/sales/{invoice.pk}/'},
            msg=f'تم إنشاء الفاتورة {invoice.invoice_number} من عرض السعر'
        )
    except Exception as e:
        return _json_error(str(e))
