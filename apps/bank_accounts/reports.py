"""
تقارير الحسابات البنكية
==========================
"""

from datetime import timedelta
from decimal import Decimal
from django.utils import timezone

from apps.treasury.reports import REFERENCE_TYPE_AR as _TREASURY_REFERENCE_TYPE_AR

from .models import BankAccount, BankAccountMovement


def format_number(value, decimals=2):
    try:
        if decimals == 0:
            return f"{int(value):,}"
        return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


REFERENCE_TYPE_AR = {
    **_TREASURY_REFERENCE_TYPE_AR,
    'transfer': 'تحويل بين حسابات بنكية',
    'treasury_bank_transfer': 'تحويل بين الخزينة وحساب بنكي',
    'supplier_payment_bank': 'دفعة مورد بنكي',
    'supplier_payment_bank_cancel': 'إلغاء دفعة مورد بنكي',
    'customer_payment_bank': 'دفعة عميل بنكي',
    'customer_payment_bank_cancel': 'إلغاء دفعة عميل بنكي',
}


class BankAccountReportGenerator:

    def __init__(self, tenant, start_date=None, end_date=None, branch=None):
        self.tenant = tenant
        self.branch = branch
        self.start_date = start_date or (timezone.localdate() - timedelta(days=30))
        self.end_date = end_date or timezone.localdate()

    def get_balances_report(self):
        """أرصدة جميع الحسابات البنكية لحظياً"""
        accounts = BankAccount.objects.filter(tenant=self.tenant, is_active=True).for_branch(self.branch).order_by('name')
        data = []
        for a in accounts:
            data.append({
                'name': a.name,
                'bank_name': a.bank_name,
                'current_balance': format_number(float(a.current_balance), 2),
                'current_balance_raw': float(a.current_balance),
            })

        total = sum(r['current_balance_raw'] for r in data)

        return {
            'data': data,
            'summary': {
                'account_count': format_number(len(data), 0),
                'total_balance': format_number(total, 2),
            },
        }

    def get_statement_report(self, bank_account_id):
        """كشف حساب بنكي محدد خلال فترة"""
        try:
            account = BankAccount.objects.get(pk=bank_account_id, tenant=self.tenant)
        except BankAccount.DoesNotExist:
            return None

        movements = BankAccountMovement.objects.filter(
            tenant=self.tenant,
            bank_account=account,
            movement_date__gte=self.start_date,
            movement_date__lte=self.end_date,
        ).order_by('-id')

        data = []
        total_receipts = Decimal('0')
        total_disbursements = Decimal('0')

        for m in movements:
            is_receipt = m.movement_type in ('receipt', 'adjustment')
            receipt_amt = float(m.amount) if is_receipt else 0
            disb_amt = float(m.amount) if not is_receipt else 0
            total_receipts += Decimal(str(receipt_amt))
            total_disbursements += Decimal(str(disb_amt))
            data.append({
                'movement_date': m.movement_date,
                'movement_type': m.get_movement_type_display(),
                'movement_type_key': m.movement_type,
                'description': m.description,
                'receipt': format_number(receipt_amt, 2) if receipt_amt else '—',
                'disbursement': format_number(disb_amt, 2) if disb_amt else '—',
                'running_balance': format_number(float(m.running_balance), 2),
                'reference_type': REFERENCE_TYPE_AR.get(m.reference_type, m.reference_type),
            })

        return {
            'bank_account': account,
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'total_receipts': format_number(float(total_receipts), 2),
                'total_disbursements': format_number(float(total_disbursements), 2),
                'net': format_number(float(total_receipts - total_disbursements), 2),
                'closing_balance': format_number(float(account.current_balance), 2),
            },
            'data': data,
        }

    def get_movements_summary(self, bank_account_id=None):
        """حركات حساب بنكي محدد بالفترة مرتبة من الأقدم للأحدث"""
        from apps.core.utils import filter_by_branch_via
        movements = filter_by_branch_via(BankAccountMovement.objects.filter(
            tenant=self.tenant,
            movement_date__gte=self.start_date,
            movement_date__lte=self.end_date,
        ), self.branch, field='bank_account__branch').select_related('bank_account').order_by('movement_date', 'id')

        if bank_account_id:
            movements = movements.filter(bank_account_id=bank_account_id)

        data = []
        total_receipts = Decimal('0')
        total_disbursements = Decimal('0')

        for m in movements:
            is_receipt = m.movement_type in ('receipt', 'adjustment')
            r = float(m.amount) if is_receipt else 0
            d = float(m.amount) if not is_receipt else 0
            total_receipts += Decimal(str(r))
            total_disbursements += Decimal(str(d))
            data.append({
                'movement_date': m.movement_date,
                'bank_account_name': m.bank_account.name,
                'movement_type': m.get_movement_type_display(),
                'movement_type_key': m.movement_type,
                'description': m.description,
                'receipt': format_number(r, 2) if r else '—',
                'disbursement': format_number(d, 2) if d else '—',
                'running_balance': format_number(float(m.running_balance), 2),
            })

        accounts = BankAccount.objects.filter(tenant=self.tenant, is_active=True).for_branch(self.branch).order_by('name')

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'movement_count': format_number(len(data), 0),
                'total_receipts': format_number(float(total_receipts), 2),
                'total_disbursements': format_number(float(total_disbursements), 2),
                'net': format_number(float(total_receipts - total_disbursements), 2),
            },
            'data': data,
            'accounts': accounts,
        }
