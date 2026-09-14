"""
اختبارات مباشرة لـ apps/bank_accounts/services.py: القبض/الصرف، رفض الصرف
الأكبر من الرصيد، ورفض الحركة بلا حساب بنكي محدَّد.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_bank_account
from apps.bank_accounts.models import BankAccountMovement
from apps.bank_accounts.services import post_bank_account_disbursement, post_bank_account_receipt


class BankAccountMovementTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.bank = make_bank_account(self.tenant, current_balance='0')

    def test_receipt_increases_balance_and_records_movement(self):
        post_bank_account_receipt(self.tenant, Decimal('500'), date(2026, 9, 13), bank_account=self.bank)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.current_balance, Decimal('500.00'))
        self.assertEqual(
            BankAccountMovement.objects.filter(tenant=self.tenant, bank_account=self.bank, movement_type='receipt').count(),
            1,
        )

    def test_disbursement_beyond_balance_is_rejected(self):
        post_bank_account_receipt(self.tenant, Decimal('100'), date(2026, 9, 13), bank_account=self.bank)
        with self.assertRaises(ValueError):
            post_bank_account_disbursement(self.tenant, Decimal('200'), date(2026, 9, 13), bank_account=self.bank)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.current_balance, Decimal('100.00'))

    def test_movement_without_bank_account_is_rejected(self):
        with self.assertRaises(ValueError):
            post_bank_account_receipt(self.tenant, Decimal('100'), date(2026, 9, 13), bank_account=None)
