import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.items.models import Item
from apps.stocks.models import Stock
from apps.suppliers.models import Supplier

from .models import PurchaseInvoice, PurchaseReturn, PurchaseReturnLine, SupplierLedger
from .services import (
    build_purchase_from_post,
    cancel_purchase_return,
    cancel_purchase_invoice,
    confirm_purchase_return,
    confirm_purchase_invoice,
    edit_confirmed_purchase_invoice,
)


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _json_error(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status)


@login_required
@require_permission('view_purchases')
def order_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = PurchaseInvoice.objects.filter(tenant=tenant)
    context = {
        'stats': {
            'total': qs.count(),
            'confirmed': qs.filter(status='confirmed').count(),
            'draft': qs.filter(status='draft').count(),
            'cancelled': qs.filter(status='cancelled').count(),
            'total_value': qs.filter(status='confirmed').aggregate(s=Sum('grand_total'))['s'] or Decimal('0'),
        }
    }
    return render(request, 'purchases/order_list.html', context)


@login_required
@require_permission('view_purchases')
def order_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '').strip()
    try:
        # Query only fields used by the table to avoid breakage if pending migrations exist.
        qs = PurchaseInvoice.objects.filter(tenant=tenant).values(
            'id',
            'invoice_number',
            'invoice_date',
            'status',
            'grand_total',
            'supplier__name',
            'stock__name',
        )
        total = qs.count()

        if status_filter:
            qs = qs.filter(status=status_filter)

        if search_value:
            qs = qs.filter(
                Q(invoice_number__icontains=search_value)
                | Q(supplier__name__icontains=search_value)
                | Q(stock__name__icontains=search_value)
            )

        filtered = qs.count()
        rows = list(qs.order_by('-invoice_date', '-id')[start:start + length])

        status_labels = {
            'draft': ('مسودة', 'secondary'),
            'confirmed': ('مؤكدة', 'success'),
            'cancelled': ('ملغاة', 'danger'),
        }

        data = []
        for inv in rows:
            status_label, status_color = status_labels.get(inv.get('status'), ('—', 'secondary'))
            inv_date = inv.get('invoice_date')
            data.append({
                'id': inv.get('id'),
                'invoice_number': inv.get('invoice_number') or '—',
                'invoice_date': inv_date.strftime('%Y-%m-%d') if inv_date else '—',
                'supplier': inv.get('supplier__name') or '—',
                'stock': inv.get('stock__name') or '—',
                'grand_total': str(inv.get('grand_total') or 0),
                'status': inv.get('status') or 'draft',
                'status_label': status_label,
                'status_color': status_color,
            })

        return JsonResponse({
            'draw': draw,
            'recordsTotal': total,
            'recordsFiltered': filtered,
            'data': data,
        })
    except Exception as e:
        return JsonResponse({
            'draw': draw,
            'recordsTotal': 0,
            'recordsFiltered': 0,
            'data': [],
            'error': str(e),
        })


@login_required
@require_permission('add_purchases')
def order_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    if request.method == 'POST':
        return _process_order_post(request, tenant, invoice=None)

    context = {
        'suppliers': Supplier.objects.for_tenant(tenant).filter(is_active=True).order_by('name'),
        'stocks': Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name'),
        'today': timezone.now().date().isoformat(),
        'action': 'create',
        'existing_lines': '[]',
        'invoice': None,
    }
    return render(request, 'purchases/order_form.html', context)


@login_required
@require_permission('change_purchases')
def order_edit(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(PurchaseInvoice, pk=pk, tenant=tenant)
    if invoice.status not in ('draft', 'confirmed'):
        return redirect('purchases:order_detail', pk=pk)

    if request.method == 'POST':
        return _process_order_post(request, tenant, invoice=invoice)

    existing_lines = []
    for line in invoice.lines.select_related('item'):
        existing_lines.append({
            'item_id': line.item_id,
            'item_name': line.item.name,
            'item_sku': line.item.sku,
            'quantity': str(line.quantity),
            'unit_cost': str(line.unit_cost),
            'tax_rate': str(line.tax_rate),
        })

    context = {
        'invoice': invoice,
        'suppliers': Supplier.objects.for_tenant(tenant).filter(is_active=True).order_by('name'),
        'stocks': Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name'),
        'today': timezone.now().date().isoformat(),
        'action': 'edit',
        'existing_lines': json.dumps(existing_lines, ensure_ascii=False),
    }
    return render(request, 'purchases/order_form.html', context)


def _process_order_post(request, tenant, invoice):
    try:
        body = json.loads(request.body or '{}')
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _json_error('بيانات غير صالحة')

    header = body.get('header', {})
    lines_raw = body.get('lines', [])
    action = body.get('action', 'save_draft')

    payment_method = str(header.get('payment_method') or 'cash').strip()
    if payment_method not in {'cash', 'bank', 'credit', 'mixed'}:
        return _json_error('طريقة الدفع غير صالحة')

    try:
        cash_amount = Decimal(str(header.get('cash_amount') or 0))
        bank_amount = Decimal(str(header.get('bank_amount') or 0))
    except (InvalidOperation, TypeError):
        return _json_error('قيم الدفع غير صالحة')

    if cash_amount < 0 or bank_amount < 0:
        return _json_error('مبالغ الدفع يجب أن تكون أكبر من أو تساوي صفر')

    bank_reference = str(header.get('bank_reference') or '').strip()

    if not lines_raw:
        return _json_error('لا يمكن حفظ أمر شراء بدون بنود')

    lines_data = []
    for ld in lines_raw:
        try:
            lines_data.append({
                'item_id': int(ld['item_id']),
                'quantity': Decimal(str(ld['quantity'])),
                'unit_cost': Decimal(str(ld['unit_cost'])),
                'tax_rate': Decimal(str(ld.get('tax_rate', 0))),
            })
        except (KeyError, InvalidOperation, ValueError):
            return _json_error('بيانات البنود غير صالحة')

    try:
        stock = Stock.objects.get(id=header.get('stock_id'), tenant=tenant)
    except Stock.DoesNotExist:
        return _json_error('المخزن المحدد غير موجود')

    try:
        if invoice is None:
            create_data = {
                **header,
                'payment_method': payment_method,
                'cash_amount': cash_amount,
                'bank_amount': bank_amount,
                'bank_reference': bank_reference,
            }
            inv = build_purchase_from_post(tenant, stock, create_data, lines_data, request.user)
        else:
            if invoice.status == 'confirmed':
                confirmed_header = {
                    'invoice_date': header.get('invoice_date') or invoice.invoice_date,
                    'payment_method': payment_method or invoice.payment_method,
                    'cash_amount': cash_amount,
                    'bank_amount': bank_amount,
                    'bank_reference': bank_reference,
                    'notes': header.get('notes', ''),
                }

                supplier_raw = header.get('supplier_id')
                if supplier_raw:
                    confirmed_header['supplier'] = Supplier.objects.get(id=supplier_raw, tenant=tenant)
                else:
                    confirmed_header['supplier'] = None

                stock_raw = header.get('stock_id')
                if stock_raw:
                    confirmed_header['stock'] = Stock.objects.get(id=stock_raw, tenant=tenant)

                inv = edit_confirmed_purchase_invoice(invoice, confirmed_header, lines_data, request.user)
                return JsonResponse({'success': True, 'redirect': f'/purchases/{inv.id}/'})

            invoice.lines.all().delete()
            for ld in lines_data:
                item = Item.objects.get(id=ld['item_id'], tenant=tenant)
                line = invoice.lines.model(
                    tenant=tenant,
                    invoice=invoice,
                    item=item,
                    quantity=ld['quantity'],
                    unit_cost=ld['unit_cost'],
                    tax_rate=ld['tax_rate'],
                )
                line.calculate()
                line.save()

            supplier_raw = header.get('supplier_id')
            invoice.supplier_id = int(supplier_raw) if supplier_raw else None
            invoice.stock = stock
            invoice.invoice_date = header.get('invoice_date') or invoice.invoice_date
            invoice.payment_method = payment_method or invoice.payment_method
            invoice.cash_amount = cash_amount
            invoice.bank_amount = bank_amount
            invoice.bank_reference = bank_reference
            invoice.notes = header.get('notes', '')
            invoice.recalculate_totals()
            invoice.save()
            inv = invoice

        if action == 'confirm':
            confirm_purchase_invoice(inv, request.user)

    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        return _json_error(f'حدث خطأ: {str(e)}')

    return JsonResponse({'success': True, 'redirect': f'/purchases/{inv.id}/'})


@login_required
@require_permission('view_purchases')
def order_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(
        PurchaseInvoice.objects.select_related('supplier', 'stock'),
        pk=pk,
        tenant=tenant,
    )
    lines = invoice.lines.select_related('item').all()
    can_return = invoice.status in ('confirmed', 'partially_returned') and any(
        (l.returnable_quantity or Decimal('0')) > 0 for l in lines
    )

    return render(request, 'purchases/order_detail.html', {
        'invoice': invoice,
        'lines': lines,
        'can_confirm': invoice.status == 'draft',
        'can_cancel': invoice.status == 'confirmed',
        'can_edit': invoice.status in ('draft', 'confirmed'),
        'can_return': can_return,
    })


@login_required
@require_permission('view_purchases')
def order_print(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    get_object_or_404(PurchaseInvoice.objects.only('id', 'tenant_id'), pk=pk, tenant=tenant)
    return redirect(f"{reverse('purchases:order_detail', kwargs={'pk': pk})}?print=1")


@login_required
@require_permission('change_purchases')
@require_POST
def order_confirm_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(PurchaseInvoice, pk=pk, tenant=tenant)
    try:
        confirm_purchase_invoice(invoice, request.user)
        return JsonResponse({'success': True, 'message': 'تم تأكيد أمر الشراء'})
    except ValueError as e:
        return _json_error(str(e))


@login_required
@require_permission('delete_purchases')
@require_POST
def order_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    invoice = get_object_or_404(PurchaseInvoice, pk=pk, tenant=tenant)
    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        body = {}

    reason = body.get('reason', '')
    try:
        cancel_purchase_invoice(invoice, request.user, reason)
        return JsonResponse({'success': True, 'message': 'تم إلغاء أمر الشراء'})
    except ValueError as e:
        return _json_error(str(e))


@login_required
@require_permission('view_purchase_returns')
def return_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = PurchaseReturn.objects.filter(tenant=tenant)
    context = {
        'stats': {
            'total': qs.count(),
            'confirmed': qs.filter(status='confirmed').count(),
            'draft': qs.filter(status='draft').count(),
            'cancelled': qs.filter(status='cancelled').count(),
        }
    }
    return render(request, 'purchases/return_list.html', context)


@login_required
@require_permission('view_purchase_returns')
def return_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '')

    qs = PurchaseReturn.objects.filter(tenant=tenant).select_related(
        'original_invoice', 'original_invoice__supplier'
    )
    total = qs.count()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if search_value:
        qs = qs.filter(
            Q(return_number__icontains=search_value)
            | Q(original_invoice__invoice_number__icontains=search_value)
            | Q(original_invoice__supplier__name__icontains=search_value)
        )

    filtered = qs.count()
    page_qs = qs.order_by('-return_date', '-created_at')[start:start + length]

    labels = {
        'draft': ('مسودة', 'secondary'),
        'confirmed': ('مؤكد', 'success'),
        'cancelled': ('ملغي', 'danger'),
    }

    data = []
    for r in page_qs:
        status_label, _ = labels.get(r.status, (r.status, 'secondary'))
        data.append({
            'id': r.id,
            'return_number': r.return_number,
            'return_date': r.return_date.strftime('%Y-%m-%d'),
            'invoice_number': r.original_invoice.invoice_number,
            'supplier': r.original_invoice.supplier.name if r.original_invoice.supplier else '—',
            'total_returned': str(r.total_returned),
            'status': r.status,
            'status_label': status_label,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered,
        'data': data,
    })


@login_required
@require_permission('view_purchase_returns')
def return_lines_api(request, return_pk):
    """API: جلب بنود المرتجع (للمودال)"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    purchase_return = get_object_or_404(PurchaseReturn, pk=return_pk, tenant=tenant)
    lines = purchase_return.lines.select_related('item').all()

    data = []
    for line in lines:
        data.append({
            'item_name': line.item.name,
            'returned_quantity': str(line.returned_quantity),
            'unit_price': str(line.unit_cost),
            'line_total': str(line.line_total),
        })

    return JsonResponse({'success': True, 'lines': data})


@login_required
@require_permission('add_purchase_returns')
def return_create(request, invoice_pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    invoice = get_object_or_404(
        PurchaseInvoice, pk=invoice_pk, tenant=tenant,
        status__in=['confirmed', 'partially_returned']
    )
    lines = invoice.lines.select_related('item').all()
    returnable_lines = [l for l in lines if l.returnable_quantity > 0]

    if request.method == 'POST':
        result = _process_return_post(request, tenant, invoice)
        if isinstance(result, JsonResponse):
            return result

    return render(request, 'purchases/return_form.html', {
        'invoice': invoice,
        'returnable_lines': returnable_lines,
        'today': timezone.now().date().isoformat(),
    })


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
            purchase_return = PurchaseReturn.objects.create(
                tenant=tenant,
                return_date=header.get('return_date') or timezone.now().date(),
                original_invoice=invoice,
                refund_method=header.get('refund_method', 'balance'),
                reason=header.get('reason', ''),
                notes=header.get('notes', ''),
                created_by=request.user,
                updated_by=request.user,
            )

            total = Decimal('0')
            for ld in lines_raw:
                inv_line = get_object_or_404(
                    invoice.lines.select_related('item'), pk=ld['invoice_line_id']
                )
                qty = Decimal(str(ld.get('returned_quantity', 0)))
                if qty <= 0:
                    continue
                line_total = (qty * (inv_line.unit_cost or Decimal('0'))).quantize(Decimal('0.01'))
                PurchaseReturnLine.objects.create(
                    tenant=tenant,
                    purchase_return=purchase_return,
                    invoice_line=inv_line,
                    item=inv_line.item,
                    returned_quantity=qty,
                    unit_cost=inv_line.unit_cost,
                    line_total=line_total,
                    created_by=request.user,
                    updated_by=request.user,
                )
                total += line_total

            purchase_return.total_returned = total
            purchase_return.save(update_fields=['total_returned', 'updated_at'])

            if action == 'confirm':
                confirm_purchase_return(purchase_return, request.user)

    except ValueError as e:
        return _json_error(str(e))
    except Exception as e:
        return _json_error(f'حدث خطأ: {str(e)}')

    return JsonResponse({'success': True, 'redirect': f'/purchases/returns/{purchase_return.id}/'})


@login_required
@require_permission('view_purchase_returns')
def return_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    purchase_return = get_object_or_404(
        PurchaseReturn.objects.select_related('original_invoice', 'original_invoice__supplier'),
        pk=pk, tenant=tenant,
    )
    lines = purchase_return.lines.select_related('item', 'invoice_line')
    return render(request, 'purchases/return_detail.html', {
        'purchase_return': purchase_return,
        'return_lines': lines,
        'can_confirm': purchase_return.status == 'draft',
        'can_cancel': purchase_return.status == 'confirmed',
    })


@login_required
@require_permission('add_purchase_returns')
@require_POST
def return_confirm_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    purchase_return = get_object_or_404(PurchaseReturn, pk=pk, tenant=tenant)
    try:
        confirm_purchase_return(purchase_return, request.user)
        return JsonResponse({'success': True, 'message': 'تم تأكيد المرتجع بنجاح'})
    except ValueError as e:
        return _json_error(str(e))


@login_required
@require_permission('add_purchase_returns')
@require_POST
def return_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    purchase_return = get_object_or_404(PurchaseReturn, pk=pk, tenant=tenant)
    try:
        cancel_purchase_return(purchase_return, request.user)
        return JsonResponse({'success': True, 'message': 'تم إلغاء المرتجع'})
    except ValueError as e:
        return _json_error(str(e))
