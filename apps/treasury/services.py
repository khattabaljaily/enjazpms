from decimal import Decimal

from django.db import transaction

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
):
    amount = Decimal(str(amount or 0))
    if amount <= 0:
        return None

    treasury = get_or_create_default_treasury(tenant, user=user)
    treasury = Treasury.objects.select_for_update().get(pk=treasury.pk)

    signed_amount = amount if movement_type in ('receipt', 'adjustment') else -amount
    next_balance = (treasury.current_balance or Decimal('0')) + signed_amount

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


def post_treasury_receipt(tenant, amount, date, reference_type='', reference_id=None, description='', user=None):
    return post_treasury_movement(
        tenant=tenant,
        movement_type='receipt',
        amount=amount,
        date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        user=user,
    )


def post_treasury_disbursement(tenant, amount, date, reference_type='', reference_id=None, description='', user=None):
    return post_treasury_movement(
        tenant=tenant,
        movement_type='disbursement',
        amount=amount,
        date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        user=user,
    )
