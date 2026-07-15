from decimal import Decimal

from .models import AgentLedger


def _apply_agent_ledger(tenant, agent, amount, entry_type, reference_type, reference_id, date, notes='', is_reversal=False):
    if not agent:
        return None

    from django.db.models import Sum

    prev = (
        AgentLedger.objects.filter(tenant=tenant, agent=agent)
        .aggregate(s=Sum('amount'))['s']
        or Decimal('0')
    )

    entry = AgentLedger.objects.create(
        tenant=tenant,
        agent=agent,
        entry_type=entry_type,
        amount=amount,
        entry_date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        running_balance=prev + amount,
        notes=notes,
        is_reversal=is_reversal,
    )
    return entry


def apply_invoice_commission(tenant, invoice):
    """يُسجل عمولة المندوب على إجمالي الفاتورة عند التأكيد (أساس: فاتورة/الاثنين)."""
    agent = invoice.agent
    if not agent:
        return None
    amount = agent.invoice_commission(invoice.grand_total)
    if amount <= 0:
        return None
    return _apply_agent_ledger(
        tenant=tenant, agent=agent, amount=amount,
        entry_type='commission', reference_type='sale_invoice',
        reference_id=invoice.id, date=invoice.invoice_date,
        notes=f'عمولة فاتورة {invoice.invoice_number}',
    )


def apply_collection_commission(tenant, invoice, payment):
    """
    يُسجل/يعكس عمولة تحصيل عند كل حركة نقد فعلية على الفاتورة
    (أساس: تحصيل/الاثنين). payment.amount موجب = تحصيل، سالب = استرداد.
    """
    agent = invoice.agent
    if not agent or agent.commission_basis not in ('collection', 'both'):
        return

    if agent.commission_type == 'percentage':
        rate = agent.commission_rate_collection if agent.commission_basis == 'both' else agent.commission_rate
        amount = (payment.amount * rate / Decimal('100')).quantize(Decimal('0.01'))
        if amount != 0:
            _apply_agent_ledger(
                tenant=tenant, agent=agent, amount=amount,
                entry_type='commission', reference_type='sale_payment',
                reference_id=payment.id, date=payment.payment_date,
                notes=f'عمولة تحصيل — {invoice.invoice_number}',
            )

    elif agent.commission_type == 'fixed':
        fully_collected = invoice.paid_amount >= invoice.grand_total - Decimal('0.01')
        existing = AgentLedger.objects.filter(
            tenant=tenant, agent=agent,
            reference_type='sale_invoice_collection', reference_id=invoice.id,
            is_reversal=False,
        ).exists()
        if fully_collected and not existing:
            rate = agent.commission_rate_collection if agent.commission_basis == 'both' else agent.commission_rate
            amount = rate.quantize(Decimal('0.01'))
            if amount > 0:
                _apply_agent_ledger(
                    tenant=tenant, agent=agent, amount=amount,
                    entry_type='commission', reference_type='sale_invoice_collection',
                    reference_id=invoice.id, date=payment.payment_date,
                    notes=f'عمولة تحصيل كامل — {invoice.invoice_number}',
                )
        elif not fully_collected and existing:
            _reverse_agent_ledger(tenant, 'sale_invoice_collection', invoice.id)


def _reverse_agent_ledger(tenant, reference_type, reference_id):
    """
    ملاحظة: is_reversal بيوصف القيد العكسي الجديد نفسه (مش القيد الأصلي اللي
    اتعكس) — عشان واجهات العرض تقدر تميّز صف الإلغاء بشارة/لون مختلف. الحماية
    من عكس نفس القيد مرتين مبنية على وجود قيد إلغاء مرتبط به فعلاً، مش على
    الفلاج، عشان تفضل شغالة حتى لو _reverse_agent_ledger اتنادت أكتر من مرة
    بنفس المرجع (زي فاتورة عندها أكتر من دفعة).
    """
    entries = AgentLedger.objects.filter(
        tenant=tenant,
        reference_type=reference_type,
        reference_id=reference_id,
        is_reversal=False,
    ).select_related('agent')
    for entry in entries:
        already_reversed = AgentLedger.objects.filter(
            tenant=tenant,
            reference_type=f'{reference_type}_cancel',
            reference_id=entry.id,
        ).exists()
        if already_reversed:
            continue
        _apply_agent_ledger(
            tenant=tenant,
            agent=entry.agent,
            amount=-entry.amount,
            entry_type=entry.entry_type,
            reference_type=f'{reference_type}_cancel',
            reference_id=entry.id,
            date=entry.entry_date,
            notes=f'إلغاء: {entry.notes}',
            is_reversal=True,
        )


AGENT_LEDGER_TYPE_LABELS = {
    'payment':    'دفعة للمندوب',
    'return':     'مرتجع مبيعات',
    'adjustment': 'تعديل يدوي',
    'opening':    'مستحقات افتتاحية',
}


def agent_ledger_display_label(entry_type, reference_type):
    """
    تسمية أوضح لقيد سجل المندوب حسب نوعه ومرجعه — بالذات تفرّق عمولة العمولة
    بين «عمولة فاتورة» (أساس فاتورة) و«عمولة تحصيل» (أساس تحصيل)، بدل تسمية
    عامة واحدة «عمولة مبيعات» للاتنين، سواء كان القيد أصلياً أو قيد إلغاء له.
    """
    if entry_type != 'commission':
        return AGENT_LEDGER_TYPE_LABELS.get(entry_type, entry_type)

    base_ref = reference_type or ''
    if base_ref.endswith('_cancel'):
        base_ref = base_ref[:-len('_cancel')]

    if base_ref == 'sale_invoice':
        return 'عمولة فاتورة'
    if base_ref in ('sale_payment', 'sale_invoice_collection'):
        return 'عمولة تحصيل'
    return 'عمولة مبيعات'
