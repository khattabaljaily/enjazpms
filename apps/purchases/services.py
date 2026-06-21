from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.sales.models import StockMovement
from apps.stocks.models import StockQuantity
from apps.treasury.services import post_treasury_disbursement, post_treasury_receipt

from .models import (
    PurchaseInvoice,
    PurchaseInvoiceLine,
    PurchasePayment,
    PurchaseReturn,
    PurchaseReturnLine,
    SupplierLedger,
)


def _is_stock_tracked_item(item):
    return getattr(item, 'item_type', None) != 'service'


def _get_stock_qty(tenant, stock, item):
    return (
        StockQuantity.objects.select_for_update()
        .get(tenant=tenant, stock=stock, item=item)
    )


def _add_stock(tenant, stock, item, qty, unit_cost, invoice):
    if not _is_stock_tracked_item(item):
        return

    sq = _get_stock_qty(tenant, stock, item)
    sq.quantity += qty
    sq.save(update_fields=['quantity', 'updated_at'])

    StockMovement.objects.create(
        tenant=tenant,
        item=item,
        stock=stock,
        movement_type='purchase_in',
        direction='in',
        quantity=qty,
        unit_cost=unit_cost,
        movement_date=invoice.invoice_date,
        reference_type='purchase_invoice',
        reference_id=invoice.id,
        balance_after=sq.quantity,
    )


def _reverse_stock_movements(tenant, invoice):
    movements = StockMovement.objects.filter(
        tenant=tenant,
        reference_type='purchase_invoice',
        reference_id=invoice.id,
        movement_type='purchase_in',
        is_reversal=False,
    ).select_related('item', 'stock')

    for mv in movements:
        if not _is_stock_tracked_item(mv.item):
            continue

        sq = _get_stock_qty(tenant, mv.stock, mv.item)
        if sq.quantity < mv.quantity:
            raise ValueError(
                f"لا يمكن إلغاء أمر الشراء: كمية «{mv.item.name}» الحالية أقل من الكمية المراد عكسها."
            )
        sq.quantity -= mv.quantity
        sq.save(update_fields=['quantity', 'updated_at'])
        StockMovement.objects.create(
            tenant=tenant,
            item=mv.item,
            stock=mv.stock,
            movement_type='adjustment_out',
            direction='out',
            quantity=mv.quantity,
            unit_cost=mv.unit_cost,
            balance_after=sq.quantity,
            reference_type=mv.reference_type,
            reference_id=mv.reference_id,
            movement_date=timezone.localdate(),
            notes=f'إلغاء أمر شراء: {mv.notes}' if mv.notes else 'إلغاء أمر شراء',
            is_reversal=True,
        )


def _apply_payment(tenant, invoice, method, amount, date, reference='', notes=''):
    amount = Decimal(str(amount or 0))
    if amount <= 0:
        return None

    payment = PurchasePayment.objects.create(
        tenant=tenant,
        invoice=invoice,
        payment_method=method,
        amount=amount,
        payment_date=date,
        reference_number=reference,
        notes=notes,
    )

    if method == 'cash':
        post_treasury_disbursement(
            tenant=tenant,
            amount=amount,
            date=date,
            reference_type='purchase_payment',
            reference_id=payment.id,
            description=notes or f'دفعة أمر شراء {invoice.invoice_number}',
            user=getattr(invoice, 'updated_by', None) or getattr(invoice, 'created_by', None),
        )

    return payment


def _reverse_payments(tenant, invoice):
    payments = list(invoice.payments.filter(is_reversed=False))
    for payment in payments:
        if payment.payment_method == 'cash' and payment.amount > 0:
            post_treasury_receipt(
                tenant=tenant,
                amount=payment.amount,
                date=timezone.localdate(),
                reference_type='purchase_payment',
                reference_id=payment.id,
                description=f'عكس دفعة أمر شراء {invoice.invoice_number}',
            )

        payment.is_reversed = True
        payment.save(update_fields=['is_reversed', 'updated_at'])


def _apply_supplier_ledger(tenant, supplier, amount, entry_type, reference_type, reference_id, date, notes=''):
    if not supplier:
        return

    from django.db.models import Sum

    prev = (
        SupplierLedger.objects.filter(tenant=tenant, supplier=supplier)
        .aggregate(s=Sum('amount'))['s']
        or Decimal('0')
    )

    SupplierLedger.objects.create(
        tenant=tenant,
        supplier=supplier,
        entry_type=entry_type,
        amount=amount,
        entry_date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        running_balance=prev + amount,
        notes=notes,
    )


def _reverse_supplier_ledger(tenant, reference_type, reference_id):
    from django.db.models import Sum as _Sum
    entries = SupplierLedger.objects.filter(
        tenant=tenant,
        reference_type=reference_type,
        reference_id=reference_id,
        is_reversal=False,
    ).select_related('supplier')
    for entry in entries:
        prev = (
            SupplierLedger.objects
            .filter(tenant=tenant, supplier=entry.supplier)
            .aggregate(s=_Sum('amount'))['s'] or Decimal('0')
        )
        reversed_amount = -entry.amount
        SupplierLedger.objects.create(
            tenant=tenant,
            supplier=entry.supplier,
            entry_type=entry.entry_type,
            amount=reversed_amount,
            entry_date=timezone.localdate(),
            reference_type=entry.reference_type,
            reference_id=entry.reference_id,
            running_balance=prev + reversed_amount,
            notes=f'إلغاء: {entry.notes}' if entry.notes else 'إلغاء قيد',
            is_reversal=True,
        )


def _deduct_stock(tenant, stock, item, qty, unit_cost, reference_type, reference_id, movement_date):
    if not _is_stock_tracked_item(item):
        return

    sq = _get_stock_qty(tenant, stock, item)
    if sq.quantity < qty:
        raise ValueError(
            f"لا يمكن خصم المرتجع: الكمية الحالية لـ «{item.name}» هي {sq.quantity} والمطلوب {qty}."
        )

    sq.quantity -= qty
    sq.save(update_fields=['quantity', 'updated_at'])

    StockMovement.objects.create(
        tenant=tenant,
        item=item,
        stock=stock,
        movement_type='purchase_return_out',
        direction='out',
        quantity=qty,
        unit_cost=unit_cost,
        movement_date=movement_date,
        reference_type=reference_type,
        reference_id=reference_id,
        balance_after=sq.quantity,
    )


@transaction.atomic
def confirm_purchase_invoice(invoice: PurchaseInvoice, user) -> PurchaseInvoice:
    if invoice.status != 'draft':
        raise ValueError(f"لا يمكن تأكيد أمر شراء بحالة «{invoice.get_status_display()}»." )

    tenant = invoice.tenant
    lines = list(invoice.lines.select_related('item'))
    if not lines:
        raise ValueError('لا يمكن تأكيد أمر شراء فارغ.')

    from apps.items.models import ItemBatch
    for line in lines:
        qty_base = (line.quantity * (line.unit_factor or Decimal('1'))).quantize(Decimal('0.0001'))
        _add_stock(
            tenant=tenant,
            stock=invoice.stock,
            item=line.item,
            qty=qty_base,
            unit_cost=line.unit_cost,
            invoice=invoice,
        )
        if line.batch_number or line.expiry_date:
            ItemBatch.objects.create(
                tenant=tenant,
                item=line.item,
                stock=invoice.stock,
                batch_number=line.batch_number or '',
                expiry_date=line.expiry_date,
                quantity_received=qty_base,
                quantity_remaining=qty_base,
                purchase_date=invoice.invoice_date,
            )

    total = invoice.grand_total
    pm = invoice.payment_method
    bank_reference = (invoice.bank_reference or '').strip()

    if pm not in {'cash', 'bank', 'credit', 'mixed'}:
        raise ValueError('طريقة الدفع غير مدعومة.')

    if pm == 'credit':
        if not invoice.supplier:
            raise ValueError('أمر الشراء الآجل يتطلب اختيار مورد.')

    # ── فحص الحد الائتماني للمورد ──────────────────────────────
    if invoice.supplier and pm in ('credit', 'mixed'):
        from django.db.models import Sum as _CLSum
        supplier = invoice.supplier
        credit_limit = supplier.credit_limit or Decimal('0')
        if credit_limit > 0:
            current_balance = (
                SupplierLedger.objects
                .filter(tenant=tenant, supplier=supplier)
                .aggregate(s=_CLSum('amount'))['s'] or Decimal('0')
            ) + (supplier.opening_balance or Decimal('0'))

            if pm == 'credit':
                credit_amount = total
            else:
                cash_amt = invoice.cash_amount or Decimal('0')
                bank_amt = invoice.bank_amount or Decimal('0')
                credit_amount = max(total - cash_amt - bank_amt, Decimal('0'))

            if credit_amount > 0 and (current_balance + credit_amount) > credit_limit:
                available = max(credit_limit - current_balance, Decimal('0'))
                raise ValueError(
                    f'تجاوز الحد الائتماني للمورد «{supplier.name}». '
                    f'الحد: {credit_limit:,.2f} | '
                    f'المديونية الحالية: {current_balance:,.2f} | '
                    f'المتاح: {available:,.2f}'
                )

    if pm == 'credit':
        _apply_supplier_ledger(
            tenant=tenant,
            supplier=invoice.supplier,
            amount=total,
            entry_type='invoice',
            reference_type='purchase_invoice',
            reference_id=invoice.id,
            date=invoice.invoice_date,
            notes=f'أمر شراء {invoice.invoice_number}',
        )
    elif pm == 'cash':
        _apply_payment(tenant, invoice, 'cash', total, invoice.invoice_date)
        if invoice.supplier:
            _apply_supplier_ledger(
                tenant=tenant, supplier=invoice.supplier, amount=total,
                entry_type='invoice', reference_type='purchase_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f'أمر شراء {invoice.invoice_number}',
            )
            _apply_supplier_ledger(
                tenant=tenant, supplier=invoice.supplier, amount=-total,
                entry_type='payment', reference_type='purchase_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f'سداد نقدي — {invoice.invoice_number}',
            )
    elif pm == 'bank':
        if not bank_reference:
            raise ValueError('يرجى إدخال مرجع التحويل البنكي.')
        _apply_payment(tenant, invoice, 'bank', total, invoice.invoice_date, reference=bank_reference)
        if invoice.supplier:
            _apply_supplier_ledger(
                tenant=tenant, supplier=invoice.supplier, amount=total,
                entry_type='invoice', reference_type='purchase_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f'أمر شراء {invoice.invoice_number}',
            )
            _apply_supplier_ledger(
                tenant=tenant, supplier=invoice.supplier, amount=-total,
                entry_type='payment', reference_type='purchase_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f'سداد بنكي — {invoice.invoice_number} ({bank_reference})',
            )
    elif pm == 'mixed':
        cash_amt = invoice.cash_amount or Decimal('0')
        bank_amt = invoice.bank_amount or Decimal('0')
        if cash_amt < 0 or bank_amt < 0:
            raise ValueError('مبالغ الدفع المختلط لا يمكن أن تكون سالبة.')

        paid_part = cash_amt + bank_amt
        if paid_part > (total + Decimal('0.005')):
            raise ValueError('مجموع النقدي والبنكي لا يمكن أن يتجاوز إجمالي أمر الشراء.')

        credit_amt = total - paid_part
        if credit_amt < Decimal('0'):
            credit_amt = Decimal('0')

        if credit_amt > Decimal('0.005') and not invoice.supplier:
            raise ValueError('الجزء الآجل في الدفع المختلط يتطلب اختيار مورد.')
        if bank_amt > 0 and not bank_reference:
            raise ValueError('يرجى إدخال مرجع التحويل البنكي للجزء البنكي.')

        if cash_amt > 0:
            _apply_payment(tenant, invoice, 'cash', cash_amt, invoice.invoice_date)
        if bank_amt > 0:
            _apply_payment(tenant, invoice, 'bank', bank_amt, invoice.invoice_date, reference=bank_reference)

        if invoice.supplier:
            _apply_supplier_ledger(
                tenant=tenant, supplier=invoice.supplier, amount=total,
                entry_type='invoice', reference_type='purchase_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f'أمر شراء {invoice.invoice_number}',
            )
            if cash_amt > 0:
                _apply_supplier_ledger(
                    tenant=tenant, supplier=invoice.supplier, amount=-cash_amt,
                    entry_type='payment', reference_type='purchase_invoice',
                    reference_id=invoice.id, date=invoice.invoice_date,
                    notes=f'سداد نقدي — {invoice.invoice_number}',
                )
            if bank_amt > 0:
                _apply_supplier_ledger(
                    tenant=tenant, supplier=invoice.supplier, amount=-bank_amt,
                    entry_type='payment', reference_type='purchase_invoice',
                    reference_id=invoice.id, date=invoice.invoice_date,
                    notes=f'سداد بنكي — {invoice.invoice_number} ({bank_reference})',
                )

    invoice.status = 'confirmed'
    try:
        invoice.confirmed_by = user
        invoice.save(update_fields=['status', 'confirmed_by', 'updated_at'])
    except Exception:
        invoice.save(update_fields=['status', 'updated_at'])
    return invoice


@transaction.atomic
def cancel_purchase_invoice(invoice: PurchaseInvoice, user, reason='') -> PurchaseInvoice:
    if invoice.status != 'confirmed':
        raise ValueError('يمكن إلغاء أوامر الشراء المؤكدة فقط. لا يمكن إلغاء أمر عليه مرتجعات.')

    tenant = invoice.tenant
    _reverse_stock_movements(tenant, invoice)
    _reverse_payments(tenant, invoice)
    _reverse_supplier_ledger(tenant, 'purchase_invoice', invoice.id)

    invoice.status = 'cancelled'
    invoice.cancellation_reason = reason
    invoice.cancelled_at = timezone.now()
    try:
        invoice.cancelled_by = user
        invoice.save(update_fields=['status', 'cancellation_reason', 'cancelled_at', 'cancelled_by', 'updated_at'])
    except Exception:
        invoice.save(update_fields=['status', 'updated_at'])
    return invoice


@transaction.atomic
def confirm_purchase_return(purchase_return: PurchaseReturn, user) -> PurchaseReturn:
    if purchase_return.status != 'draft':
        raise ValueError(f"لا يمكن تأكيد مرتجع بحالة «{purchase_return.get_status_display()}».")

    invoice = purchase_return.original_invoice
    if invoice.status not in ('confirmed', 'partially_returned'):
        raise ValueError('يمكن إرجاع أوامر الشراء المؤكدة فقط.')

    tenant = purchase_return.tenant
    lines = list(purchase_return.lines.select_related('invoice_line', 'item'))
    if not lines:
        raise ValueError('لا يمكن تأكيد مرتجع فارغ.')

    total = Decimal('0')
    for rl in lines:
        inv_line = PurchaseInvoiceLine.objects.select_for_update().get(pk=rl.invoice_line_id)
        if rl.returned_quantity > inv_line.returnable_quantity:
            raise ValueError(
                f"الكمية القابلة للإرجاع لـ «{rl.item.name}» هي {inv_line.returnable_quantity} والمطلوب {rl.returned_quantity}."
            )

        _deduct_stock(
            tenant=tenant,
            stock=invoice.stock,
            item=rl.item,
            qty=rl.returned_quantity,
            unit_cost=inv_line.unit_cost,
            reference_type='purchase_return',
            reference_id=purchase_return.id,
            movement_date=purchase_return.return_date,
        )

        inv_line.returned_quantity += rl.returned_quantity
        inv_line.save(update_fields=['returned_quantity', 'updated_at'])

        rl.line_total = (rl.returned_quantity * rl.unit_cost).quantize(Decimal('0.01'))
        rl.save(update_fields=['line_total', 'updated_at'])
        total += rl.line_total

    purchase_return.total_returned = total
    purchase_return.save(update_fields=['total_returned', 'updated_at'])

    if purchase_return.refund_method == 'cash':
        post_treasury_receipt(
            tenant=tenant,
            amount=total,
            date=purchase_return.return_date,
            reference_type='purchase_return',
            reference_id=purchase_return.id,
            description=f'استلام مرتجع شراء {purchase_return.return_number}',
            user=user,
        )

    if invoice.supplier:
        _apply_supplier_ledger(
            tenant=tenant,
            supplier=invoice.supplier,
            amount=-total,
            entry_type='return',
            reference_type='purchase_return',
            reference_id=purchase_return.id,
            date=purchase_return.return_date,
            notes=f'مرتجع شراء {purchase_return.return_number}',
        )

    all_lines = invoice.lines.all()
    all_returned = all((l.returned_quantity or Decimal('0')) >= (l.quantity or Decimal('0')) for l in all_lines)
    invoice.status = 'returned' if all_returned else 'partially_returned'
    invoice.save(update_fields=['status', 'updated_at'])

    purchase_return.status = 'confirmed'
    try:
        purchase_return.confirmed_by = user
        purchase_return.save(update_fields=['status', 'confirmed_by', 'updated_at'])
    except Exception:
        purchase_return.save(update_fields=['status', 'updated_at'])
    return purchase_return


@transaction.atomic
def cancel_purchase_return(purchase_return: PurchaseReturn, user) -> PurchaseReturn:
    if purchase_return.status != 'confirmed':
        raise ValueError('يمكن إلغاء المرتجع المؤكد فقط.')

    tenant = purchase_return.tenant
    invoice = purchase_return.original_invoice

    movements = StockMovement.objects.filter(
        tenant=tenant,
        reference_type='purchase_return',
        reference_id=purchase_return.id,
        movement_type='purchase_return_out',
        is_reversal=False,
    ).select_related('item', 'stock')

    for mv in movements:
        if not _is_stock_tracked_item(mv.item):
            continue
        sq = _get_stock_qty(tenant, mv.stock, mv.item)
        sq.quantity += mv.quantity
        sq.save(update_fields=['quantity', 'updated_at'])
        StockMovement.objects.create(
            tenant=tenant,
            item=mv.item,
            stock=mv.stock,
            movement_type='adjustment_in',
            direction='in',
            quantity=mv.quantity,
            unit_cost=mv.unit_cost,
            balance_after=sq.quantity,
            reference_type=mv.reference_type,
            reference_id=mv.reference_id,
            movement_date=timezone.localdate(),
            notes=f'إلغاء مرتجع شراء: {mv.notes}' if mv.notes else 'إلغاء مرتجع شراء',
            is_reversal=True,
        )

    for rl in purchase_return.lines.select_related('invoice_line'):
        inv_line = PurchaseInvoiceLine.objects.select_for_update().get(pk=rl.invoice_line_id)
        inv_line.returned_quantity -= rl.returned_quantity
        if inv_line.returned_quantity < 0:
            inv_line.returned_quantity = Decimal('0')
        inv_line.save(update_fields=['returned_quantity', 'updated_at'])

    if purchase_return.refund_method == 'cash' and purchase_return.total_returned > 0:
        post_treasury_disbursement(
            tenant=tenant,
            amount=purchase_return.total_returned,
            date=timezone.localdate(),
            reference_type='purchase_return',
            reference_id=purchase_return.id,
            description=f'عكس استلام مرتجع شراء {purchase_return.return_number}',
            user=user,
        )

    _reverse_supplier_ledger(tenant, 'purchase_return', purchase_return.id)

    if invoice.status in ('returned', 'partially_returned'):
        invoice.status = 'confirmed'
        invoice.save(update_fields=['status', 'updated_at'])

    purchase_return.status = 'cancelled'
    purchase_return.save(update_fields=['status', 'updated_at'])
    return purchase_return


@transaction.atomic
def edit_confirmed_purchase_invoice(invoice: PurchaseInvoice, header_data: dict, lines_data: list, user) -> PurchaseInvoice:
    if invoice.status != 'confirmed':
        raise ValueError('تعديل هذا الأمر مسموح للحالة المؤكدة فقط.')

    tenant = invoice.tenant

    _reverse_stock_movements(tenant, invoice)
    _reverse_payments(tenant, invoice)
    _reverse_supplier_ledger(tenant, 'purchase_invoice', invoice.id)

    allowed = {
        'supplier', 'stock', 'invoice_date', 'payment_method',
        'cash_amount', 'bank_amount', 'bank_reference', 'notes',
    }
    for field, value in header_data.items():
        if field in allowed:
            setattr(invoice, field, value)

    invoice.lines.all().delete()

    from apps.items.models import Item

    new_lines = []
    for ld in lines_data:
        item = Item.objects.get(id=ld['item_id'], tenant=tenant)
        line = PurchaseInvoiceLine(
            tenant=tenant,
            invoice=invoice,
            item=item,
            quantity=Decimal(str(ld['quantity'])),
            unit_cost=Decimal(str(ld['unit_cost'])),
            tax_rate=Decimal(str(ld.get('tax_rate', 0))),
            batch_number=ld.get('batch_number', '') or '',
            serial_number=ld.get('serial_number', '') or '',
            expiry_date=ld.get('expiry_date') or None,
        )
        line.calculate()
        new_lines.append(line)

    PurchaseInvoiceLine.objects.bulk_create(new_lines)
    invoice.recalculate_totals()
    invoice.status = 'draft'
    invoice.save()

    confirm_purchase_invoice(invoice, user)
    return invoice


def build_purchase_from_post(tenant, stock, data: dict, lines_data: list, user) -> PurchaseInvoice:
    from apps.items.models import Item
    from apps.suppliers.models import Supplier

    supplier = None
    if data.get('supplier_id'):
        supplier = Supplier.objects.get(id=data['supplier_id'], tenant=tenant)

    invoice = PurchaseInvoice.objects.create(
        tenant=tenant,
        supplier=supplier,
        stock=stock,
        invoice_date=data['invoice_date'],
        payment_method=data.get('payment_method', 'cash'),
        cash_amount=Decimal(str(data.get('cash_amount', 0))),
        bank_amount=Decimal(str(data.get('bank_amount', 0))),
        bank_reference=data.get('bank_reference', ''),
        notes=data.get('notes', ''),
        status='draft',
        created_by=user,
        updated_by=user,
    )

    from apps.items.models import Unit as ItemUnit
    for ld in lines_data:
        item = Item.objects.get(id=ld['item_id'], tenant=tenant)
        unit_obj = None
        if ld.get('unit_id'):
            unit_obj = ItemUnit.objects.filter(pk=ld['unit_id'], tenant=tenant).first()
        line = PurchaseInvoiceLine(
            tenant=tenant,
            invoice=invoice,
            item=item,
            quantity=Decimal(str(ld['quantity'])),
            unit_cost=Decimal(str(ld['unit_cost'])),
            tax_rate=Decimal(str(ld.get('tax_rate', 0))),
            unit=unit_obj,
            unit_factor=Decimal(str(ld.get('unit_factor', 1) or 1)),
            batch_number=ld.get('batch_number', '') or '',
            serial_number=ld.get('serial_number', '') or '',
            expiry_date=ld.get('expiry_date') or None,
            created_by=user,
            updated_by=user,
        )
        line.calculate()
        line.save()

    invoice.recalculate_totals()
    invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
    return invoice
