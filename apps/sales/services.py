"""
وحدة المبيعات — منطق الأعمال (Services)
=========================================

هذا الملف هو المكان الوحيد الذي يُعدِّل:
  - StockQuantity   (كميات المخزون)
  - StockMovement   (سجل الحركات)
  - SalePayment     (دفعات السداد)
  - CustomerLedger  (سجل حسابات العملاء)

كل دالة رئيسية تعمل بـ @transaction.atomic لضمان الاتساق.

الدوال الرئيسية:
  confirm_sale_invoice(invoice, user)
      تأكيد الفاتورة: خصم المخزون + تسجيل الدفعة أو المطالبة

  cancel_sale_invoice(invoice, user, reason)
      إلغاء الفاتورة: عكس كل التأثيرات

  edit_confirmed_invoice(invoice, lines_data, header_data, user)
      تعديل فاتورة مؤكدة بأمان (عكس + إعادة تطبيق)

  confirm_sale_return(sale_return, user)
      تأكيد المرتجع: إعادة المخزون + عكس المطالبة أو رد النقد

  cancel_sale_return(sale_return, user)
      إلغاء مرتجع مؤكد

  record_customer_payment(invoice, amount, method, date, reference, user)
      تسجيل دفعة جديدة من العميل على فاتورة آجلة
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.stocks.models import StockQuantity
from apps.treasury.models import TreasuryMovement
from apps.treasury.services import post_treasury_disbursement, post_treasury_receipt

from .models import (
    CustomerLedger,
    SaleInvoice,
    SaleInvoiceLine,
    SalePayment,
    SaleReturn,
    SaleReturnLine,
    StockMovement,
)


# ─────────────────────────────────────────────
#   HELPERS (داخلية)
# ─────────────────────────────────────────────

def _get_stock_qty(tenant, stock, item):
    """
    يجلب سجل StockQuantity مع قفل للتعديل (SELECT FOR UPDATE).
    يُفرز استثناء إذا لم يكن موجوداً.
    """
    return (
        StockQuantity.objects
        .select_for_update()
        .get(tenant=tenant, stock=stock, item=item)
    )


def _fmt_decimal(value):
    """Return Decimal as compact string without trailing zeros."""
    if value is None:
        return '0'
    try:
        text = format(Decimal(value), 'f')
    except Exception:
        text = str(value)
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _is_stock_tracked_item(item):
    """الخدمة لا تُتبع كمخزون ولا تُنشأ لها حركات صنف."""
    return getattr(item, 'item_type', None) != 'service'


def _reserve_stock(tenant, stock, item, qty):
    """يزيد reserved_quantity بدون خصم فعلي من الكمية. يتحقق من الكمية المتاحة."""
    if not _is_stock_tracked_item(item):
        return
    sq = _get_stock_qty(tenant, stock, item)
    if sq.available_quantity < qty:
        raise ValueError(
            f"الكمية المتاحة لـ «{item.name}» في «{stock.name}» "
            f"هي {_fmt_decimal(sq.available_quantity)} فقط، والمطلوب {_fmt_decimal(qty)}."
        )
    sq.reserved_quantity += qty
    sq.save(update_fields=['reserved_quantity', 'updated_at'])


def _release_reservation(tenant, stock, item, qty):
    """يحرر حجز سابق (ينقص reserved_quantity)."""
    if not _is_stock_tracked_item(item):
        return
    sq = _get_stock_qty(tenant, stock, item)
    sq.reserved_quantity = max(Decimal('0'), sq.reserved_quantity - qty)
    sq.save(update_fields=['reserved_quantity', 'updated_at'])


def _deduct_stock(tenant, stock, item, qty, unit_cost, invoice):
    """
    يخصم qty من المخزون ويُسجِّل حركة sale_out.
    يُفرز ValueError إذا كانت الكمية غير كافية.
    """
    if not _is_stock_tracked_item(item):
        return

    sq = _get_stock_qty(tenant, stock, item)
    if sq.available_quantity < qty:
        raise ValueError(
            f"الكمية المتاحة لـ «{item.name}» في «{stock.name}» "
            f"هي {_fmt_decimal(sq.available_quantity)} فقط، والمطلوب {_fmt_decimal(qty)}."
        )
    sq.quantity -= qty
    sq.save(update_fields=['quantity', 'updated_at'])

    StockMovement.objects.create(
        tenant=tenant,
        item=item,
        stock=stock,
        movement_type='sale_out',
        direction='out',
        quantity=qty,
        unit_cost=unit_cost,
        movement_date=invoice.invoice_date,
        reference_type='sale_invoice',
        reference_id=invoice.id,
        balance_after=sq.quantity,
        notes=f'بيع — فاتورة {invoice.invoice_number}',
    )


def _restore_stock(tenant, stock, item, qty, unit_cost, reference_type, reference_id,
                   movement_date, movement_type='sale_return_in', notes=''):
    """
    يُعيد qty إلى المخزون ويُسجِّل حركة دخول.
    """
    if not _is_stock_tracked_item(item):
        return

    sq = _get_stock_qty(tenant, stock, item)
    sq.quantity += qty
    sq.save(update_fields=['quantity', 'updated_at'])

    StockMovement.objects.create(
        tenant=tenant,
        item=item,
        stock=stock,
        movement_type=movement_type,
        direction='in',
        quantity=qty,
        unit_cost=unit_cost,
        movement_date=movement_date,
        reference_type=reference_type,
        reference_id=reference_id,
        balance_after=sq.quantity,
        notes=notes,
    )


def _apply_payment(tenant, invoice, method, amount, date, reference='', notes='', treasury=None):
    """
    يُنشئ SalePayment ويُحدِّث paid_amount في الفاتورة.
    """
    amount = Decimal(str(amount or 0))
    if amount == 0:
        return
    payment = SalePayment.objects.create(
        tenant=tenant,
        invoice=invoice,
        payment_method=method,
        amount=amount,
        payment_date=date,
        reference_number=reference,
        notes=notes,
    )
    invoice.paid_amount = (invoice.paid_amount or Decimal('0')) + amount
    invoice.save(update_fields=['paid_amount', 'updated_at'])

    if invoice.agent_id:
        from apps.agents.services import apply_collection_commission
        apply_collection_commission(tenant, invoice, payment)

    if method == 'cash':
        treasury_notes = notes or f'فاتورة {invoice.invoice_number}'
        if amount > 0:
            post_treasury_receipt(
                tenant=tenant,
                amount=amount,
                date=date,
                reference_type='sale_payment',
                reference_id=payment.id,
                description=treasury_notes,
                user=getattr(invoice, 'confirmed_by', None) or getattr(invoice, 'updated_by', None) or getattr(invoice, 'created_by', None),
                treasury=treasury,
            )
        else:
            post_treasury_disbursement(
                tenant=tenant,
                amount=abs(amount),
                date=date,
                reference_type='sale_payment',
                reference_id=payment.id,
                description=treasury_notes,
                user=getattr(invoice, 'confirmed_by', None) or getattr(invoice, 'updated_by', None) or getattr(invoice, 'created_by', None),
                treasury=treasury,
            )

    return payment


def _apply_customer_ledger(tenant, customer, amount, entry_type,
                           reference_type, reference_id, date, notes='',
                           hc_amount=None, hc_currency='', hc_exchange_rate=None):
    """
    يُنشئ قيداً في CustomerLedger.
    amount موجب = مطالبة على العميل.
    amount سالب = رصيد دائن للعميل.
    hc_amount يُملأ فقط عندما HC mode مفعّل.
    """
    if not customer:
        return

    from django.db.models import Sum as _Sum
    prev = (
        CustomerLedger.objects
        .filter(tenant=tenant, customer=customer)
        .aggregate(s=_Sum('amount'))['s'] or Decimal('0')
    )

    hc_prev = None
    hc_run = None
    if hc_amount is not None:
        hc_prev = (
            CustomerLedger.objects
            .filter(tenant=tenant, customer=customer, hc_amount__isnull=False)
            .aggregate(s=_Sum('hc_amount'))['s'] or Decimal('0')
        )
        hc_run = hc_prev + hc_amount

    entry = CustomerLedger.objects.create(
        tenant=tenant,
        customer=customer,
        entry_type=entry_type,
        amount=amount,
        entry_date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        running_balance=prev + amount,
        notes=notes,
        hc_amount=hc_amount,
        hc_currency=hc_currency or '',
        hc_exchange_rate=hc_exchange_rate,
        hc_running_balance=hc_run,
    )
    return entry


def _reverse_stock_movements(tenant, invoice):
    """
    يعكس كل حركات sale_out المرتبطة بالفاتورة:
      - يُعيد الكمية إلى StockQuantity
      - يُسجّل حركة عكسية (is_reversal=True) بدلاً من الحذف
    """
    movements = StockMovement.objects.filter(
        tenant=tenant,
        reference_type='sale_invoice',
        reference_id=invoice.id,
        movement_type='sale_out',
        is_reversal=False,
    ).select_related('item', 'stock')

    for mv in movements:
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
            notes=f'إلغاء فاتورة بيع: {mv.notes}' if mv.notes else 'إلغاء فاتورة بيع',
            is_reversal=True,
        )


def _reverse_single_payment(tenant, invoice, payment):
    """
    يعكس دفعة واحدة بشكل كامل ومتّسق (تُستخدم من _reverse_payments ومن
    إلغاء دفعة فردية من شاشة دفعيات العملاء):
      1. يعكس حركة الخزينة على نفس الخزينة الأصلية (وليس الافتراضية).
      2. يعكس عمولة تحصيل المندوب المرتبطة بهذه الدفعة تحديداً.
      3. يعكس قيد CustomerLedger المرتبط بهذه الدفعة (يُبقي رصيد العميل متّسقاً).
      4. ينقص paid_amount ويُعلّم الدفعة كمعكوسة.
    """
    from apps.agents.services import _reverse_agent_ledger

    if payment.payment_method == 'cash':
        reverse_notes = f"عكس حركة دفعة {invoice.invoice_number}"
        original_movement = TreasuryMovement.objects.filter(
            tenant=tenant, reference_type='sale_payment', reference_id=payment.id,
        ).first()
        movement_treasury = original_movement.treasury if original_movement else None
        if payment.amount > 0:
            post_treasury_disbursement(
                tenant=tenant,
                amount=abs(payment.amount),
                date=timezone.localdate(),
                reference_type='sale_payment',
                reference_id=payment.id,
                description=reverse_notes,
                treasury=movement_treasury,
            )
        elif payment.amount < 0:
            post_treasury_receipt(
                tenant=tenant,
                amount=abs(payment.amount),
                date=timezone.localdate(),
                reference_type='sale_payment',
                reference_id=payment.id,
                description=reverse_notes,
                treasury=movement_treasury,
            )

    if invoice.agent_id:
        _reverse_agent_ledger(tenant, 'sale_payment', payment.id)
        _reverse_agent_ledger(tenant, 'sale_invoice_collection', invoice.id)

    if invoice.customer_id:
        ledger_reference_type = 'customer_payment_bank' if payment.payment_method == 'bank' else 'customer_payment_cash'
        _reverse_customer_ledger(tenant, ledger_reference_type, payment.id)

    invoice.paid_amount = max(Decimal('0'), (invoice.paid_amount or Decimal('0')) - payment.amount)
    invoice.save(update_fields=['paid_amount', 'updated_at'])

    payment.is_reversed = True
    payment.save(update_fields=['is_reversed', 'updated_at'])


def _reverse_payments(tenant, invoice):
    """يعكس كل SalePayments النشطة المرتبطة بالفاتورة بالكامل (خزينة + عمولة + قيد عميل)."""
    active_payments = list(invoice.payments.filter(is_reversed=False))
    for payment in active_payments:
        _reverse_single_payment(tenant, invoice, payment)
    invoice.paid_amount = Decimal('0')
    invoice.save(update_fields=['paid_amount', 'updated_at'])


def _reverse_customer_ledger(tenant, reference_type, reference_id):
    """يُسجّل قيوداً عكسية في CustomerLedger بدلاً من الحذف."""
    from django.db.models import Sum as _Sum
    entries = CustomerLedger.objects.filter(
        tenant=tenant,
        reference_type=reference_type,
        reference_id=reference_id,
        is_reversal=False,
    ).select_related('customer')
    for entry in entries:
        prev = (
            CustomerLedger.objects
            .filter(tenant=tenant, customer=entry.customer)
            .aggregate(s=_Sum('amount'))['s'] or Decimal('0')
        )
        reversed_amount = -entry.amount
        CustomerLedger.objects.create(
            tenant=tenant,
            customer=entry.customer,
            entry_type=entry.entry_type,
            amount=reversed_amount,
            entry_date=timezone.localdate(),
            reference_type=entry.reference_type,
            reference_id=entry.reference_id,
            running_balance=prev + reversed_amount,
            notes=f'إلغاء: {entry.notes}' if entry.notes else 'إلغاء قيد',
            is_reversal=True,
        )


# ─────────────────────────────────────────────
#   CONFIRM SALE INVOICE  (تأكيد الفاتورة)
# ─────────────────────────────────────────────

@transaction.atomic
def confirm_sale_invoice(invoice: SaleInvoice, user) -> SaleInvoice:
    """
    تأكيد فاتورة مبيعات مسودة.

    الخطوات:
      1. التحقق من الحالة (draft فقط)
      2. لكل بند: خصم الكمية من StockQuantity + تسجيل StockMovement(sale_out)
      3. تسجيل الدفع حسب payment_method:
           cash   → SalePayment(cash, grand_total)
           bank   → SalePayment(bank, grand_total)
           credit → CustomerLedger(invoice, +grand_total)
           mixed  → SalePayment(cash, cash_amount) + SalePayment(bank, bank_amount)
                    + CustomerLedger(invoice, +remaining) إذا وُجد
      4. تحديث الحالة إلى confirmed
    """
    if invoice.status != 'draft':
        raise ValueError(
            f"لا يمكن تأكيد فاتورة بحالة «{invoice.get_status_display()}»."
        )

    tenant = invoice.tenant
    lines = list(invoice.lines.select_related('item'))

    if not lines:
        raise ValueError("لا يمكن تأكيد فاتورة فارغة (لا توجد بنود).")

    # ── 1. تأثير المخزون ────────────────────────────────
    deferred = invoice.delivery_type == 'deferred'
    for line in lines:
        qty_base = (line.quantity * (line.unit_factor or Decimal('1'))).quantize(Decimal('0.0001'))
        if deferred:
            _reserve_stock(
                tenant=tenant, stock=invoice.stock,
                item=line.item, qty=qty_base,
            )
        else:
            _deduct_stock(
                tenant=tenant, stock=invoice.stock,
                item=line.item, qty=qty_base,
                unit_cost=line.cost_price_snapshot,
                invoice=invoice,
            )

    # ── 2. تسجيل الدفع ──────────────────────────────────
    pm = invoice.payment_method
    total = invoice.grand_total

    if pm == 'credit' and not invoice.customer:
        raise ValueError('الفاتورة الآجلة تتطلب اختيار عميل قبل التأكيد.')

    # ── فحص الحد الائتماني ──────────────────────────────
    if invoice.customer and pm in ('credit', 'mixed'):
        customer = invoice.customer
        credit_limit = customer.credit_limit or Decimal('0')
        if credit_limit > 0:
            from django.db.models import Sum as _CLSum
            current_balance = (
                CustomerLedger.objects
                .filter(tenant=tenant, customer=customer)
                .aggregate(s=_CLSum('amount'))['s'] or Decimal('0')
            )
            if pm == 'credit':
                credit_amount = total
            else:
                cash_amt = invoice.cash_amount or Decimal('0')
                bank_amt = invoice.bank_amount or Decimal('0')
                credit_amount = max(total - cash_amt - bank_amt, Decimal('0'))

            if credit_amount > 0 and (current_balance + credit_amount) > credit_limit:
                available = credit_limit - current_balance
                raise ValueError(
                    f'تجاوز الحد الائتماني للعميل «{customer.name}». '
                    f'الحد: {credit_limit:,.2f} | '
                    f'المديونية الحالية: {current_balance:,.2f} | '
                    f'المتاح: {max(available, Decimal("0")):,.2f}'
                )

    if pm == 'cash':
        _apply_payment(tenant, invoice, 'cash', total, invoice.invoice_date)
        if invoice.customer:
            _apply_customer_ledger(
                tenant=tenant, customer=invoice.customer, amount=total,
                entry_type='invoice', reference_type='sale_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f"فاتورة {invoice.invoice_number}",
            )
            _apply_customer_ledger(
                tenant=tenant, customer=invoice.customer, amount=-total,
                entry_type='payment', reference_type='sale_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f"سداد نقدي — {invoice.invoice_number}",
            )

    elif pm == 'bank':
        _apply_payment(tenant, invoice, 'bank', total, invoice.invoice_date,
                       reference=invoice.bank_reference)
        if invoice.customer:
            _apply_customer_ledger(
                tenant=tenant, customer=invoice.customer, amount=total,
                entry_type='invoice', reference_type='sale_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f"فاتورة {invoice.invoice_number}",
            )
            _apply_customer_ledger(
                tenant=tenant, customer=invoice.customer, amount=-total,
                entry_type='payment', reference_type='sale_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f"سداد بنكي — {invoice.invoice_number}",
            )

    elif pm == 'credit':
        _apply_customer_ledger(
            tenant=tenant,
            customer=invoice.customer,
            amount=total,
            entry_type='invoice',
            reference_type='sale_invoice',
            reference_id=invoice.id,
            date=invoice.invoice_date,
            notes=f"فاتورة {invoice.invoice_number}",
        )

    elif pm == 'mixed':
        cash_amt = invoice.cash_amount or Decimal('0')
        bank_amt = invoice.bank_amount or Decimal('0')
        credit_amt = total - cash_amt - bank_amt

        if credit_amt > Decimal('0.005') and not invoice.customer:
            raise ValueError('الفاتورة المختلطة التي تحتوي على جزء آجل تتطلب اختيار عميل قبل التأكيد.')

        if cash_amt > 0:
            _apply_payment(tenant, invoice, 'cash', cash_amt, invoice.invoice_date)
        if bank_amt > 0:
            _apply_payment(tenant, invoice, 'bank', bank_amt, invoice.invoice_date,
                           reference=invoice.bank_reference)
        if invoice.customer:
            _apply_customer_ledger(
                tenant=tenant, customer=invoice.customer, amount=total,
                entry_type='invoice', reference_type='sale_invoice',
                reference_id=invoice.id, date=invoice.invoice_date,
                notes=f"فاتورة {invoice.invoice_number}",
            )
            if cash_amt > 0:
                _apply_customer_ledger(
                    tenant=tenant, customer=invoice.customer, amount=-cash_amt,
                    entry_type='payment', reference_type='sale_invoice',
                    reference_id=invoice.id, date=invoice.invoice_date,
                    notes=f"سداد نقدي — {invoice.invoice_number}",
                )
            if bank_amt > 0:
                _apply_customer_ledger(
                    tenant=tenant, customer=invoice.customer, amount=-bank_amt,
                    entry_type='payment', reference_type='sale_invoice',
                    reference_id=invoice.id, date=invoice.invoice_date,
                    notes=f"سداد بنكي — {invoice.invoice_number}",
                )

    # ── 3. تحديث الحالة ─────────────────────────────────
    invoice.status = 'pending_delivery' if deferred else 'confirmed'
    try:
        invoice.confirmed_by = user
    except Exception:
        pass
    invoice.save(update_fields=['status', 'confirmed_by', 'updated_at'])

    # ── 4. عمولة المندوب ─────────────────────────────────
    if invoice.agent_id:
        from apps.agents.services import apply_invoice_commission
        apply_invoice_commission(tenant, invoice)

    return invoice


# ─────────────────────────────────────────────
#   DELIVER SALE INVOICE  (تسليم مؤجل)
# ─────────────────────────────────────────────

@transaction.atomic
def deliver_sale_invoice(invoice: SaleInvoice, user) -> SaleInvoice:
    """
    تنفيذ تسليم فاتورة قيد التسليم (deferred).

    الخطوات:
      1. التحقق: pending_delivery فقط
      2. لكل بند: خصم الكمية الفعلية + تحرير الحجز + تسجيل StockMovement
      3. تحديث الحالة إلى confirmed + تسجيل delivered_at/by
    """
    if invoice.status != 'pending_delivery':
        raise ValueError(
            f"لا يمكن تسليم فاتورة بحالة «{invoice.get_status_display()}»."
        )

    tenant = invoice.tenant
    lines = list(invoice.lines.select_related('item'))

    for line in lines:
        qty_base = (line.quantity * (line.unit_factor or Decimal('1'))).quantize(Decimal('0.0001'))
        if not _is_stock_tracked_item(line.item):
            continue
        sq = _get_stock_qty(tenant, invoice.stock, line.item)
        # الكمية الفعلية يجب أن تكفي (لا يُفترض أن تكون أقل لأنها محجوزة)
        if sq.quantity < qty_base:
            raise ValueError(
                f"الكمية الفعلية لـ «{line.item.name}» في «{invoice.stock.name}» "
                f"هي {_fmt_decimal(sq.quantity)} فقط، والمطلوب {_fmt_decimal(qty_base)}."
            )
        sq.quantity -= qty_base
        sq.reserved_quantity = max(Decimal('0'), sq.reserved_quantity - qty_base)
        sq.save(update_fields=['quantity', 'reserved_quantity', 'updated_at'])
        StockMovement.objects.create(
            tenant=tenant,
            item=line.item,
            stock=invoice.stock,
            movement_type='sale_out',
            direction='out',
            quantity=qty_base,
            unit_cost=line.cost_price_snapshot,
            movement_date=timezone.localdate(),
            reference_type='sale_invoice',
            reference_id=invoice.id,
            balance_after=sq.quantity,
            notes=f'تسليم فاتورة {invoice.invoice_number}',
        )

    invoice.status = 'confirmed'
    invoice.delivered_at = timezone.now()
    try:
        invoice.delivered_by = user
    except Exception:
        pass
    invoice.save(update_fields=['status', 'delivered_at', 'delivered_by', 'updated_at'])
    return invoice


# ─────────────────────────────────────────────
#   CANCEL SALE INVOICE  (إلغاء الفاتورة)
# ─────────────────────────────────────────────

@transaction.atomic
def cancel_sale_invoice(invoice: SaleInvoice, user, reason: str = '') -> SaleInvoice:
    """
    إلغاء فاتورة مؤكدة.

    الخطوات:
      1. التحقق: confirmed فقط (لا يمكن إلغاء مرتجع / ملغاة)
      2. عكس حركات المخزون (إعادة الكميات)
      3. حذف SalePayments وإعادة paid_amount إلى 0
      4. حذف قيود CustomerLedger المرتبطة
      5. تحديث الحالة إلى cancelled
    """
    if invoice.status not in ('confirmed', 'pending_delivery'):
        raise ValueError(
            f"لا يمكن إلغاء فاتورة بحالة «{invoice.get_status_display()}»."
            " يمكن إلغاء الفواتير المؤكدة وقيد التسليم فقط."
        )

    tenant = invoice.tenant

    if invoice.status == 'pending_delivery':
        # حرر الحجوزات فقط (لم يُخصم مخزون بعد)
        lines = list(invoice.lines.select_related('item'))
        for line in lines:
            qty_base = (line.quantity * (line.unit_factor or Decimal('1'))).quantize(Decimal('0.0001'))
            _release_reservation(
                tenant=tenant, stock=invoice.stock,
                item=line.item, qty=qty_base,
            )
    else:
        # عكس المخزون (الحالة الاعتيادية)
        _reverse_stock_movements(tenant, invoice)

    # عكس الدفعات
    _reverse_payments(tenant, invoice)

    # عكس قيود العميل
    _reverse_customer_ledger(tenant, 'sale_invoice', invoice.id)

    # عكس عمولة المندوب
    if invoice.agent_id:
        from apps.agents.services import _reverse_agent_ledger
        _reverse_agent_ledger(tenant, 'sale_invoice', invoice.id)

    # تحديث الحالة
    invoice.status = 'cancelled'
    invoice.cancellation_reason = reason
    invoice.cancelled_at = timezone.now()

    try:
        invoice.cancelled_by = user
    except Exception:
        pass

    invoice.save(update_fields=[
        'status', 'cancellation_reason', 'cancelled_at',
        'cancelled_by', 'updated_at',
    ])
    return invoice


# ─────────────────────────────────────────────
#   EDIT CONFIRMED INVOICE  (تعديل فاتورة مؤكدة)
# ─────────────────────────────────────────────

@transaction.atomic
def edit_confirmed_invoice(invoice: SaleInvoice, header_data: dict,
                           lines_data: list, user) -> SaleInvoice:
    """
    تعديل فاتورة مؤكدة بأمان الكامل.

    الاستراتيجية: عكس كل التأثيرات ← تحديث البيانات ← إعادة التطبيق.
    هذا أكثر أماناً من حساب الفروقات يدوياً.

    header_data: dict يحتوي الحقول المحدَّثة (customer, stock, payment_method, ...)
    lines_data: list of dicts, كل dict يمثل سطراً جديداً للفاتورة:
      {
        'item_id': int,
        'quantity': Decimal,
        'unit_price': Decimal,
        'discount_percent': Decimal,
        'tax_rate': Decimal,
        'cost_price_snapshot': Decimal,
        'batch_number': str,
        'serial_number': str,
        'expiry_date': date | None,
      }

    سيناريوهات مغطاة:
      - تغيير المنتج  → يعكس مخزون المنتج القديم، يخصم من الجديد
      - تغيير الكمية  → يُعدِّل المخزون حسب الفرق
      - تغيير العميل  → ينقل قيود الآجل
      - تغيير السعر   → يُعدِّل قيد الآجل
      - تغيير طريقة الدفع → يُعدِّل كامل الجانب المالي
    """
    if invoice.status != 'confirmed':
        raise ValueError(
            f"لا يمكن تعديل فاتورة بحالة «{invoice.get_status_display()}»."
        )

    if invoice.sale_returns.filter(status='confirmed').exists():
        raise ValueError(
            "لا يمكن تعديل فاتورة تحتوي على مرتجعات مؤكدة."
        )

    tenant = invoice.tenant

    # ── الخطوة 1: عكس كل التأثيرات ─────────────────────
    _reverse_stock_movements(tenant, invoice)
    _reverse_payments(tenant, invoice)
    _reverse_customer_ledger(tenant, 'sale_invoice', invoice.id)
    if invoice.agent_id:
        from apps.agents.services import _reverse_agent_ledger
        _reverse_agent_ledger(tenant, 'sale_invoice', invoice.id)

    # ── الخطوة 2: تحديث البيانات ─────────────────────────
    allowed_header_fields = {
        'customer', 'stock', 'agent', 'invoice_date', 'due_date',
        'payment_method', 'invoice_discount_type', 'invoice_discount_value',
        'cash_amount', 'bank_amount', 'bank_reference', 'notes',
        'reference_number',
    }
    for field, value in header_data.items():
        if field in allowed_header_fields:
            setattr(invoice, field, value)

    # تحديث البنود: حذف القديمة وإنشاء الجديدة
    invoice.lines.all().delete()

    new_lines = []
    for ld in lines_data:
        from apps.items.models import Item
        item = Item.objects.get(id=ld['item_id'], tenant=tenant)

        from apps.items.models import Unit as ItemUnit
        unit_obj = ItemUnit.objects.filter(pk=ld['unit_id'], tenant=tenant).first() if ld.get('unit_id') else None

        line = SaleInvoiceLine(
            tenant=tenant,
            invoice=invoice,
            item=item,
            quantity=Decimal(str(ld['quantity'])),
            unit_price=Decimal(str(ld['unit_price'] or '0')),
            discount_percent=Decimal(str(ld.get('discount_percent') or '0')),
            tax_rate=Decimal(str(ld.get('tax_rate') or item.tax_rate)),
            cost_price_snapshot=Decimal(str(ld.get('cost_price_snapshot', item.cost_price))),
            batch_number=ld.get('batch_number', ''),
            serial_number=ld.get('serial_number', ''),
            expiry_date=ld.get('expiry_date'),
            unit=unit_obj,
            unit_factor=Decimal(str(ld.get('unit_factor', 1) or 1)),
        )
        line.calculate()
        new_lines.append(line)

    SaleInvoiceLine.objects.bulk_create(new_lines)
    # إعادة جلب مع id
    invoice.lines.set(SaleInvoiceLine.objects.filter(invoice=invoice))

    invoice.recalculate_totals()
    invoice.paid_amount = Decimal('0')
    invoice.save()

    # ── الخطوة 3: إعادة التأكيد (إعادة التطبيق) ─────────
    # نعيد الحالة إلى draft مؤقتاً حتى يعمل confirm_sale_invoice
    invoice.status = 'draft'
    invoice.save(update_fields=['status'])
    confirm_sale_invoice(invoice, user)

    return invoice


# ─────────────────────────────────────────────
#   CONFIRM SALE RETURN  (تأكيد المرتجع)
# ─────────────────────────────────────────────

@transaction.atomic
def confirm_sale_return(sale_return: SaleReturn, user) -> SaleReturn:
    """
    تأكيد مرتجع مبيعات.

    الخطوات:
      1. التحقق: draft فقط، والفاتورة الأصلية مؤكدة
      2. للكل سطر مرتجع:
         a. التحقق أن الكمية المُرتجَعة ≤ المتاحة للإرجاع
         b. إعادة الكمية إلى StockQuantity
         c. تسجيل StockMovement(sale_return_in)
         d. تحديث SaleInvoiceLine.returned_quantity
      3. الاسترداد حسب refund_method:
           cash    → SalePayment(cash, −total) أو ببساطة قيد عكسي
           bank    → SalePayment(bank, −total)
           balance → CustomerLedger(return, −total) = رصيد دائن للعميل
      4. تحديث حالة الفاتورة (returned / partially_returned)
      5. تحديث حالة المرتجع إلى confirmed
    """
    if sale_return.status != 'draft':
        raise ValueError(
            f"لا يمكن تأكيد مرتجع بحالة «{sale_return.get_status_display()}»."
        )

    invoice = sale_return.original_invoice
    if invoice.status not in ('confirmed', 'partially_returned'):
        raise ValueError(
            "يمكن إرجاع الفواتير المؤكدة فقط."
        )

    tenant = sale_return.tenant
    return_lines = list(sale_return.lines.select_related('invoice_line', 'item'))

    if not return_lines:
        raise ValueError("لا يمكن تأكيد مرتجع فارغ.")

    total_returned = Decimal('0')

    for rl in return_lines:
        inv_line = SaleInvoiceLine.objects.select_for_update().get(pk=rl.invoice_line_id)
        if rl.returned_quantity > inv_line.returnable_quantity:
            raise ValueError(
                f"الكمية القابلة للإرجاع لـ «{rl.item.name}» هي "
                f"{inv_line.returnable_quantity}، والمطلوب {rl.returned_quantity}."
            )

        # إعادة المخزون
        _restore_stock(
            tenant=tenant,
            stock=invoice.stock,
            item=rl.item,
            qty=rl.returned_quantity,
            unit_cost=inv_line.cost_price_snapshot,
            reference_type='sale_return',
            reference_id=sale_return.id,
            movement_date=sale_return.return_date,
            notes=f'مرتجع بيع {sale_return.return_number} — فاتورة {invoice.invoice_number}',
        )

        # تحديث returned_quantity في سطر الفاتورة
        inv_line.returned_quantity += rl.returned_quantity
        inv_line.save(update_fields=['returned_quantity', 'updated_at'])

        # حساب قيمة المرتجع لهذا السطر
        rl.line_total = (rl.returned_quantity * rl.unit_price).quantize(Decimal('0.01'))
        rl.save(update_fields=['line_total', 'updated_at'])
        total_returned += rl.line_total

    sale_return.total_returned = total_returned
    sale_return.save(update_fields=['total_returned', 'updated_at'])

    # ── الاسترداد المالي ──────────────────────────────────
    refund = sale_return.refund_method

    if refund in ('cash', 'bank'):
        _apply_payment(
            tenant=tenant,
            invoice=invoice,
            method=refund,
            amount=-total_returned,
            date=sale_return.return_date,
            notes=f"استرداد مرتجع {sale_return.return_number}",
        )
        invoice.sync_paid_amount()
        invoice.save(update_fields=['paid_amount', 'updated_at'])

    elif refund == 'balance' and invoice.customer:
        # رصيد دائن في حساب العميل
        _apply_customer_ledger(
            tenant=tenant,
            customer=invoice.customer,
            amount=-total_returned,  # سالب = دين من الشركة للعميل
            entry_type='return',
            reference_type='sale_return',
            reference_id=sale_return.id,
            date=sale_return.return_date,
            notes=f"مرتجع {sale_return.return_number} ← {invoice.invoice_number}",
        )

    # ── تحديث حالة الفاتورة ───────────────────────────────
    all_lines = invoice.lines.all()
    all_returned = all(l.returned_quantity >= l.quantity for l in all_lines)
    invoice.status = 'returned' if all_returned else 'partially_returned'
    invoice.save(update_fields=['status', 'updated_at'])

    # ── عكس عمولة المندوب بنسبة المرتجع (أساس: فاتورة/الاثنين فقط) ──
    if (invoice.agent_id and total_returned > 0 and invoice.grand_total > 0
            and invoice.agent.commission_basis in ('invoice', 'both')):
        from apps.agents.services import _apply_agent_ledger
        original_commission = invoice.agent.invoice_commission(invoice.grand_total)
        if original_commission > 0:
            ratio = total_returned / invoice.grand_total
            reversal = (original_commission * ratio).quantize(Decimal('0.01'))
            _apply_agent_ledger(
                tenant=tenant, agent=invoice.agent, amount=-reversal,
                entry_type='return', reference_type='sale_return',
                reference_id=sale_return.id, date=sale_return.return_date,
                notes=f'عكس عمولة مرتجع {sale_return.return_number}',
            )

    # ── تأكيد المرتجع ─────────────────────────────────────
    sale_return.status = 'confirmed'
    try:
        sale_return.confirmed_by = user
    except Exception:
        pass
    sale_return.save(update_fields=['status', 'confirmed_by', 'updated_at'])

    return sale_return


# ─────────────────────────────────────────────
#   CANCEL SALE RETURN  (إلغاء مرتجع مؤكد)
# ─────────────────────────────────────────────

@transaction.atomic
def cancel_sale_return(sale_return: SaleReturn, user) -> SaleReturn:
    """
    إلغاء مرتجع مبيعات مؤكد.

    الخطوات:
      1. عكس حركات المخزون (sale_return_in → خروج مخزون مجدداً)
      2. عكس قيود CustomerLedger / SalePayment
      3. إعادة returned_quantity في سطور الفاتورة
      4. إعادة حالة الفاتورة إلى confirmed
      5. تحديث حالة المرتجع إلى cancelled
    """
    if sale_return.status != 'confirmed':
        raise ValueError(
            f"لا يمكن إلغاء مرتجع بحالة «{sale_return.get_status_display()}»."
        )

    tenant = sale_return.tenant
    invoice = sale_return.original_invoice

    # ── عكس حركات المخزون ────────────────────────────────
    movements = StockMovement.objects.filter(
        tenant=tenant,
        reference_type='sale_return',
        reference_id=sale_return.id,
        is_reversal=False,
    ).select_related('item', 'stock')

    for mv in movements:
        sq = _get_stock_qty(tenant, mv.stock, mv.item)
        sq.quantity -= mv.quantity
        if sq.quantity < 0:
            raise ValueError(
                f"تعذّر إلغاء المرتجع: الكمية الحالية لـ «{mv.item.name}» "
                f"({sq.quantity + mv.quantity}) أقل من المُرتجَع ({mv.quantity})."
            )
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
            notes=f'إلغاء مرتجع بيع: {mv.notes}' if mv.notes else 'إلغاء مرتجع بيع',
            is_reversal=True,
        )

    # ── عكس سطور returned_quantity ───────────────────────
    for rl in sale_return.lines.select_related('invoice_line'):
        inv_line = SaleInvoiceLine.objects.select_for_update().get(pk=rl.invoice_line_id)
        inv_line.returned_quantity -= rl.returned_quantity
        if inv_line.returned_quantity < 0:
            inv_line.returned_quantity = Decimal('0')
        inv_line.save(update_fields=['returned_quantity', 'updated_at'])

    # ── عكس التأثيرات المالية ─────────────────────────────
    from apps.agents.services import _reverse_agent_ledger, apply_collection_commission

    refund = sale_return.refund_method
    if refund in ('cash', 'bank'):
        refund_payments = list(SalePayment.objects.filter(
            tenant=tenant,
            invoice=invoice,
            notes__contains=sale_return.return_number,
            is_reversed=False,
        ))
        for payment in refund_payments:
            if payment.payment_method == 'cash':
                reverse_notes = f"عكس استرداد مرتجع {sale_return.return_number}"
                if payment.amount < 0:
                    post_treasury_receipt(
                        tenant=tenant,
                        amount=abs(payment.amount),
                        date=timezone.localdate(),
                        reference_type='sale_payment',
                        reference_id=payment.id,
                        description=reverse_notes,
                    )
                elif payment.amount > 0:
                    post_treasury_disbursement(
                        tenant=tenant,
                        amount=abs(payment.amount),
                        date=timezone.localdate(),
                        reference_type='sale_payment',
                        reference_id=payment.id,
                        description=reverse_notes,
                    )
            # استعادة عمولة التحصيل (النسبية) التي عُكست عند تأكيد المرتجع
            if invoice.agent_id:
                _reverse_agent_ledger(tenant, 'sale_payment', payment.id)
            payment.is_reversed = True
            payment.save(update_fields=['is_reversed', 'updated_at'])
        invoice.sync_paid_amount()
        invoice.save(update_fields=['paid_amount', 'updated_at'])
        # إعادة تقييم عمولة "تحصيل كامل" الثابتة بعد عودة paid_amount
        if invoice.agent_id and refund_payments:
            apply_collection_commission(tenant, invoice, refund_payments[-1])
    elif refund == 'balance':
        _reverse_customer_ledger(tenant, 'sale_return', sale_return.id)

    # ── استعادة عمولة الفاتورة التناسبية التي عُكست عند تأكيد المرتجع ──
    if invoice.agent_id:
        _reverse_agent_ledger(tenant, 'sale_return', sale_return.id)

    # ── إعادة حالة الفاتورة ──────────────────────────────
    invoice.status = 'confirmed'
    invoice.save(update_fields=['status', 'updated_at'])

    # ── إلغاء المرتجع ─────────────────────────────────────
    sale_return.status = 'cancelled'
    sale_return.save(update_fields=['status', 'updated_at'])
    return sale_return


# ─────────────────────────────────────────────
#   RECORD CUSTOMER PAYMENT  (تسجيل دفعة من عميل)
# ─────────────────────────────────────────────

@transaction.atomic
def record_customer_payment(invoice: SaleInvoice, amount: Decimal,
                            method: str, date, reference: str = '',
                            notes: str = '', user=None, treasury=None) -> SalePayment:
    """
    تسجيل دفعة جديدة من عميل على فاتورة آجلة (credit).
    يُحدِّث paid_amount ويُنشئ قيداً عكسياً في CustomerLedger.
    """
    tenant = invoice.tenant

    if invoice.status not in ('confirmed', 'partially_returned'):
        raise ValueError("يمكن تسجيل الدفعات على الفواتير المؤكدة فقط.")

    if not invoice.customer:
        raise ValueError("لا يمكن تسجيل دفعة لفاتورة غير مرتبطة بعميل.")

    remaining = invoice.remaining_amount
    if amount > remaining + Decimal('0.01'):
        raise ValueError(
            f"المبلغ المدفوع ({amount}) يتجاوز المتبقي ({remaining})."
        )

    # إنشاء الدفعة
    payment = _apply_payment(
        tenant=tenant,
        invoice=invoice,
        method=method,
        amount=amount,
        date=date,
        reference=reference,
        notes=notes or f"دفعة على فاتورة {invoice.invoice_number}",
        treasury=treasury,
    )

    # تقليل المطالبة في حساب العميل
    if invoice.customer:
        ledger_reference_type = 'customer_payment_bank' if method == 'bank' else 'customer_payment_cash'
        _apply_customer_ledger(
            tenant=tenant,
            customer=invoice.customer,
            amount=-amount,  # سالب = تقليل المطالبة
            entry_type='payment',
            reference_type=ledger_reference_type,
            reference_id=payment.id,
            date=date,
            notes=f"دفعة على فاتورة {invoice.invoice_number}",
        )

    return payment


def _post_customer_direct_payment(tenant, customer, amount, method, date, reference, notes, user, treasury, kind):
    """
    يسجّل جزءاً من دفعة عميل غير مرتبط بأي فاتورة: إما تسديد مستحقات
    افتتاحية (kind='opening') أو رصيد دائن لدفعة زائدة (kind='credit').
    """
    method_suffix = 'bank' if method == 'bank' else 'cash'
    reference_type = f'customer_payment_{kind}_{method_suffix}'
    label = 'مستحقات افتتاحية' if kind == 'opening' else 'رصيد دائن (دفعة زائدة)'
    note_text = notes or f'دفعة عميل — {label}'

    entry = _apply_customer_ledger(
        tenant=tenant, customer=customer, amount=-amount,
        entry_type='payment', reference_type=reference_type, reference_id=None,
        date=date, notes=note_text,
    )
    if method == 'cash' and entry:
        post_treasury_receipt(
            tenant=tenant, amount=amount, date=date,
            reference_type=reference_type, reference_id=entry.id,
            description=note_text, user=user, treasury=treasury,
        )
    return entry


@transaction.atomic
def record_customer_payment_allocated(tenant, customer, amount: Decimal, method: str, date,
                                      reference: str = '', notes: str = '', user=None, treasury=None) -> dict:
    """
    يوزّع دفعة عميل تلقائياً بدل خصم رقم عام من رصيده:
      1. على فواتيره الآجلة/المختلطة المفتوحة، الأقدم أولاً (عبر record_customer_payment
         الفعلية — فتُحدَّث paid_amount وعمولة تحصيل المندوب وقيد العميل لكل فاتورة كالمعتاد).
      2. ما تبقّى من مبلغ ضد مستحقاته غير المرتبطة بفاتورة (مستحقات افتتاحية قديمة).
      3. ما يزيد عن كامل مديونيته يُسجَّل كرصيد دائن له.
    يُرجع تفصيل التوزيع لعرضه للمستخدم.
    """
    from django.db.models import Sum

    amount = Decimal(str(amount or 0))
    if amount <= 0:
        raise ValueError('المبلغ يجب أن يكون أكبر من الصفر')

    remaining = amount
    allocation = {'invoices': [], 'opening_balance': Decimal('0'), 'credit': Decimal('0')}

    open_invoices = (
        SaleInvoice.objects.select_for_update()
        .filter(
            tenant=tenant, customer=customer,
            status__in=('confirmed', 'partially_returned'),
            payment_method__in=('credit', 'mixed'),
        )
        .order_by('invoice_date', 'id')
    )

    for invoice in open_invoices:
        if remaining <= Decimal('0.005'):
            break
        due = invoice.remaining_amount
        if due <= Decimal('0.005'):
            continue
        chunk = min(due, remaining)
        payment = record_customer_payment(
            invoice, chunk, method, date,
            reference=reference, notes=notes, user=user, treasury=treasury,
        )
        allocation['invoices'].append({'invoice': invoice, 'amount': chunk, 'payment': payment})
        remaining -= chunk

    if remaining > Decimal('0.005'):
        ledger_total = (
            CustomerLedger.objects.filter(tenant=tenant, customer=customer)
            .aggregate(s=Sum('amount'))['s'] or Decimal('0')
        )
        current_balance = (customer.opening_balance or Decimal('0')) + ledger_total
        invoices_due_total = sum(
            (inv.remaining_amount for inv in SaleInvoice.objects.filter(
                tenant=tenant, customer=customer,
                status__in=('confirmed', 'partially_returned'),
                payment_method__in=('credit', 'mixed'),
            )),
            Decimal('0'),
        )
        non_invoice_dues = max(Decimal('0'), current_balance - invoices_due_total)

        if non_invoice_dues > Decimal('0.005'):
            chunk = min(non_invoice_dues, remaining)
            _post_customer_direct_payment(
                tenant, customer, chunk, method, date, reference, notes, user, treasury, kind='opening',
            )
            allocation['opening_balance'] = chunk
            remaining -= chunk

    if remaining > Decimal('0.005'):
        _post_customer_direct_payment(
            tenant, customer, remaining, method, date, reference, notes, user, treasury, kind='credit',
        )
        allocation['credit'] = remaining

    return allocation


def reverse_customer_payment_by_ledger_entry(tenant, ledger_entry, user=None):
    """
    يعكس قيد دفعة عميل واحد (زر إلغاء دفعة في شاشة دفعيات العملاء).
    لو القيد مرتبط بدفعة فاتورة حقيقية (SalePayment غير معكوسة) يعكسها
    بالكامل عبر _reverse_single_payment (خزينة + عمولة مندوب + قيد العميل +
    paid_amount)، وإلا (مستحقات افتتاحية / رصيد دائن / قيد قديم من قبل هذا
    الإصلاح بلا فاتورة مرتبطة) يعكس القيد وحركة الخزينة المرتبطة فقط.
    """
    if ledger_entry.entry_type != 'payment':
        raise ValueError('هذا القيد ليس دفعة قابلة للإلغاء.')

    linked_payment = None
    if ledger_entry.reference_type in ('customer_payment_cash', 'customer_payment_bank') and ledger_entry.reference_id:
        linked_payment = SalePayment.objects.filter(
            tenant=tenant, pk=ledger_entry.reference_id, is_reversed=False,
        ).select_related('invoice').first()

    if linked_payment:
        _reverse_single_payment(tenant, linked_payment.invoice, linked_payment)
        return

    reverse_notes = f"إلغاء دفعة عميل — {ledger_entry.notes or ''}".strip()
    if ledger_entry.reference_type.endswith('_cash'):
        movement = TreasuryMovement.objects.filter(
            tenant=tenant, reference_type=ledger_entry.reference_type, reference_id=ledger_entry.id,
        ).first()
        if movement:
            post_treasury_disbursement(
                tenant=tenant, amount=abs(ledger_entry.amount), date=timezone.localdate(),
                reference_type=f'{ledger_entry.reference_type}_cancel', reference_id=ledger_entry.id,
                description=reverse_notes, user=user, treasury=movement.treasury,
            )

    _apply_customer_ledger(
        tenant=tenant, customer=ledger_entry.customer, amount=-ledger_entry.amount,
        entry_type='adjustment', reference_type='customer_payment_cancel', reference_id=ledger_entry.id,
        date=timezone.localdate(), notes=reverse_notes,
    )


def build_customer_statement_timeline(tenant, entries):
    """
    يبني قائمة حركات كشف حساب العميل من قيود CustomerLedger خام، بعد تجميع
    أنماط الإلغاء/التعديل بدل عرضها كقيود متفرقة مربكة:
      - مجموعة قيود بنفس (reference_type, reference_id) لو آخرها قيد غير
        معكوس (تعديل فاتورة: عكس ثم إعادة إنشاء) → تُعرض النسخة الأخيرة فقط.
      - لو آخرها معكوس (إلغاء بلا إعادة إنشاء بعده) → تُلخَّص في قيد واحد.
      - إلغاء فاتورة آجلة بالكامل (الفاتورة + كل دفعاتها معكوسة) يُلخَّص في
        سطر واحد للفاتورة كلها، بدل عرض الفاتورة وكل دفعة وكل عكس منفصلين.

    entries: قائمة CustomerLedger لعميل واحد، مرتبة تصاعدياً (entry_date, id)
    — هذا الترتيب ضروري لضمان معالجة قيد الفاتورة قبل قيود دفعاتها.

    يُرجع قائمة عناصر (SimpleNamespace) بنفس حقول CustomerLedger المعروضة
    (entry_date, entry_type, amount, running_balance, notes, reference_type,
    reference_id) بالإضافة إلى is_edited و is_reversal، مرتبة تصاعدياً.
    """
    from types import SimpleNamespace

    groups = {}
    ungrouped = []
    for e in entries:
        if e.reference_type and e.reference_id:
            groups.setdefault((e.reference_type, e.reference_id), []).append(e)
        else:
            ungrouped.append(e)
    for group_entries in groups.values():
        group_entries.sort(key=lambda x: x.id)

    payment_id_to_invoice_id = {}
    sale_payment_ids = [
        ref_id for (ref_type, ref_id) in groups
        if ref_type in ('customer_payment_cash', 'customer_payment_bank')
    ]
    if sale_payment_ids:
        for sp_id, inv_id in SalePayment.objects.filter(
            tenant=tenant, pk__in=sale_payment_ids,
        ).values_list('id', 'invoice_id'):
            payment_id_to_invoice_id[sp_id] = inv_id

    def item(e, sort_id, is_edited=False, is_reversal=None, amount=None,
             running_balance=None, notes=None, entry_date=None):
        return SimpleNamespace(
            entry_date=e.entry_date if entry_date is None else entry_date,
            entry_type=e.entry_type,
            amount=e.amount if amount is None else amount,
            running_balance=e.running_balance if running_balance is None else running_balance,
            notes=(e.notes or '') if notes is None else notes,
            reference_type=e.reference_type, reference_id=e.reference_id,
            is_edited=is_edited, is_reversal=e.is_reversal if is_reversal is None else is_reversal,
            _sort_id=sort_id,
        )

    consumed_keys = set()
    result = [item(e, e.id) for e in ungrouped]

    for key, group_entries in groups.items():
        if key in consumed_keys:
            continue
        ref_type, ref_id = key
        last = group_entries[-1]

        if not last.is_reversal:
            result.append(item(last, last.id, is_edited=len(group_entries) > 1))
            continue

        # آخر قيد في المجموعة معكوس = المجموعة مُلغاة بالكامل
        if ref_type == 'sale_invoice':
            merged = list(group_entries)
            for pid, inv_id in payment_id_to_invoice_id.items():
                if inv_id != ref_id:
                    continue
                for pkey in (('customer_payment_cash', pid), ('customer_payment_bank', pid)):
                    p_group = groups.get(pkey)
                    if p_group and p_group[-1].is_reversal:
                        merged.extend(p_group)
                        consumed_keys.add(pkey)
            merged.sort(key=lambda x: x.id)
            final = merged[-1]
            base = group_entries[0]
            result.append(item(
                base, final.id,
                is_reversal=True,
                entry_date=final.entry_date,
                running_balance=final.running_balance,
                notes=f'تم إلغاء الفاتورة بالكامل — {base.notes}'.strip(' —'),
            ))
        else:
            base = group_entries[0]
            result.append(item(
                base, last.id,
                is_reversal=True,
                entry_date=last.entry_date,
                running_balance=last.running_balance,
                notes=last.notes,
            ))

    result.sort(key=lambda it: it._sort_id)
    return result


# ─────────────────────────────────────────────
#   BUILD INVOICE FROM POST DATA  (مساعد Views)
# ─────────────────────────────────────────────

def build_invoice_from_post(tenant, stock, data: dict, lines_data: list,
                            user) -> SaleInvoice:
    """
    ينشئ SaleInvoice + SaleInvoiceLines من بيانات النموذج ويحفظها كـ draft.
    لا يؤكِّد الفاتورة؛ استخدم confirm_sale_invoice لاحقاً.
    """
    from apps.customers.models import Customer
    from apps.items.models import Item

    customer = None
    if data.get('customer_id'):
        customer = Customer.objects.get(id=data['customer_id'], tenant=tenant)

    agent = None
    if data.get('agent_id'):
        from apps.agents.models import Agent
        try:
            agent = Agent.objects.get(id=data['agent_id'], tenant=tenant, is_active=True)
        except Agent.DoesNotExist:
            pass

    invoice = SaleInvoice(
        tenant=tenant,
        customer=customer,
        agent=agent,
        stock=stock,
        invoice_date=data['invoice_date'],
        due_date=data.get('due_date'),
        payment_method=data.get('payment_method', 'cash'),
        delivery_type=data.get('delivery_type', 'immediate'),
        invoice_discount_type=data.get('invoice_discount_type', 'percent'),
        invoice_discount_value=Decimal(str(data.get('invoice_discount_value', 0))),
        cash_amount=Decimal(str(data.get('cash_amount', 0))),
        bank_amount=Decimal(str(data.get('bank_amount', 0))),
        bank_reference=data.get('bank_reference', ''),
        notes=data.get('notes', ''),
        reference_number=data.get('reference_number', ''),
        created_by=user,
    )
    invoice.save()

    for ld in lines_data:
        item = Item.objects.get(id=ld['item_id'], tenant=tenant)

        from apps.items.models import Unit as ItemUnit
        unit_obj = ItemUnit.objects.filter(pk=ld['unit_id'], tenant=tenant).first() if ld.get('unit_id') else None

        line = SaleInvoiceLine(
            tenant=tenant,
            invoice=invoice,
            item=item,
            quantity=Decimal(str(ld['quantity'])),
            unit_price=Decimal(str(ld['unit_price'] or '0')),
            discount_percent=Decimal(str(ld.get('discount_percent') or '0')),
            tax_rate=Decimal(str(ld.get('tax_rate') or item.tax_rate)),
            cost_price_snapshot=Decimal(str(ld.get('cost_price_snapshot', item.cost_price))),
            batch_number=ld.get('batch_number', ''),
            serial_number=ld.get('serial_number', ''),
            expiry_date=ld.get('expiry_date'),
            unit=unit_obj,
            unit_factor=Decimal(str(ld.get('unit_factor', 1) or 1)),
            created_by=user,
        )
        line.calculate()
        line.save()

    invoice.recalculate_totals()
    invoice.save()
    return invoice


# ═══════════════════════════════════════════════════════════
#   SALE QUOTE SERVICES  — عروض الأسعار
# ═══════════════════════════════════════════════════════════

from .models import SaleQuote, SaleQuoteLine  # noqa: E402 (circular-safe after model def)
from apps.items.models import Item  # already imported above; safe double


def build_quote_from_post(tenant, user, post_data, lines_data, instance=None):
    """
    ينشئ أو يُحدِّث SaleQuote (مسودة فقط) من بيانات POST.

    post_data: dict يحتوي على حقول الرأس
    lines_data: list of dicts (item_id, quantity, unit_price, …)
    instance: SaleQuote موجود للتعديل (None لإنشاء جديد)
    """
    from apps.stocks.models import Stock
    from apps.customers.models import Customer

    stock = Stock.objects.get(id=post_data['stock_id'], tenant=tenant)
    customer = None
    if post_data.get('customer_id'):
        customer = Customer.objects.get(id=post_data['customer_id'], tenant=tenant)

    if instance is None:
        quote = SaleQuote(tenant=tenant, created_by=user)
    else:
        if instance.status != 'draft':
            raise ValueError("لا يمكن تعديل عرض سعر غير مسودة.")
        quote = instance
        quote.updated_by = user
        quote.quote_lines.all().delete()

    quote.customer = customer
    quote.stock = stock
    quote.quote_date = post_data['quote_date']
    quote.expiry_date = post_data.get('expiry_date') or None
    quote.reference_number = post_data.get('reference_number', '')
    quote.notes = post_data.get('notes', '')
    quote.terms = post_data.get('terms', '')
    quote.quote_discount_type = post_data.get('quote_discount_type', 'percent')
    quote.quote_discount_value = Decimal(str(post_data.get('quote_discount_value', 0)))
    quote.save()

    for ld in lines_data:
        item = Item.objects.get(id=ld['item_id'], tenant=tenant)

        line = SaleQuoteLine(
            tenant=tenant,
            quote=quote,
            item=item,
            quantity=Decimal(str(ld['quantity'])),
            unit_price=Decimal(str(ld['unit_price'] or '0')),
            discount_percent=Decimal(str(ld.get('discount_percent') or '0')),
            tax_rate=Decimal(str(ld.get('tax_rate') or item.tax_rate)),
            created_by=user,
        )
        # حساب السطر
        qty = line.quantity
        price = line.unit_price
        disc_pct = line.discount_percent
        disc_amt = (qty * price * disc_pct / Decimal('100')).quantize(Decimal('0.01'))
        line.discount_amount = disc_amt
        sub = (qty * price - disc_amt).quantize(Decimal('0.01'))
        line.line_subtotal = sub
        tax_amt = (sub * line.tax_rate / Decimal('100')).quantize(Decimal('0.01'))
        line.tax_amount = tax_amt
        line.line_total = sub + tax_amt
        line.save()

    quote.recalculate_totals()
    quote.save()
    return quote


@transaction.atomic
def mark_quote_sent(quote, user):
    """تغيير الحالة إلى مُرسَل."""
    if not quote.can_send:
        raise ValueError("لا يمكن إرسال هذا العرض في وضعه الحالي.")
    quote.status = 'sent'
    quote.updated_by = user
    quote.save(update_fields=['status', 'updated_by', 'updated_at'])


@transaction.atomic
def mark_quote_accepted(quote, user):
    """تغيير الحالة إلى مقبول."""
    if quote.status not in ('sent',):
        raise ValueError("لا يمكن قبول هذا العرض في وضعه الحالي.")
    quote.status = 'accepted'
    quote.updated_by = user
    quote.save(update_fields=['status', 'updated_by', 'updated_at'])


@transaction.atomic
def mark_quote_rejected(quote, user):
    """تغيير الحالة إلى مرفوض."""
    if quote.status not in ('sent', 'accepted'):
        raise ValueError("لا يمكن رفض هذا العرض في وضعه الحالي.")
    quote.status = 'rejected'
    quote.updated_by = user
    quote.save(update_fields=['status', 'updated_by', 'updated_at'])


@transaction.atomic
def cancel_sale_quote(quote, user):
    """إلغاء عرض السعر."""
    if not quote.can_cancel:
        raise ValueError("لا يمكن إلغاء هذا العرض في وضعه الحالي.")
    quote.status = 'cancelled'
    quote.updated_by = user
    quote.save(update_fields=['status', 'updated_by', 'updated_at'])


@transaction.atomic
def convert_quote_to_invoice(quote, user, payment_method='cash',
                              cash_amount=None, bank_amount=None, bank_reference=''):
    """
    يحوّل عرض السعر إلى فاتورة بيع مسودة.
    ينسخ الرأس والبنود ويربط الفاتورة بالعرض.
    الفاتورة تُنشأ كمسودة (draft) — يؤكدها المستخدم لاحقاً.
    """
    if not quote.can_convert:
        raise ValueError(f"لا يمكن تحويل عرض بالحالة «{quote.get_status_display()}».")

    # بناء بيانات الرأس
    header = {
        'stock_id': quote.stock_id,
        'customer_id': quote.customer_id,
        'invoice_date': str(quote.quote_date),
        'due_date': str(quote.expiry_date) if quote.expiry_date else None,
        'reference_number': quote.reference_number,
        'payment_method': payment_method,
        'invoice_discount_type': quote.quote_discount_type,
        'invoice_discount_value': str(quote.quote_discount_value),
        'notes': quote.notes,
        'cash_amount': str(cash_amount or 0),
        'bank_amount': str(bank_amount or 0),
        'bank_reference': bank_reference or '',
    }

    # بناء بنود الفاتورة من بنود العرض
    lines = []
    for ql in quote.quote_lines.select_related('item').all():
        lines.append({
            'item_id': ql.item_id,
            'quantity': str(ql.quantity),
            'unit_price': str(ql.unit_price),
            'discount_percent': str(ql.discount_percent),
            'tax_rate': str(ql.tax_rate),
            'cost_price_snapshot': str(getattr(ql.item, 'cost_price', 0)),
            'batch_number': '',
            'serial_number': '',
            'expiry_date': None,
        })

    invoice = build_invoice_from_post(
        tenant=quote.tenant,
        stock=quote.stock,
        data=header,
        lines_data=lines,
        user=user,
    )

    # ربط العرض بالفاتورة
    quote.converted_invoice = invoice
    quote.converted_at = timezone.now()
    quote.converted_by = user
    quote.status = 'converted'
    quote.updated_by = user
    quote.save(update_fields=[
        'status', 'converted_invoice', 'converted_at', 'converted_by',
        'updated_by', 'updated_at'
    ])

    return invoice
