"""
تقارير التأمين
================
"""
from django.utils import timezone

from .models import InsuranceClaim

_OPEN_STATUSES = ('submitted', 'approved', 'partially_approved', 'partially_paid')


def format_number(value, decimals=2):
    try:
        if decimals == 0:
            return f"{int(value):,}"
        return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


class InsuranceReportGenerator:
    """فئة شاملة لإنشاء تقارير التأمين"""

    def __init__(self, tenant):
        self.tenant = tenant

    def get_claims_aging_report(self):
        """تقرير أعمار مطالبات التأمين — المطالبات التي لا تزال مستحقة على شركات التأمين"""
        today = timezone.localdate()

        claims = (
            InsuranceClaim.objects
            .filter(tenant=self.tenant, status__in=_OPEN_STATUSES)
            .select_related('customer', 'insurance_company', 'invoice')
        )

        buckets = {
            'not_due': {'label': 'لسه ما استحقتش', 'count': 0, 'amount': 0},
            'due_0_30': {'label': '0-30 يوم', 'count': 0, 'amount': 0},
            'due_31_60': {'label': '31-60 يوم', 'count': 0, 'amount': 0},
            'due_60_plus': {'label': 'أكثر من 60 يوم', 'count': 0, 'amount': 0},
        }

        data = []
        total_remaining = 0
        for claim in claims:
            remaining = claim.remaining_amount
            if remaining <= 0:
                continue

            days_overdue = None
            bucket_key = 'not_due'
            if claim.due_date:
                days_overdue = (today - claim.due_date).days
                if days_overdue > 60:
                    bucket_key = 'due_60_plus'
                elif days_overdue > 30:
                    bucket_key = 'due_31_60'
                elif days_overdue > 0:
                    bucket_key = 'due_0_30'

            buckets[bucket_key]['count'] += 1
            buckets[bucket_key]['amount'] += float(remaining)
            total_remaining += float(remaining)

            data.append({
                'claim_number': claim.claim_number,
                'invoice_number': claim.invoice.invoice_number,
                'customer_name': claim.customer.name if claim.customer else 'بدون عميل مسجَّل',
                'insurance_company_name': claim.insurance_company.name,
                'status_display': claim.get_status_display(),
                'due_date': claim.due_date,
                'days_overdue': days_overdue,
                'bucket': buckets[bucket_key]['label'],
                'remaining_amount': format_number(remaining),
            })

        # الأكثر تأخراً أولاً؛ المطالبات غير المستحقة بعد في الآخر
        data.sort(key=lambda r: (r['days_overdue'] is None, -(r['days_overdue'] or 0)))

        for b in buckets.values():
            b['amount'] = format_number(b['amount'])

        return {
            'summary': {
                'total_open_claims': format_number(len(data), 0),
                'total_remaining': format_number(total_remaining),
                'buckets': buckets,
            },
            'data': data,
        }
