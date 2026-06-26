from decimal import Decimal

from .models import AgentLedger


def _apply_agent_ledger(tenant, agent, amount, entry_type, reference_type, reference_id, date, notes=''):
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
    )
    return entry


def _reverse_agent_ledger(tenant, reference_type, reference_id):
    entries = AgentLedger.objects.filter(
        tenant=tenant,
        reference_type=reference_type,
        reference_id=reference_id,
        is_reversal=False,
    ).select_related('agent')
    for entry in entries:
        _apply_agent_ledger(
            tenant=tenant,
            agent=entry.agent,
            amount=-entry.amount,
            entry_type='adjustment',
            reference_type=f'{reference_type}_cancel',
            reference_id=entry.id,
            date=entry.entry_date,
            notes=f'إلغاء: {entry.notes}',
        )
        entry.is_reversal = True
        entry.save(update_fields=['is_reversal'])
