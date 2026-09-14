"""
اختبارات مباشرة لـ apps/treasury/services.py: القبض/الصرف، رفض الصرف الأكبر
من الرصيد، الرصيد الافتتاحي (إنشاء/تعديل/حذف عند الصفر)، وإعادة حساب
الأرصدة التراكمية.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_treasury
from apps.treasury.models import TreasuryMovement
from apps.treasury.services import (
    post_treasury_disbursement,
    post_treasury_receipt,
    recalculate_treasury_running_balances,
    set_opening_balance,
)


class TreasuryMovementTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.treasury = make_treasury(self.tenant, current_balance='0')

    def test_receipt_increases_balance_and_records_movement(self):
        post_treasury_receipt(self.tenant, Decimal('100'), date(2026, 9, 13), treasury=self.treasury)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('100.00'))
        self.assertEqual(
            TreasuryMovement.objects.filter(tenant=self.tenant, treasury=self.treasury, movement_type='receipt').count(),
            1,
        )

    def test_disbursement_decreases_balance(self):
        post_treasury_receipt(self.tenant, Decimal('100'), date(2026, 9, 13), treasury=self.treasury)
        post_treasury_disbursement(self.tenant, Decimal('30'), date(2026, 9, 13), treasury=self.treasury)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('70.00'))

    def test_disbursement_beyond_balance_is_rejected(self):
        post_treasury_receipt(self.tenant, Decimal('50'), date(2026, 9, 13), treasury=self.treasury)
        with self.assertRaises(ValueError):
            post_treasury_disbursement(self.tenant, Decimal('100'), date(2026, 9, 13), treasury=self.treasury)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('50.00'), 'يجب ألا يتغيّر الرصيد عند الرفض')

    def test_zero_or_negative_amount_is_a_no_op(self):
        result = post_treasury_receipt(self.tenant, Decimal('0'), date(2026, 9, 13), treasury=self.treasury)
        self.assertIsNone(result)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('0.00'))

    def test_set_opening_balance_creates_then_updates_then_removes_at_zero(self):
        set_opening_balance(self.tenant, self.treasury, Decimal('200'), date(2026, 9, 1), self.user)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('200'))

        set_opening_balance(self.tenant, self.treasury, Decimal('350'), date(2026, 9, 1), self.user)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('350'))
        self.assertEqual(
            TreasuryMovement.objects.filter(tenant=self.tenant, treasury=self.treasury, reference_type='opening_balance').count(),
            1,
        )

        set_opening_balance(self.tenant, self.treasury, Decimal('0'), date(2026, 9, 1), self.user)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('0'))
        self.assertFalse(
            TreasuryMovement.objects.filter(tenant=self.tenant, treasury=self.treasury, reference_type='opening_balance').exists()
        )

    def test_recalculate_running_balances_matches_chronological_order(self):
        post_treasury_receipt(self.tenant, Decimal('100'), date(2026, 9, 1), treasury=self.treasury)
        post_treasury_disbursement(self.tenant, Decimal('40'), date(2026, 9, 5), treasury=self.treasury)
        post_treasury_receipt(self.tenant, Decimal('20'), date(2026, 9, 3), treasury=self.treasury)

        final_balance = recalculate_treasury_running_balances(self.tenant, self.treasury, user=self.user)
        self.assertEqual(final_balance, Decimal('80'))  # 100 + 20 - 40

        movements = list(
            TreasuryMovement.objects.filter(tenant=self.tenant, treasury=self.treasury).order_by('movement_date', 'id')
        )
        self.assertEqual([m.running_balance for m in movements], [Decimal('100'), Decimal('120'), Decimal('80')])
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('80'))
