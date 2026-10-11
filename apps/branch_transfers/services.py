"""
خدمات تحويلات الفروع: إرسال، استلام (بالمبلغ الفعلي)، وإلغاء قبل الاستلام.
كل دالة ترفع ValueError برسالة عربية جاهزة للعرض.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.bank_accounts.models import BankAccount
from apps.bank_accounts.services import post_bank_account_disbursement, post_bank_account_receipt
from apps.treasury.models import Treasury
from apps.treasury.services import post_treasury_disbursement, post_treasury_receipt

from .models import BranchTransfer

KIND_LABELS = {'cash': 'خزينة', 'bank': 'حساب بنكي'}


def to_decimal(value, label='المبلغ'):
    try:
        number = Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{label} غير صالح.')
    if not number.is_finite():
        raise ValueError(f'{label} غير صالح.')
    return number.quantize(Decimal('0.01'))


def currency_of(tenant, account):
    return (account.currency or tenant.currency or '').strip()


def _lock(kind, account):
    model = Treasury if kind == 'cash' else BankAccount
    return model.objects.select_for_update().get(pk=account.pk)


def _post(kind, direction, tenant, account, amount, date, ref_type, ref_id, desc, user):
    """حركة على خزينة (cash) أو حساب بنكي (bank): direction = 'out' (صرف) أو 'in' (إيداع)."""
    if kind == 'cash':
        fn = post_treasury_disbursement if direction == 'out' else post_treasury_receipt
        return fn(tenant=tenant, amount=amount, date=date, reference_type=ref_type, reference_id=ref_id,
                  description=desc, user=user, treasury=account)
    fn = post_bank_account_disbursement if direction == 'out' else post_bank_account_receipt
    return fn(tenant=tenant, amount=amount, date=date, reference_type=ref_type, reference_id=ref_id,
              description=desc, user=user, bank_account=account)


def _label(kind, account):
    return f'{KIND_LABELS[kind]} {account.name}'


def _notify(transfer, *, to_branch, title, message):
    from apps.notifications.models import Notification
    Notification.objects.create(
        tenant=transfer.tenant, branch=transfer.branch if to_branch else None,
        notification_type='general', title=title, message=message,
        link=reverse('branch_transfers:detail', args=[transfer.pk]), priority='medium')


def validate_sides(tenant, direction, branch, source_kind, source, dest_kind, dest):
    """يتحقق من أن الطرفين في مكانهما الصحيح (إدارة/فرع) ونشطان وبنفس العملة."""
    if direction not in dict(BranchTransfer.DIRECTION_CHOICES):
        raise ValueError('اتجاه التحويل غير صالح.')
    if source_kind not in KIND_LABELS or dest_kind not in KIND_LABELS:
        raise ValueError('اختر نوع التحويل: خزينة أو حساب بنكي.')
    if source_kind != dest_kind:
        raise ValueError('التحويل بين الإدارة والفرع من خزينة إلى خزينة، أو من حساب بنكي إلى حساب بنكي؛ '
                         'لا يجوز من خزينة إلى حساب بنكي ولا العكس.')
    if source is None or dest is None:
        raise ValueError('اختر حساب المصدر وحساب الوجهة.')
    if branch is None or not branch.is_active or branch.tenant_id != tenant.pk:
        raise ValueError('اختر فرعاً نشطاً.')
    for account in (source, dest):
        if account.tenant_id != tenant.pk or not account.is_active:
            raise ValueError(f'«{account.name}» غير نشط أو غير متاح.')
    head, side = (source, dest) if direction == 'to_branch' else (dest, source)
    if not head.is_head_office:
        raise ValueError('طرف الإدارة يجب أن يكون خزينة أو حساباً بنكياً للإدارة المركزية.')
    if side.is_head_office or side.branch_id != branch.pk:
        raise ValueError(f'طرف الفرع يجب أن يتبع فرع «{branch.name}».')
    src_cur, dst_cur = currency_of(tenant, source), currency_of(tenant, dest)
    if src_cur != dst_cur:
        raise ValueError(
            f'عملة الطرفين يجب أن تتطابق: المصدر بعملة {src_cur} والوجهة بعملة {dst_cur}. '
            'لا تحويل بسعر صرف بين الإدارة والفروع.')
    return src_cur


@transaction.atomic
def create_transfer(tenant, user, *, direction, branch, source_kind, source, dest_kind, dest, amount,
                    transfer_date, notes=''):
    """يُرسل التحويل: يخرج المبلغ من المصدر فوراً ويبقى «في الطريق» حتى يؤكد الطرف الآخر استلامه."""
    amount = to_decimal(amount)
    if amount <= 0:
        raise ValueError('يجب أن يكون المبلغ أكبر من صفر.')
    currency = validate_sides(tenant, direction, branch, source_kind, source, dest_kind, dest)
    source = _lock(source_kind, source)
    transfer = BranchTransfer.objects.create(
        tenant=tenant, direction=direction, branch=branch, transfer_date=transfer_date, amount=amount,
        currency=currency, notes=notes or '',
        from_treasury=source if source_kind == 'cash' else None,
        from_bank=source if source_kind == 'bank' else None,
        to_treasury=dest if dest_kind == 'cash' else None,
        to_bank=dest if dest_kind == 'bank' else None,
        sent_at=timezone.now(), created_by=user, updated_by=user)
    desc = f'تحويل فروع {transfer.number}: من {_label(source_kind, source)} إلى {_label(dest_kind, dest)}'
    _post(source_kind, 'out', tenant, source, amount, transfer_date, 'branch_transfer', transfer.pk, desc, user)
    _notify(transfer, to_branch=(direction == 'to_branch'),
            title='تحويل مالي في الطريق',
            message=f'تحويل {transfer.number} بمبلغ {amount} {currency} من '
                    f'{"الإدارة إلى فرع " + branch.name if direction == "to_branch" else "فرع " + branch.name + " إلى الإدارة"}. '
                    'أكّد المبلغ المستلَم عند وصوله.')
    return transfer


@transaction.atomic
def receive_transfer(transfer, received_amount, user):
    """
    يؤكد الطرف المستلِم المبلغ الفعلي (من صفر إلى المحوَّل): يدخل رصيد الوجهة، ويعود الفرق
    تلقائياً للمصدر مع تعليم التحويل. الصفر = رفض الاستلام (يعود المبلغ كله).
    """
    transfer = BranchTransfer.objects.select_for_update().select_related(
        'from_treasury', 'from_bank', 'to_treasury', 'to_bank', 'branch').get(pk=transfer.pk)
    if transfer.status != 'in_transit':
        raise ValueError('يؤكد استلام التحويلات التي في الطريق فقط.')
    received = to_decimal(received_amount, 'المبلغ المستلَم')
    if received < 0 or received > transfer.amount:
        raise ValueError(f'المبلغ المستلَم يجب أن يكون بين 0 و{transfer.amount}.')
    tenant, today = transfer.tenant, timezone.localdate()
    source, dest = transfer.source, transfer.destination
    if received > 0:
        _post(transfer.destination_kind, 'in', tenant, _lock(transfer.destination_kind, dest), received,
              today, 'branch_transfer', transfer.pk,
              f'استلام تحويل فروع {transfer.number} من {_label(transfer.source_kind, source)}', user)
    diff = transfer.amount - received
    if diff > 0:
        _post(transfer.source_kind, 'in', tenant, _lock(transfer.source_kind, source), diff, today,
              'branch_transfer_return', transfer.pk,
              f'إعادة فرق استلام تحويل فروع {transfer.number}', user)
    transfer.status = 'received'
    transfer.received_amount = received
    transfer.has_difference = diff > 0
    transfer.received_at = timezone.now()
    transfer.received_by = user
    transfer.updated_by = user
    transfer.save(update_fields=['status', 'received_amount', 'has_difference', 'received_at', 'received_by',
                                 'updated_by', 'updated_at'])
    sender_is_ho = transfer.sender_is_head_office
    _notify(transfer, to_branch=not sender_is_ho,
            title='تم استلام التحويل' if diff == 0 else 'استلام تحويل بفرق',
            message=f'تحويل {transfer.number}: استُلم {received} من {transfer.amount} {transfer.currency}.'
                    + (f' عاد الفرق {diff} إلى المصدر.' if diff > 0 else ''))
    return transfer


@transaction.atomic
def cancel_transfer(transfer, user):
    """إلغاء قبل الاستلام: يعود المبلغ كاملاً إلى المصدر."""
    transfer = BranchTransfer.objects.select_for_update().select_related(
        'from_treasury', 'from_bank', 'to_treasury', 'to_bank', 'branch').get(pk=transfer.pk)
    if transfer.status != 'in_transit':
        raise ValueError('لا يمكن إلغاء تحويل تم استلامه أو ألغي من قبل.')
    _post(transfer.source_kind, 'in', transfer.tenant, _lock(transfer.source_kind, transfer.source),
          transfer.amount, timezone.localdate(), 'branch_transfer_cancel', transfer.pk,
          f'إلغاء تحويل فروع {transfer.number}', user)
    transfer.status = 'cancelled'
    transfer.cancelled_at = timezone.now()
    transfer.cancelled_by = user
    transfer.updated_by = user
    transfer.save(update_fields=['status', 'cancelled_at', 'cancelled_by', 'updated_by', 'updated_at'])
    _notify(transfer, to_branch=transfer.sender_is_head_office,  # يُخطَر الطرف المستلِم بالإلغاء
            title='أُلغي التحويل', message=f'أُلغي تحويل {transfer.number} قبل الاستلام وعاد المبلغ إلى المصدر.')
    return transfer
