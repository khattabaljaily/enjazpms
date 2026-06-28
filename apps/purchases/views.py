import json
import re
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from apps.accounts.activity_service import log_activity

from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from django.db import transaction
from django.db.models import Q, Sum, Subquery, OuterRef
from django.db.models.functions import Coalesce
from django.db.models import DecimalField as DjDecimalField
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.items.models import Item
from apps.purchases.models import PurchaseInvoice, PurchaseReturn, PurchaseReturnLine, PurchaseRFQ, PurchaseRFQLine
from apps.purchases.services import build_purchase_from_post, cancel_purchase_invoice, cancel_purchase_return, confirm_purchase_invoice, confirm_purchase_return, edit_confirmed_purchase_invoice
from apps.stocks.models import Stock
from apps.suppliers.models import Supplier

from .reports import PurchasesReportGenerator


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _json_error(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status, json_dumps_params={'ensure_ascii': False})


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
        _returned_sq = (
            PurchaseReturn.objects
            .filter(original_invoice=OuterRef('pk'), status='confirmed')
            .values('original_invoice')
            .annotate(t=Sum('total_returned'))
            .values('t')
        )

        qs = PurchaseInvoice.objects.filter(tenant=tenant).select_related('supplier', 'stock')
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
        qs = qs.annotate(
            returned_amt=Coalesce(
                Subquery(_returned_sq, output_field=DjDecimalField(max_digits=14, decimal_places=2)),
                Decimal('0')
            )
        )
        rows = list(qs.order_by('-invoice_date', '-id')[start:start + length])

        status_labels = {
            'draft': ('مسودة', 'secondary'),
            'confirmed': ('مؤكدة', 'success'),
            'cancelled': ('ملغاة', 'danger'),
            'partially_returned': ('مرتجع جزئي', 'warning'),
            'returned': ('مرتجع كلي', 'dark'),
        }

        data = []
        for inv in rows:
            status_label, status_color = status_labels.get(inv.status, ('—', 'secondary'))
            returned_amt = inv.returned_amt or Decimal('0')
            net_total = inv.grand_total - returned_amt
            data.append({
                'id': inv.id,
                'invoice_number': inv.invoice_number or '—',
                'invoice_date': inv.invoice_date.strftime('%Y-%m-%d') if inv.invoice_date else '—',
                'supplier': inv.supplier.name if inv.supplier else '—',
                'stock': inv.stock.name if inv.stock else '—',
                'grand_total': str(inv.grand_total or 0),
                'returned_amount': str(returned_amt),
                'net_total': str(net_total),
                'status': inv.status or 'draft',
                'status_label': status_label,
                'status_color': status_color,
            })

        return JsonResponse({
            'draw': draw,
            'recordsTotal': total,
            'recordsFiltered': filtered,
            'data': data,
        }, json_dumps_params={'ensure_ascii': False})
    except Exception as e:
        return JsonResponse({
            'draw': draw,
            'recordsTotal': 0,
            'recordsFiltered': 0,
            'data': [],
            'error': str(e),
        }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_purchases')
def order_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    if request.method == 'POST':
        return _process_order_post(request, tenant, invoice=None)

    _hc_mode = getattr(tenant, 'hard_currency_mode', False)
    _hc_rate = float(tenant.exchange_rate) if _hc_mode and getattr(tenant, 'exchange_rate', None) else 0
    context = {
        'suppliers': Supplier.objects.for_tenant(tenant).filter(is_active=True).order_by('name'),
        'stocks': Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name'),
        'today': timezone.localdate().isoformat(),
        'action': 'create',
        'existing_lines': '[]',
        'invoice': None,
        'hc_mode': _hc_mode,
        'hc_rate': _hc_rate,
        'tenant_currency': (getattr(tenant, 'currency', '') or '').strip(),
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
    for line in invoice.lines.select_related('item').prefetch_related('item__item_units'):
        iu_qs = list(line.item.item_units.order_by('factor'))
        units = [{'id': iu.id, 'name': iu.name, 'factor': str(iu.factor)} for iu in iu_qs]
        # match saved unit_factor to find the right ItemUnit
        unit_id = ''
        if iu_qs:
            matched = next((iu for iu in iu_qs if abs(float(iu.factor) - float(line.unit_factor or 1)) < 0.0001), iu_qs[0])
            unit_id = matched.id
        existing_lines.append({
            'item_id': line.item_id,
            'item_name': line.item.name,
            'item_sku': line.item.sku,
            'quantity': str(line.quantity),
            'unit_cost': str(line.unit_cost),
            'tax_rate': str(line.tax_rate),
            'units': units,
            'unit_id': unit_id,
            'unit_factor': str(line.unit_factor or 1),
            'batch_number': line.batch_number or '',
            'serial_number': line.serial_number or '',
            'expiry_date': line.expiry_date.isoformat() if line.expiry_date else '',
        })

    _hc_mode = getattr(tenant, 'hard_currency_mode', False)
    _hc_rate = float(tenant.exchange_rate) if _hc_mode and getattr(tenant, 'exchange_rate', None) else 0
    context = {
        'invoice': invoice,
        'suppliers': Supplier.objects.for_tenant(tenant).filter(is_active=True).order_by('name'),
        'stocks': Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name'),
        'today': timezone.localdate().isoformat(),
        'action': 'edit',
        'existing_lines': json.dumps(existing_lines, ensure_ascii=False),
        'hc_mode': _hc_mode,
        'hc_rate': _hc_rate,
        'tenant_currency': (getattr(tenant, 'currency', '') or '').strip(),
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
                'unit_cost': Decimal(str(ld['unit_cost'] or '0')),
                'tax_rate': Decimal(str(ld.get('tax_rate') or '0')),
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

    if invoice is None:
        supplier = inv.supplier.name if inv.supplier else 'بدون مورد'
        log_activity(request, 'إنشاء أمر شراء',
                     f"أمر الشراء: {inv.invoice_number}\nالمورد: {supplier}\nالإجمالي: {inv.grand_total}", 'create')

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
    lines = list(invoice.lines.select_related('item').prefetch_related('item__item_units').all())
    for ln in lines:
        iu_list = list(ln.item.item_units.order_by('factor'))
        matched = next((u for u in iu_list if abs(float(u.factor) - float(ln.unit_factor or 1)) < 0.0001), iu_list[0] if iu_list else None)
        ln.unit_display = matched.name if matched else ln.item.base_unit_name
    can_return = invoice.status in ('confirmed', 'partially_returned') and any(
        (l.returnable_quantity or Decimal('0')) > 0 for l in lines
    )

    returns = invoice.purchase_returns.filter(status='confirmed').order_by('return_date', 'id')

    from apps.core.models import Settings as TenantSettings
    settings_obj, _ = TenantSettings.objects.get_or_create(tenant=tenant)

    returned_amount = sum(r.total_returned for r in returns)
    net_total = invoice.grand_total - returned_amount

    return render(request, 'purchases/order_detail.html', {
        'invoice': invoice,
        'lines': lines,
        'returns': returns,
        'returned_amount': returned_amount,
        'net_total': net_total,
        'can_confirm': invoice.status == 'draft',
        'can_cancel': invoice.status == 'confirmed',
        'can_edit': invoice.status in ('draft', 'confirmed'),
        'can_return': can_return,
        'settings_obj': settings_obj,
        'tenant': tenant,
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
        log_activity(request, 'تأكيد أمر شراء', f'{invoice.invoice_number} — {invoice.supplier.name}', 'create')
        return JsonResponse({'success': True, 'message': 'تم تأكيد أمر الشراء'}, json_dumps_params={'ensure_ascii': False})
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
        supplier_name = invoice.supplier.name if invoice.supplier else 'مورد محذوف'
        log_activity(request, 'إلغاء أمر شراء', f'{invoice.invoice_number} — {supplier_name}', 'delete')
        return JsonResponse({'success': True, 'message': 'تم إلغاء أمر الشراء'}, json_dumps_params={'ensure_ascii': False})
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
    }, json_dumps_params={'ensure_ascii': False})


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

    return JsonResponse({'success': True, 'lines': data}, json_dumps_params={'ensure_ascii': False})


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
        'today': timezone.localdate().isoformat(),
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
                return_date=header.get('return_date') or timezone.localdate(),
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

    supplier = invoice.supplier.name if invoice.supplier else '—'
    log_activity(request, 'إنشاء مرتجع مشتريات',
                 f"المرتجع: {purchase_return.return_number}\nأمر الشراء: {invoice.invoice_number}\nالمورد: {supplier}\nالمبلغ المسترد: {purchase_return.total_returned}", 'create')

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
        log_activity(request, 'تأكيد مرتجع مشتريات', f'{purchase_return.return_number}', 'create')
        return JsonResponse({'success': True, 'message': 'تم تأكيد المرتجع بنجاح'}, json_dumps_params={'ensure_ascii': False})
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
        log_activity(request, 'إلغاء مرتجع مشتريات', f'{purchase_return.return_number}', 'delete')
        return JsonResponse({'success': True, 'message': 'تم إلغاء المرتجع'}, json_dumps_params={'ensure_ascii': False})
    except ValueError as e:
        return _json_error(str(e))


# ─────────────────────────────────────────────
#   REPORTS
# ─────────────────────────────────────────────

@login_required
@require_permission('view_purchases_summary_report')
def purchases_summary_report(request):
    """تقرير ملخص المشتريات"""
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
    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_summary_report()

    # Chart data: daily trend for the selected period
    import json
    trend = generator.get_by_date_report(group_by='day')
    chart_labels  = json.dumps([d['label'] for d in trend['data']], ensure_ascii=False)
    chart_amounts = json.dumps([float(str(d['total_amount']).replace(',', '')) for d in trend['data']])

    return render(request, 'purchases/reports/summary.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'purchases_reports',
        'report_type': 'summary',
        'chart_labels': chart_labels,
        'chart_amounts': chart_amounts,
    })


@login_required
@require_permission('view_purchases_summary_report')
def purchases_summary_report_export(request):
    """تصدير تقرير ملخص المشتريات"""
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
    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_summary_report()

    # Create CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchases_summary_{end_date}.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(['تقرير ملخص المشتريات'])
    writer.writerow([])
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    writer.writerow(['البيان', 'القيمة'])
    writer.writerow(['عدد أوامر الشراء', report_data['summary']['invoice_count']])
    writer.writerow(['إجمالي الكمية', report_data['summary']['total_quantity']])
    writer.writerow(['إجمالي المشتريات', report_data['summary']['total_amount']])
    writer.writerow(['إجمالي الضريبة', report_data['summary']['total_tax']])
    writer.writerow(['متوسط الأمر', report_data['summary']['avg_invoice_amount']])

    return response


@login_required
@require_permission('view_purchases_by_supplier_report')
def purchases_by_supplier_report(request):
    """تقرير المشتريات حسب المورد"""
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

    # Optional supplier filter
    supplier_id = request.GET.get('supplier_id')

    # Generate report
    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_supplier_report(supplier_id=supplier_id)

    # suppliers list for filter dropdown
    from apps.suppliers.models import Supplier
    suppliers = Supplier.objects.filter(tenant=tenant).order_by('name')

    selected_supplier = None
    if supplier_id:
        try:
            selected_supplier = Supplier.objects.get(tenant=tenant, id=supplier_id)
        except Supplier.DoesNotExist:
            selected_supplier = None

    return render(request, 'purchases/reports/by_supplier.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'suppliers': suppliers,
        'selected_supplier_id': supplier_id,
        'selected_supplier': selected_supplier,
        'section': 'purchases_reports',
        'report_type': 'by_supplier',
    })


@login_required
@require_permission('view_purchases_by_supplier_report')
def purchases_by_supplier_report_export(request):
    """تصدير تقرير المشتريات حسب المورد"""
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

    # Optional supplier filter
    supplier_id = request.GET.get('supplier_id')

    # Generate report
    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_supplier_report(supplier_id=supplier_id)

    # Create CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchases_by_supplier_{end_date}.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(['تقرير المشتريات حسب المورد'])
    writer.writerow([])
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    # If supplier filter provided, export invoice-level details
    if supplier_id and report_data.get('supplier'):
        sup = None
        try:
            from apps.suppliers.models import Supplier
            sup = Supplier.objects.get(tenant=tenant, id=supplier_id)
        except Exception:
            sup = None

        if sup:
            writer.writerow([f"المورد: {sup.name}"])
            if getattr(sup, 'phone', None):
                writer.writerow([f"هاتف: {sup.phone}"])
            if getattr(sup, 'email', None):
                writer.writerow([f"بريد إلكتروني: {sup.email}"])
        else:
            writer.writerow([f"المورد: {report_data['supplier']['name']}"])

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
        writer.writerow(['اسم المورد', 'عدد الأوامر', 'إجمالي الكمية', 'إجمالي المشتريات', 'متوسط الأمر'])
        for item in report_data['data']:
            writer.writerow([
                item['supplier_name'],
                item['invoice_count'],
                item['total_quantity'],
                item['total_amount'],
                item['avg_invoice_amount'],
            ])

    return response


@login_required
@require_permission('view_purchases_by_item_report')
def purchases_by_item_report(request):
    """تقرير المشتريات حسب المنتج"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_item_report(item_id=item_id)

    items = Item.objects.filter(
        tenant=tenant,
        purchase_lines__invoice__status='confirmed',
    ).distinct().order_by('name')

    return render(request, 'purchases/reports/by_item.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'items': items,
        'selected_item_id': item_id,
        'section': 'purchases_reports',
        'report_type': 'by_item',
    })


@login_required
@require_permission('view_purchases_by_item_report')
def purchases_by_item_report_export(request):
    """تصدير تقرير المشتريات حسب المنتج"""
    import csv
    from django.http import HttpResponse

    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_item_report(item_id=item_id)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchases_by_item_{end_date}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    if report_data.get('item'):
        writer.writerow([f'المنتج: {report_data["item"]["name"]}'])
        writer.writerow([])
        writer.writerow(['رقم الفاتورة', 'تاريخ الفاتورة', 'المورد', 'الكمية', 'سعر الوحدة', 'الإجمالي'])
        for row in report_data['data']:
            writer.writerow([row['invoice_number'], row['invoice_date'], row['supplier_name'], row['quantity'], row['unit_cost'], row['line_total']])
    else:
        writer.writerow(['اسم المنتج', 'الوحدة', 'الكمية المشتراة', 'إجمالي المشتريات', 'متوسط سعر الوحدة', 'عدد البنود'])
        for item in report_data['data']:
            writer.writerow([item['item_name'], item['unit'], item['quantity_purchased'], item['total_amount'], item['avg_unit_cost'], item['purchase_lines']])

    return response


@login_required
@require_permission('view_purchases_by_date_report')
def purchases_by_date_report(request):
    """تقرير المشتريات حسب التاريخ"""
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
    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_date_report(group_by)

    return render(request, 'purchases/reports/by_date.html', {
        'report': report_data,
        'start_date': start_date,
        'end_date': end_date,
        'group_by': group_by,
        'section': 'purchases_reports',
        'report_type': 'by_date',
    })


@login_required
@require_permission('view_purchases_by_date_report')
def purchases_by_date_report_export(request):
    """تصدير تقرير المشتريات حسب التاريخ"""
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
    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report_data = generator.get_by_date_report(group_by)

    # Create CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchases_by_date_{end_date}.csv"'
    response.write('\ufeff')

    writer = csv.writer(response)
    writer.writerow(['تقرير المشتريات حسب التاريخ'])
    writer.writerow([])
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([f'التجميع: {group_by}'])
    writer.writerow([])

    writer.writerow(['التاريخ', 'عدد الأوامر', 'إجمالي المشتريات', 'إجمالي الكمية'])
    for item in report_data['data']:
        writer.writerow([
            item['label'],
            item['invoice_count'],
            item['total_amount'],
            item['total_quantity'],
        ])

    return response


# ─────────────────────────────────────────────────────────────────
#   NEW REPORTS: Supplier Statement / Balances / Payments / Returns
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_purchases_supplier_statement_report')
def purchases_supplier_statement(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    supplier_id = request.GET.get('supplier_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report = generator.get_supplier_statement(supplier_id) if supplier_id else None
    suppliers = Supplier.objects.filter(tenant=tenant).order_by('name')

    return render(request, 'purchases/reports/supplier_statement.html', {
        'report': report,
        'suppliers': suppliers,
        'selected_supplier_id': supplier_id,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'purchases_reports',
    })


@login_required
@require_permission('view_purchases_supplier_statement_report')
def purchases_supplier_statement_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    supplier_id = request.GET.get('supplier_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_supplier_statement(supplier_id) if supplier_id else None
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="supplier_statement_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    if report:
        writer.writerow([f'كشف حساب: {report["supplier"].name}'])
        writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
        writer.writerow([])
        writer.writerow(['التاريخ', 'نوع القيد', 'المبلغ', 'المديونية التراكمية', 'ملاحظات'])
        for row in report['data']:
            writer.writerow([row['entry_date'], row['entry_type'], row['amount'], row['running_balance'], row['notes']])
    return response


@login_required
@require_permission('view_purchases_supplier_balances_report')
def purchases_supplier_balances(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = PurchasesReportGenerator(tenant).get_supplier_balances()

    return render(request, 'purchases/reports/supplier_balances.html', {
        'report': report,
        'section': 'purchases_reports',
    })


@login_required
@require_permission('view_purchases_supplier_balances_report')
def purchases_supplier_balances_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = PurchasesReportGenerator(tenant).get_supplier_balances()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="supplier_balances.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الكود', 'اسم المورد', 'الهاتف', 'الحد الائتماني', 'المديونية'])
    for row in report['data']:
        writer.writerow([row['code'], row['name'], row['phone'], row['credit_limit'], row['balance']])
    return response


@login_required
@require_permission('view_purchases_payments_report')
def purchases_payments_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    supplier_id = request.GET.get('supplier_id') or None

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_payments_report(supplier_id=supplier_id or None)
    suppliers = Supplier.objects.filter(tenant=tenant).order_by('name')

    return render(request, 'purchases/reports/payments.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'suppliers': suppliers,
        'selected_supplier_id': supplier_id or '',
        'section': 'purchases_reports',
    })


@login_required
@require_permission('view_purchases_payments_report')
def purchases_payments_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_payments_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchase_payments_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['تاريخ الدفع', 'رقم الفاتورة', 'المورد', 'طريقة الدفع', 'المبلغ', 'رقم مرجعي'])
    for row in report['data']:
        writer.writerow([row['payment_date'], row['invoice_number'], row['supplier_name'], row['payment_method'], row['amount'], row['reference_number']])
    return response


@login_required
@require_permission('view_purchases_returns_report')
def purchases_returns_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_returns_report()

    return render(request, 'purchases/reports/returns.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'purchases_reports',
    })


@login_required
@require_permission('view_purchases_returns_report')
def purchases_returns_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_returns_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchase_returns_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['تاريخ المرتجع', 'رقم المرتجع', 'رقم الفاتورة', 'المورد', 'اسم المنتج', 'الكمية المرتجعة', 'سعر الوحدة', 'إجمالي السطر', 'طريقة الاسترداد'])
    for row in report['data']:
        writer.writerow([row['return_date'], row['return_number'], row['invoice_number'], row['supplier_name'], row['item_name'], row['returned_quantity'], row['unit_cost'], row['line_total'], row['refund_method']])
    return response


# ─────────────────────────────────────────────────────────────────
#   BY USER
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_purchases_by_user_report')
def purchases_by_user_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    user_id = request.GET.get('user_id') or None

    generator = PurchasesReportGenerator(tenant, start_date, end_date)
    report = generator.get_by_user_report(user_id=user_id)

    from django.contrib.auth import get_user_model
    user_ids = PurchaseInvoice.objects.filter(tenant=tenant, status='confirmed').values_list('created_by', flat=True).distinct()
    users = get_user_model().objects.filter(pk__in=user_ids).order_by('first_name', 'last_name')

    return render(request, 'purchases/reports/by_user.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'users': users,
        'selected_user_id': user_id,
        'section': 'purchases_reports',
    })


@login_required
@require_permission('view_purchases_by_user_report')
def purchases_by_user_report_export(request):
    import csv
    from django.http import HttpResponse
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    user_id = request.GET.get('user_id') or None

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_by_user_report(user_id=user_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="purchases_by_user_{end_date}.csv"'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    if report.get('user'):
        writer.writerow([f'المستخدم: {report["user"]["name"]}'])
        writer.writerow([])
        writer.writerow(['رقم الفاتورة', 'تاريخ الفاتورة', 'المورد', 'الكمية الإجمالية', 'الإجمالي'])
        for row in report['data']:
            writer.writerow([row['invoice_number'], row['invoice_date'], row['supplier_name'], row['total_quantity'], row['grand_total']])
    else:
        writer.writerow(['المستخدم', 'عدد الفواتير', 'إجمالي المشتريات', 'متوسط الفاتورة'])
        for row in report['data']:
            writer.writerow([row['user_name'], row['invoice_count'], row['total_amount'], row['avg_invoice_amount']])

    return response


# ─────────────────────────────────────────────────────────────────
#   PURCHASE PRICE HISTORY REPORT
# ─────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_purchases_price_history_report')
def purchases_price_history_report(request):
    from apps.items.models import Item
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_price_history_report(item_id=item_id)

    items = Item.objects.filter(
        tenant=tenant,
        purchase_lines__invoice__status='confirmed',
    ).distinct().order_by('name')

    return render(request, 'purchases/reports/price_history.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'items': items,
        'selected_item_id': item_id,
        'section': 'purchases_reports',
    })


@login_required
@require_permission('view_purchases_price_history_report')
def purchases_price_history_report_export(request):
    import csv
    from django.http import HttpResponse
    from apps.items.models import Item
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    item_id = request.GET.get('item_id') or None

    report = PurchasesReportGenerator(tenant, start_date, end_date).get_price_history_report(item_id=item_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="price_history_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])

    if report.get('item'):
        writer.writerow([f'المنتج: {report["item"]["name"]}'])
        writer.writerow([])
        writer.writerow(['التاريخ', 'رقم الفاتورة', 'المورد', 'الكمية', 'سعر الوحدة', 'الإجمالي'])
        for row in report['data']:
            writer.writerow([row['invoice_date'], row['invoice_number'], row['supplier_name'],
                             row['quantity'], row['unit_cost'], row['line_total']])
    else:
        writer.writerow(['المنتج', 'الوحدة', 'عدد مرات الشراء', 'آخر شراء', 'آخر مورد',
                         'آخر سعر', 'أقل سعر', 'أعلى سعر', 'متوسط السعر', 'فارق السعر'])
        for row in report['data']:
            writer.writerow([row['item_name'], row['unit'], row['purchase_count'],
                             row['last_purchase_date'], row['last_supplier'], row['last_price'],
                             row['min_price'], row['max_price'], row['avg_price'], row['price_variance']])
    return response


# ============================================================
# طلبات عروض الأسعار (Purchase RFQ)
# ============================================================

@login_required
@require_permission('view_purchases')
def rfq_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = PurchaseRFQ.objects.filter(tenant=tenant)
    STATUS_LABELS = dict(PurchaseRFQ.STATUS_CHOICES)
    stats = {
        'total':     qs.count(),
        'draft':     qs.filter(status='draft').count(),
        'sent':      qs.filter(status='sent').count(),
        'received':  qs.filter(status='received').count(),
        'converted': qs.filter(status='converted').count(),
    }
    suppliers = Supplier.objects.for_tenant(tenant).filter(is_active=True)
    return render(request, 'purchases/rfq_list.html', {
        'stats': stats, 'suppliers': suppliers, 'STATUS_LABELS': STATUS_LABELS,
    })


@login_required
@require_permission('view_purchases')
def rfq_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'error': 'no tenant'}, status=400, json_dumps_params={'ensure_ascii': False})

    draw      = int(request.GET.get('draw', 1))
    start     = int(request.GET.get('start', 0))
    length    = int(request.GET.get('length', 25))
    search    = request.GET.get('search[value]', '').strip()
    status_f  = request.GET.get('status', '').strip()

    qs = PurchaseRFQ.objects.filter(tenant=tenant).select_related('supplier', 'stock')
    total = qs.count()

    if status_f:
        qs = qs.filter(status=status_f)
    if search:
        qs = qs.filter(
            Q(rfq_number__icontains=search) |
            Q(supplier__name__icontains=search)
        )

    filtered = qs.count()
    qs = qs[start:start + length]

    STATUS_COLORS = {
        'draft': 'secondary', 'sent': 'info', 'received': 'primary',
        'accepted': 'success', 'rejected': 'danger',
        'converted': 'success', 'cancelled': 'danger',
    }
    STATUS_LABELS = dict(PurchaseRFQ.STATUS_CHOICES)

    rows = []
    for r in qs:
        badge = (
            f'<span class="badge bg-{STATUS_COLORS.get(r.status, "secondary")}">'
            f'{STATUS_LABELS.get(r.status, r.status)}</span>'
        )
        rows.append({
            'DT_RowId': f'row_{r.id}',
            'rfq_number': r.rfq_number,
            'rfq_date': str(r.rfq_date),
            'supplier': r.supplier.name if r.supplier else '—',
            'stock': r.stock.name,
            'grand_total': str(r.grand_total),
            'status_badge': badge,
            'status': r.status,
            'total_lines': r.lines.count(),
            'id': r.id,
        })

    return JsonResponse({'draw': draw, 'recordsTotal': total, 'recordsFiltered': filtered, 'data': rows}, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_purchases')
def rfq_create(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    suppliers = Supplier.objects.for_tenant(tenant).filter(is_active=True)
    stocks    = Stock.objects.for_tenant(tenant).filter(is_active=True)
    items     = Item.objects.for_tenant(tenant).filter(is_active=True).exclude(item_type='service')

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = request.POST.dict()

        supplier_id = data.get('supplier_id')
        stock_id    = data.get('stock_id')
        rfq_date    = data.get('rfq_date') or str(timezone.localdate())
        expiry_date = data.get('expiry_date') or None
        notes       = data.get('notes', '')
        terms       = data.get('terms', '')
        lines_raw   = data.get('lines', [])

        errors = {}
        if not stock_id:
            errors['stock_id'] = ['المخزن مطلوب']
        if not lines_raw:
            errors['lines'] = ['أضف بنداً واحداً على الأقل']
        if errors:
            return JsonResponse({'success': False, 'errors': errors}, status=400, json_dumps_params={'ensure_ascii': False})

        try:
            stock = Stock.objects.for_tenant(tenant).get(pk=stock_id)
        except Stock.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'المخزن غير موجود'}, status=400, json_dumps_params={'ensure_ascii': False})

        supplier = None
        if supplier_id:
            try:
                supplier = Supplier.objects.for_tenant(tenant).get(pk=supplier_id)
            except Supplier.DoesNotExist:
                pass

        with transaction.atomic():
            rfq = PurchaseRFQ.objects.create(
                tenant=tenant, supplier=supplier, stock=stock,
                rfq_date=rfq_date, expiry_date=expiry_date or None,
                notes=notes, terms=terms,
            )
            for ln in lines_raw:
                item_id = ln.get('item_id')
                qty = Decimal(str(ln.get('quantity', 0)))
                if not item_id or qty <= 0:
                    continue
                try:
                    item = Item.objects.for_tenant(tenant).get(pk=item_id)
                except Item.DoesNotExist:
                    continue
                PurchaseRFQLine.objects.create(
                    tenant=tenant, rfq=rfq, item=item,
                    requested_quantity=qty,
                    quoted_price=Decimal('0'),
                    notes=ln.get('notes', ''),
                )

        supplier_name = rfq.supplier.name if rfq.supplier else '—'
        log_activity(request, 'إنشاء طلب عرض سعر (RFQ)',
                     f"الطلب: {rfq.rfq_number}\nالمورد: {supplier_name}\nالمخزن: {rfq.stock.name}", 'create')

        return JsonResponse({'success': True, 'id': rfq.id,
                             'redirect': f'/purchases/rfq/{rfq.id}/'})

    context = {'suppliers': suppliers, 'stocks': stocks, 'items': items,
               'today': str(timezone.localdate())}
    return render(request, 'purchases/rfq_form.html', context)


@login_required
@require_permission('view_purchases')
def rfq_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    rfq = get_object_or_404(
        PurchaseRFQ.objects.select_related('supplier', 'stock')
        .prefetch_related('lines__item__unit'),
        tenant=tenant, pk=pk
    )
    return render(request, 'purchases/rfq_detail.html', {'rfq': rfq})


@login_required
@require_permission('add_purchases')
@require_POST
def rfq_send_ajax(request, pk):
    tenant = _ensure_tenant(request)
    try:
        rfq = PurchaseRFQ.objects.get(tenant=tenant, pk=pk)
        if rfq.status != 'draft':
            return JsonResponse({'success': False, 'message': 'يمكن إرسال المسودات فقط'}, status=400, json_dumps_params={'ensure_ascii': False})
        rfq.status = 'sent'
        rfq.save(update_fields=['status', 'updated_at'])
        log_activity(request, 'إرسال طلب عرض سعر (RFQ)', f'{rfq.rfq_number} — {rfq.supplier.name}', 'other')
        return JsonResponse({'success': True, 'message': 'تم تحديث الحالة إلى مُرسَل'}, json_dumps_params={'ensure_ascii': False})
    except PurchaseRFQ.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الطلب غير موجود'}, status=404, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_purchases')
def rfq_receive_ajax(request, pk):
    """Mark as received and save quoted prices from lines."""
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    tenant = _ensure_tenant(request)
    try:
        rfq = PurchaseRFQ.objects.get(tenant=tenant, pk=pk)
        if rfq.status not in ('sent', 'draft'):
            return JsonResponse({'success': False, 'message': 'الحالة الحالية لا تسمح بهذا الإجراء'}, status=400, json_dumps_params={'ensure_ascii': False})

        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            data = {}

        prices = data.get('prices', {})  # {line_id: quoted_price}
        with transaction.atomic():
            for line_id, price_val in prices.items():
                try:
                    price = Decimal(str(price_val or '0'))
                    line = PurchaseRFQLine.objects.get(pk=int(line_id), rfq=rfq)
                    line.quoted_price = price
                    line.save()
                except (PurchaseRFQLine.DoesNotExist, InvalidOperation):
                    pass
            rfq.status = 'received'
            rfq.save(update_fields=['status', 'updated_at'])
            rfq.recalculate_total()

        log_activity(request, 'استلام عروض الأسعار (RFQ)', f'{rfq.rfq_number} — {rfq.supplier.name}', 'other')
        return JsonResponse({'success': True, 'message': 'تم تسجيل أسعار المورد'}, json_dumps_params={'ensure_ascii': False})
    except PurchaseRFQ.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الطلب غير موجود'}, status=404, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_purchases')
@require_POST
def rfq_accept_ajax(request, pk):
    tenant = _ensure_tenant(request)
    try:
        rfq = PurchaseRFQ.objects.get(tenant=tenant, pk=pk)
        if rfq.status not in ('received', 'sent', 'draft'):
            return JsonResponse({'success': False, 'message': 'الحالة لا تسمح بالقبول'}, status=400, json_dumps_params={'ensure_ascii': False})
        rfq.status = 'accepted'
        rfq.save(update_fields=['status', 'updated_at'])
        log_activity(request, 'قبول عرض الأسعار (RFQ)', f'{rfq.rfq_number} — {rfq.supplier.name}', 'other')
        return JsonResponse({'success': True, 'message': 'تم قبول عرض الأسعار'}, json_dumps_params={'ensure_ascii': False})
    except PurchaseRFQ.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الطلب غير موجود'}, status=404, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_purchases')
@require_POST
def rfq_reject_ajax(request, pk):
    tenant = _ensure_tenant(request)
    try:
        rfq = PurchaseRFQ.objects.get(tenant=tenant, pk=pk)
        if rfq.status in ('converted', 'cancelled'):
            return JsonResponse({'success': False, 'message': 'لا يمكن رفض هذا الطلب'}, status=400, json_dumps_params={'ensure_ascii': False})
        rfq.status = 'rejected'
        rfq.save(update_fields=['status', 'updated_at'])
        log_activity(request, 'رفض عرض الأسعار (RFQ)', f'{rfq.rfq_number} — {rfq.supplier.name}', 'delete')
        return JsonResponse({'success': True, 'message': 'تم رفض عرض الأسعار'}, json_dumps_params={'ensure_ascii': False})
    except PurchaseRFQ.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الطلب غير موجود'}, status=404, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_purchases')
@require_POST
def rfq_cancel_ajax(request, pk):
    tenant = _ensure_tenant(request)
    try:
        rfq = PurchaseRFQ.objects.get(tenant=tenant, pk=pk)
        if rfq.status == 'converted':
            return JsonResponse({'success': False, 'message': 'لا يمكن إلغاء طلب محوَّل'}, status=400, json_dumps_params={'ensure_ascii': False})
        rfq.status = 'cancelled'
        rfq.save(update_fields=['status', 'updated_at'])
        log_activity(request, 'إلغاء طلب عرض سعر (RFQ)', f'{rfq.rfq_number} — {rfq.supplier.name}', 'delete')
        return JsonResponse({'success': True}, json_dumps_params={'ensure_ascii': False})
    except PurchaseRFQ.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الطلب غير موجود'}, status=404, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_purchases')
@require_POST
def rfq_convert_ajax(request, pk):
    """Convert accepted RFQ to a purchase invoice (draft)."""
    tenant = _ensure_tenant(request)
    try:
        rfq = PurchaseRFQ.objects.get(tenant=tenant, pk=pk)
        if rfq.status not in ('accepted', 'received'):
            return JsonResponse({'success': False, 'message': 'يجب قبول عرض الأسعار أولاً'}, status=400, json_dumps_params={'ensure_ascii': False})

        with transaction.atomic():
            invoice = PurchaseInvoice.objects.create(
                tenant=tenant,
                supplier=rfq.supplier,
                stock=rfq.stock,
                invoice_date=timezone.localdate(),
                payment_method='credit',
                status='draft',
                notes=f'محوَّل من {rfq.rfq_number}',
            )
            from apps.purchases.models import PurchaseInvoiceLine
            for ln in rfq.lines.select_related('item'):
                PurchaseInvoiceLine.objects.create(
                    tenant=tenant,
                    invoice=invoice,
                    item=ln.item,
                    quantity=ln.requested_quantity,
                    unit_cost=ln.quoted_price,
                    line_total=ln.line_total,
                )
            invoice.grand_total = rfq.grand_total
            invoice.save(update_fields=['grand_total', 'updated_at'])

            rfq.status = 'converted'
            rfq.converted_invoice = invoice
            rfq.save(update_fields=['status', 'converted_invoice', 'updated_at'])

        log_activity(request, 'تحويل RFQ لأمر شراء', f'{rfq.rfq_number} ← {invoice.invoice_number}', 'create')
        return JsonResponse({'success': True,
                             'redirect': reverse('purchases:order_detail', args=[invoice.id])}, json_dumps_params={'ensure_ascii': False})
    except PurchaseRFQ.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'الطلب غير موجود'}, status=404, json_dumps_params={'ensure_ascii': False})
