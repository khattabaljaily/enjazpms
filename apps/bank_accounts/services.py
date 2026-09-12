from decimal import Decimal

from django.db import transaction

from .models import BankAccount, BankAccountMovement


@transaction.atomic
def post_bank_account_movement(
    tenant,
    movement_type,
    amount,
    date,
    reference_type='',
    reference_id=None,
    description='',
    user=None,
    bank_account=None,
):
    amount = Decimal(str(amount or 0))
    if amount <= 0:
        return None

    if bank_account is None:
        raise ValueError('يجب اختيار حساب بنكي.')
    bank_account = BankAccount.objects.select_for_update().get(pk=bank_account.pk)

    signed_amount = amount if movement_type in ('receipt', 'adjustment') else -amount
    next_balance = (bank_account.current_balance or Decimal('0')) + signed_amount

    if movement_type == 'disbursement' and next_balance < 0:
        raise ValueError(
            f"رصيد الحساب البنكي غير كافٍ. الرصيد الحالي: {bank_account.current_balance or Decimal('0')} والمطلوب صرفه: {amount}."
        )

    movement = BankAccountMovement.objects.create(
        tenant=tenant,
        bank_account=bank_account,
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

    bank_account.current_balance = next_balance
    bank_account.updated_by = user
    bank_account.save(update_fields=['current_balance', 'updated_by', 'updated_at'])
    return movement


def post_bank_account_receipt(tenant, amount, date, reference_type='', reference_id=None, description='', user=None, bank_account=None):
    return post_bank_account_movement(
        tenant=tenant,
        movement_type='receipt',
        amount=amount,
        date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        user=user,
        bank_account=bank_account,
    )


def post_bank_account_disbursement(tenant, amount, date, reference_type='', reference_id=None, description='', user=None, bank_account=None):
    return post_bank_account_movement(
        tenant=tenant,
        movement_type='disbursement',
        amount=amount,
        date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        user=user,
        bank_account=bank_account,
    )


def recalculate_bank_account_running_balances(tenant, bank_account, user=None):
    """Recalculate running_balance for every movement of a bank account in chronological order."""
    movements = list(
        BankAccountMovement.objects.for_tenant(tenant)
        .filter(bank_account=bank_account)
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

    account_obj = BankAccount.objects.select_for_update().get(pk=bank_account.pk)
    account_obj.current_balance = running
    if user:
        account_obj.updated_by = user
    account_obj.save(update_fields=['current_balance', 'updated_by', 'updated_at'])
    return running


@transaction.atomic
def set_opening_balance(tenant, bank_account, amount, date, user=None):
    """Create or update the opening-balance adjustment movement for a bank account."""
    amount = Decimal(str(amount or 0))

    existing = BankAccountMovement.objects.for_tenant(tenant).filter(
        bank_account=bank_account,
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
        BankAccountMovement.objects.create(
            tenant=tenant,
            bank_account=bank_account,
            movement_type='adjustment',
            amount=amount,
            movement_date=date,
            description='رصيد افتتاحي',
            reference_type='opening_balance',
            running_balance=Decimal('0'),  # will be fixed by recalculate below
            created_by=user,
            updated_by=user,
        )

    recalculate_bank_account_running_balances(tenant, bank_account, user=user)
    return BankAccountMovement.objects.for_tenant(tenant).filter(
        bank_account=bank_account, reference_type='opening_balance'
    ).first()


@transaction.atomic
def post_bank_account_transfer(
    tenant,
    from_account,
    to_account,
    from_amount,
    to_amount,
    exchange_rate,
    transfer_date,
    notes='',
    user=None,
):
    """يُنشئ تحويلاً بين حسابين بنكيين: خصم من المصدر وإيداع في الوجهة."""
    from .models import BankAccountTransfer

    from_amount = Decimal(str(from_amount))
    to_amount = Decimal(str(to_amount))
    exchange_rate = Decimal(str(exchange_rate))

    from_currency = from_account.currency or tenant.currency
    to_currency = to_account.currency or tenant.currency
    desc_out = f'تحويل إلى {to_account.name} ({to_amount} {to_currency}) — سعر الصرف: {exchange_rate}'
    desc_in = f'تحويل من {from_account.name} ({from_amount} {from_currency}) — سعر الصرف: {exchange_rate}'

    mv_out = post_bank_account_movement(
        tenant=tenant, movement_type='disbursement',
        amount=from_amount, date=transfer_date,
        description=desc_out, reference_type='transfer',
        user=user, bank_account=from_account,
    )
    mv_in = post_bank_account_movement(
        tenant=tenant, movement_type='receipt',
        amount=to_amount, date=transfer_date,
        description=desc_in, reference_type='transfer',
        user=user, bank_account=to_account,
    )

    transfer = BankAccountTransfer.objects.create(
        tenant=tenant,
        from_bank_account=from_account,
        to_bank_account=to_account,
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


@transaction.atomic
def post_treasury_to_bank_transfer(tenant, treasury, bank_account, amount, transfer_date, notes='', user=None):
    """سحب من الخزينة وإيداع في حساب بنكي (إيداع نقدي بالبنك)."""
    from apps.treasury.services import post_treasury_disbursement
    from .models import TreasuryBankTransfer

    amount = Decimal(str(amount))
    desc = f'تحويل من الخزينة {treasury.name} إلى الحساب البنكي {bank_account.name}'

    treasury_movement = post_treasury_disbursement(
        tenant=tenant, amount=amount, date=transfer_date,
        reference_type='treasury_bank_transfer', description=desc,
        user=user, treasury=treasury,
    )
    bank_movement = post_bank_account_receipt(
        tenant=tenant, amount=amount, date=transfer_date,
        reference_type='treasury_bank_transfer', description=desc,
        user=user, bank_account=bank_account,
    )

    transfer = TreasuryBankTransfer.objects.create(
        tenant=tenant,
        direction=TreasuryBankTransfer.DIRECTION_TREASURY_TO_BANK,
        treasury=treasury,
        bank_account=bank_account,
        amount=amount,
        transfer_date=transfer_date,
        notes=notes,
        treasury_movement=treasury_movement,
        bank_movement=bank_movement,
        created_by=user,
        updated_by=user,
    )
    if treasury_movement:
        treasury_movement.reference_id = transfer.id
        treasury_movement.save(update_fields=['reference_id'])
    if bank_movement:
        bank_movement.reference_id = transfer.id
        bank_movement.save(update_fields=['reference_id'])
    return transfer


@transaction.atomic
def post_bank_to_treasury_transfer(tenant, bank_account, treasury, amount, transfer_date, notes='', user=None):
    """سحب من حساب بنكي وإيداع في الخزينة (سحب نقدي من البنك)."""
    from apps.treasury.services import post_treasury_receipt
    from .models import TreasuryBankTransfer

    amount = Decimal(str(amount))
    desc = f'تحويل من الحساب البنكي {bank_account.name} إلى الخزينة {treasury.name}'

    bank_movement = post_bank_account_disbursement(
        tenant=tenant, amount=amount, date=transfer_date,
        reference_type='treasury_bank_transfer', description=desc,
        user=user, bank_account=bank_account,
    )
    treasury_movement = post_treasury_receipt(
        tenant=tenant, amount=amount, date=transfer_date,
        reference_type='treasury_bank_transfer', description=desc,
        user=user, treasury=treasury,
    )

    transfer = TreasuryBankTransfer.objects.create(
        tenant=tenant,
        direction=TreasuryBankTransfer.DIRECTION_BANK_TO_TREASURY,
        treasury=treasury,
        bank_account=bank_account,
        amount=amount,
        transfer_date=transfer_date,
        notes=notes,
        treasury_movement=treasury_movement,
        bank_movement=bank_movement,
        created_by=user,
        updated_by=user,
    )
    if treasury_movement:
        treasury_movement.reference_id = transfer.id
        treasury_movement.save(update_fields=['reference_id'])
    if bank_movement:
        bank_movement.reference_id = transfer.id
        bank_movement.save(update_fields=['reference_id'])
    return transfer
