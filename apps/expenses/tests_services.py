"""
اختبارات apps/expenses/services.py الإضافية غير المغطاة في
apps/core/tests_full.py (التي تغطي فقط تأكيد/إلغاء نقدي ناجح): رفض
التأكيد عند رصيد خزينة غير كافٍ، ومنع تأكيد مصروف مؤكَّد مسبقاً.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_treasury
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import confirm_expense


class ExpenseServiceTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.treasury = make_treasury(self.tenant, current_balance='100')
        self.category = ExpenseCategory.objects.create(tenant=self.tenant, name='تشغيل')

    def make_expense(self, amount):
        return Expense.objects.create(
            tenant=self.tenant, category=self.category, description='مصروف اختبار',
            amount=Decimal(amount), expense_date=date(2026, 9, 13),
            payment_method=Expense.PAYMENT_CASH, treasury=self.treasury,
        )

    def test_confirm_rejects_when_treasury_balance_insufficient(self):
        expense = self.make_expense('500')
        with self.assertRaises(ValueError):
            confirm_expense(expense, self.user)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('100.00'))
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.STATUS_DRAFT)

    def test_confirm_twice_is_rejected(self):
        expense = self.make_expense('50')
        confirm_expense(expense, self.user)
        with self.assertRaises(ValueError):
            confirm_expense(expense, self.user)
