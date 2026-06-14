"""
تقارير المصروفات
================
"""

from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone

from .models import Expense, ExpenseCategory


def format_number(value, decimals=2):
    try:
        if decimals == 0:
            return f"{int(value):,}"
        return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


class ExpensesReportGenerator:

    def __init__(self, tenant, start_date=None, end_date=None):
        self.tenant = tenant
        self.start_date = start_date or (timezone.localdate() - timedelta(days=30))
        self.end_date = end_date or timezone.localdate()

    def get_summary_report(self):
        """ملخص المصروفات بالفترة"""
        expenses = Expense.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            expense_date__gte=self.start_date,
            expense_date__lte=self.end_date,
        )

        total = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        total_cash = expenses.filter(payment_method='cash').aggregate(t=Sum('amount'))['t'] or Decimal('0')
        total_bank = expenses.filter(payment_method='bank').aggregate(t=Sum('amount'))['t'] or Decimal('0')
        count = expenses.count()

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'expense_count': format_number(count, 0),
                'total_amount': format_number(float(total), 2),
                'total_cash': format_number(float(total_cash), 2),
                'total_bank': format_number(float(total_bank), 2),
                'avg_expense': format_number(float(total / count) if count else 0, 2),
            },
        }

    def get_by_category_report(self):
        """المصروفات حسب الفئة"""
        categories = ExpenseCategory.objects.filter(tenant=self.tenant).order_by('name')
        data = []
        for cat in categories:
            agg = Expense.objects.filter(
                tenant=self.tenant,
                category=cat,
                status='confirmed',
                expense_date__gte=self.start_date,
                expense_date__lte=self.end_date,
            ).aggregate(total=Sum('amount'), count=Sum('id'))

            count = Expense.objects.filter(
                tenant=self.tenant,
                category=cat,
                status='confirmed',
                expense_date__gte=self.start_date,
                expense_date__lte=self.end_date,
            ).count()

            total = float(agg['total'] or 0)
            if total > 0 or count > 0:
                data.append({
                    'category_name': cat.name,
                    'expense_count': format_number(count, 0),
                    'total_amount': format_number(total, 2),
                    'total_amount_raw': total,
                })

        # Uncategorized
        uncat = Expense.objects.filter(
            tenant=self.tenant,
            category=None,
            status='confirmed',
            expense_date__gte=self.start_date,
            expense_date__lte=self.end_date,
        )
        uncat_total = float(uncat.aggregate(t=Sum('amount'))['t'] or 0)
        if uncat_total > 0:
            data.append({
                'category_name': 'غير مصنف',
                'expense_count': format_number(uncat.count(), 0),
                'total_amount': format_number(uncat_total, 2),
                'total_amount_raw': uncat_total,
            })

        data.sort(key=lambda x: x['total_amount_raw'], reverse=True)
        grand_total = sum(r['total_amount_raw'] for r in data)

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'data': data,
            'grand_total': format_number(grand_total, 2),
        }

    def get_by_date_report(self, group_by='day'):
        """المصروفات حسب التاريخ"""
        expenses = Expense.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            expense_date__gte=self.start_date,
            expense_date__lte=self.end_date,
        ).order_by('expense_date')

        buckets = {}
        for e in expenses:
            if group_by == 'day':
                key = e.expense_date.strftime('%Y-%m-%d')
                label = e.expense_date.strftime('%d/%m/%Y')
            elif group_by == 'month':
                key = e.expense_date.strftime('%Y-%m')
                label = e.expense_date.strftime('%B %Y')
            else:
                key = e.expense_date.strftime('%Y-%m-%d')
                label = e.expense_date.strftime('%d/%m/%Y')

            if key not in buckets:
                buckets[key] = {'label': label, 'total': Decimal('0'), 'count': 0}
            buckets[key]['total'] += e.amount or 0
            buckets[key]['count'] += 1

        data = []
        for key in sorted(buckets):
            b = buckets[key]
            data.append({
                'label': b['label'],
                'expense_count': format_number(b['count'], 0),
                'total_amount': format_number(float(b['total']), 2),
            })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'group_by': group_by,
            'data': data,
        }

    def get_details_report(self, category_id=None):
        """قائمة تفصيلية بالمصروفات"""
        expenses = Expense.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            expense_date__gte=self.start_date,
            expense_date__lte=self.end_date,
        ).select_related('category', 'treasury').order_by('-expense_date')

        if category_id:
            expenses = expenses.filter(category_id=category_id)

        data = []
        for e in expenses:
            data.append({
                'expense_date': e.expense_date,
                'code': e.code,
                'description': e.description,
                'category_name': e.category.name if e.category else '—',
                'payment_method': e.get_payment_method_display(),
                'treasury_name': e.treasury.name if e.treasury else '—',
                'amount': format_number(float(e.amount), 2),
                'reference_number': e.reference_number,
            })

        total = sum(float(e.amount) for e in expenses)
        categories = ExpenseCategory.objects.filter(tenant=self.tenant).order_by('name')

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'data': data,
            'categories': categories,
            'grand_total': format_number(total, 2),
        }
