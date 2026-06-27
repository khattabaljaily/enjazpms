from decimal import Decimal

from django.db import transaction
from django.utils import timezone as dj_tz

from .models import Treasury, TreasuryMovement


@transaction.atomic
def get_or_create_default_treasury(tenant, user=None):
    treasury = Treasury.objects.for_tenant(tenant).filter(is_default=True).first()
    if treasury:
        return treasury

    treasury = Treasury.objects.for_tenant(tenant).order_by('id').first()
    if treasury:
        if not treasury.is_default:
            treasury.is_default = True
            treasury.updated_by = user
            treasury.save(update_fields=['is_default', 'updated_by', 'updated_at'])
        return treasury

    return Treasury.objects.create(
        tenant=tenant,
        name='الخزينة الرئيسية',
        code='MAIN',
        is_default=True,
        is_active=True,
        created_by=user,
        updated_by=user,
    )


@transaction.atomic
def post_treasury_movement(
    tenant,
    movement_type,
    amount,
    date,
    reference_type='',
    reference_id=None,
    description='',
    user=None,
    treasury=None,
):
    amount = Decimal(str(amount or 0))
    if amount <= 0:
        return None

    if treasury is None:
        treasury = get_or_create_default_treasury(tenant, user=user)
    treasury = Treasury.objects.select_for_update().get(pk=treasury.pk)

    signed_amount = amount if movement_type in ('receipt', 'adjustment') else -amount
    next_balance = (treasury.current_balance or Decimal('0')) + signed_amount

    if movement_type == 'disbursement' and next_balance < 0:
        raise ValueError(
            f"رصيد الخزينة غير كافٍ. الرصيد الحالي: {treasury.current_balance or Decimal('0')} والمطلوب صرفه: {amount}."
        )

    movement = TreasuryMovement.objects.create(
        tenant=tenant,
        treasury=treasury,
        movement_type=movement_type,
        amount=amount,
        movement_date=date,
        description=description,
        reference_type=reference_type,
        reference_id=reference_id,
        running_balance=next_balance,
        created_by=user,
        updated_by=user,
    )

    treasury.current_balance = next_balance
    treasury.updated_by = user
    treasury.save(update_fields=['current_balance', 'updated_by', 'updated_at'])
    return movement


def post_treasury_receipt(tenant, amount, date, reference_type='', reference_id=None, description='', user=None, treasury=None):
    return post_treasury_movement(
        tenant=tenant,
        movement_type='receipt',
        amount=amount,
        date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        user=user,
        treasury=treasury,
    )


def post_treasury_disbursement(tenant, amount, date, reference_type='', reference_id=None, description='', user=None, treasury=None):
    return post_treasury_movement(
        tenant=tenant,
        movement_type='disbursement',
        amount=amount,
        date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        user=user,
        treasury=treasury,
    )


def recalculate_treasury_running_balances(tenant, treasury, user=None):
    """Recalculate running_balance for every movement of a treasury in chronological order."""
    movements = list(
        TreasuryMovement.objects.for_tenant(tenant)
        .filter(treasury=treasury)
        .order_by('movement_date', 'id')
    )
    running = Decimal('0')
    for mv in movements:
        if mv.movement_type in ('receipt', 'adjustment'):
            running += mv.amount
        else:
            running -= mv.amount
        mv.running_balance = running
        mv.save(update_fields=['running_balance'])

    treasury_obj = Treasury.objects.select_for_update().get(pk=treasury.pk)
    treasury_obj.current_balance = running
    if user:
        treasury_obj.updated_by = user
    treasury_obj.save(update_fields=['current_balance', 'updated_by', 'updated_at'])
    return running


@transaction.atomic
def set_opening_balance(tenant, treasury, amount, date, user=None):
    """Create or update the opening-balance adjustment movement for a treasury."""
    amount = Decimal(str(amount or 0))

    existing = TreasuryMovement.objects.for_tenant(tenant).filter(
        treasury=treasury,
        reference_type='opening_balance',
    ).first()

    if existing:
        if amount == 0:
            existing.delete()
        else:
            existing.amount = amount
            existing.movement_date = date
            if user:
                existing.updated_by = user
            existing.save(update_fields=['amount', 'movement_date', 'updated_by', 'updated_at'])
    else:
        if amount == 0:
            return None
        TreasuryMovement.objects.create(
            tenant=tenant,
            treasury=treasury,
            movement_type='adjustment',
            amount=amount,
            movement_date=date,
            description='رصيد افتتاحي',
            reference_type='opening_balance',
            running_balance=Decimal('0'),  # will be fixed by recalculate below
            created_by=user,
            updated_by=user,
        )

    recalculate_treasury_running_balances(tenant, treasury, user=user)
    return TreasuryMovement.objects.for_tenant(tenant).filter(
        treasury=treasury, reference_type='opening_balance'
    ).first()


@transaction.atomic
def post_treasury_transfer(
    tenant,
    from_treasury,
    to_treasury,
    from_amount,
    to_amount,
    exchange_rate,
    transfer_date,
    notes='',
    user=None,
):
    """يُنشئ تحويلاً بين خزينتين: خصم من المصدر وإيداع في الوجهة."""
    from .models import TreasuryTransfer

    from_amount = Decimal(str(from_amount))
    to_amount = Decimal(str(to_amount))
    exchange_rate = Decimal(str(exchange_rate))

    from_currency = from_treasury.currency or tenant.currency
    to_currency = to_treasury.currency or tenant.currency
    desc_out = f'تحويل إلى {to_treasury.name} ({to_amount} {to_currency}) — سعر الصرف: {exchange_rate}'
    desc_in = f'تحويل من {from_treasury.name} ({from_amount} {from_currency}) — سعر الصرف: {exchange_rate}'

    mv_out = post_treasury_movement(
        tenant=tenant, movement_type='disbursement',
        amount=from_amount, date=transfer_date,
        description=desc_out, reference_type='transfer',
        user=user, treasury=from_treasury,
    )
    mv_in = post_treasury_movement(
        tenant=tenant, movement_type='receipt',
        amount=to_amount, date=transfer_date,
        description=desc_in, reference_type='transfer',
        user=user, treasury=to_treasury,
    )

    transfer = TreasuryTransfer.objects.create(
        tenant=tenant,
        from_treasury=from_treasury,
        to_treasury=to_treasury,
        from_amount=from_amount,
        to_amount=to_amount,
        exchange_rate=exchange_rate,
        transfer_date=transfer_date,
        notes=notes,
        from_movement=mv_out,
        to_movement=mv_in,
        created_by=user,
        updated_by=user,
    )
    return transfer
