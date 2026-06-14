"""
تقارير الخزينة
================
"""

from datetime import timedelta
from decimal import Decimal
from django.utils import timezone

from .models import Treasury, TreasuryMovement


def format_number(value, decimals=2):
    try:
        if decimals == 0:
            return f"{int(value):,}"
        return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


class TreasuryReportGenerator:

    def __init__(self, tenant, start_date=None, end_date=None):
        self.tenant = tenant
        self.start_date = start_date or (timezone.localdate() - timedelta(days=30))
        self.end_date = end_date or timezone.localdate()

    def get_balances_report(self):
        """أرصدة جميع الخزائن لحظياً"""
        treasuries = Treasury.objects.filter(tenant=self.tenant, is_active=True).order_by('name')
        data = []
        for t in treasuries:
            data.append({
                'name': t.name,
                'code': t.code,
                'current_balance': format_number(float(t.current_balance), 2),
                'current_balance_raw': float(t.current_balance),
            })

        total = sum(r['current_balance_raw'] for r in data)

        return {
            'data': data,
            'summary': {
                'treasury_count': format_number(len(data), 0),
                'total_balance': format_number(total, 2),
            },
        }

    def get_statement_report(self, treasury_id):
        """كشف حساب خزينة محددة خلال فترة"""
        try:
            treasury = Treasury.objects.get(pk=treasury_id, tenant=self.tenant)
        except Treasury.DoesNotExist:
            return None

        movements = TreasuryMovement.objects.filter(
            tenant=self.tenant,
            treasury=treasury,
            movement_date__gte=self.start_date,
            movement_date__lte=self.end_date,
        ).order_by('movement_date', 'id')

        data = []
        total_receipts = Decimal('0')
        total_disbursements = Decimal('0')

        for m in movements:
            is_receipt = m.movement_type == 'receipt'
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
                'reference_type': m.reference_type,
            })

        return {
            'treasury': treasury,
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'total_receipts': format_number(float(total_receipts), 2),
                'total_disbursements': format_number(float(total_disbursements), 2),
                'net': format_number(float(total_receipts - total_disbursements), 2),
                'closing_balance': format_number(float(treasury.current_balance), 2),
            },
            'data': data,
        }

    def get_movements_summary(self):
        """ملخص حركات الخزائن بالفترة (جميع الخزائن)"""
        movements = TreasuryMovement.objects.filter(
            tenant=self.tenant,
            movement_date__gte=self.start_date,
            movement_date__lte=self.end_date,
        ).select_related('treasury').order_by('-movement_date')

        data = []
        total_receipts = Decimal('0')
        total_disbursements = Decimal('0')

        for m in movements:
            is_receipt = m.movement_type == 'receipt'
            r = float(m.amount) if is_receipt else 0
            d = float(m.amount) if not is_receipt else 0
            total_receipts += Decimal(str(r))
            total_disbursements += Decimal(str(d))
            data.append({
                'movement_date': m.movement_date,
                'treasury_name': m.treasury.name,
                'movement_type': m.get_movement_type_display(),
                'movement_type_key': m.movement_type,
                'description': m.description,
                'receipt': format_number(r, 2) if r else '—',
                'disbursement': format_number(d, 2) if d else '—',
                'running_balance': format_number(float(m.running_balance), 2),
            })

        treasuries = Treasury.objects.filter(tenant=self.tenant, is_active=True).order_by('name')

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'movement_count': format_number(len(data), 0),
                'total_receipts': format_number(float(total_receipts), 2),
                'total_disbursements': format_number(float(total_disbursements), 2),
                'net': format_number(float(total_receipts - total_disbursements), 2),
            },
            'data': data,
            'treasuries': treasuries,
        }
