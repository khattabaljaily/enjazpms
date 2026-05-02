from decimal import Decimal

from django.db import transaction

from apps.treasury.services import post_treasury_disbursement, post_treasury_movement


@transaction.atomic
def confirm_expense(expense, user=None):
    """
    Confirm an expense.
    Cash payments deduct from the linked treasury.
    Bank payments do not affect treasury and are confirmed without a treasury movement.
    """
    if expense.status != expense.STATUS_DRAFT:
        raise ValueError('المصروف ليس في حالة مسودة.')

    if expense.payment_method == expense.PAYMENT_CASH:
        movement = post_treasury_disbursement(
            tenant=expense.tenant,
            amount=expense.amount,
            date=expense.expense_date,
            reference_type='expense',
            reference_id=expense.pk,
            description=f'مصروف {expense.code}: {expense.description}',
            user=user,
            treasury=expense.treasury,
        )
        expense.treasury_movement = movement
    else:
        expense.treasury_movement = None

    expense.status = expense.STATUS_CONFIRMED
    expense.updated_by = user
    expense.save(update_fields=['status', 'treasury_movement', 'updated_by', 'updated_at'])
    return expense


@transaction.atomic
def cancel_expense(expense, user=None):
    """
    Cancel a confirmed expense: reverse the treasury movement.
    """
    if expense.status not in (expense.STATUS_DRAFT, expense.STATUS_CONFIRMED):
        raise ValueError('لا يمكن إلغاء مصروف بهذه الحالة.')

    if expense.treasury_movement:
        # Reverse: receipt back to treasury
        post_treasury_movement(
            tenant=expense.tenant,
            movement_type='receipt',
            amount=expense.amount,
            date=expense.expense_date,
            reference_type='expense_cancel',
            reference_id=expense.pk,
            description=f'إلغاء مصروف {expense.code}',
            user=user,
            treasury=expense.treasury_movement.treasury,
        )
        expense.treasury_movement = None

    expense.status = expense.STATUS_CANCELLED
    expense.updated_by = user
    expense.save(update_fields=['status', 'treasury_movement', 'updated_by', 'updated_at'])
    return expense
