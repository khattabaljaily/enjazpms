"""
اختبارات دورة حياة مطالبة التأمين غير المغطاة في apps/core/tests_full.py
(التي تغطي فقط المسار السعيد: تقديم → اعتماد كامل → تسوية نقدية):
  - اعتماد جزئي ينتج خسارة (writeoff) تُشطب من رصيد الفاتورة.
  - رفض كامل ينتج خسارة بكامل المبلغ المطالَب به.
  - إلغاء مطالبة مسودة (بلا أثر مالي) مقابل إلغاء مطالبة مُقدَّمة (يعكس القيد).
  - رفض إلغاء مطالبة استُلم جزء من تسويتها.
  - رفض تسوية بمبلغ أكبر من المتبقي على المطالبة.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from apps.core.test_utils import TenantTestCase, make_customer, make_item, make_treasury
from apps.insurance.models import InsuranceClaimSettlement, InsuranceCompany, InsuranceMember
from apps.insurance.services import (
    cancel_claim,
    create_claim_from_sale,
    record_claim_response,
    settle_claim_payment,
    submit_claim,
)
from apps.sales.models import SaleInvoice, SaleInvoiceLine
from apps.sales.services import confirm_sale_invoice


class InsuranceClaimLifecycleTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, cost_price='40', selling_price='100')
        self.set_quantity(self.item, self.stock, '100')
        self.customer = make_customer(self.tenant)
        self.treasury = make_treasury(self.tenant, current_balance='0')
        self.company = InsuranceCompany.objects.create(
            tenant=self.tenant, name='شركة تأمين اختبار', code='INS-T',
            default_coverage_percent=Decimal('60'), settlement_period_days=30,
        )
        self.member = InsuranceMember.objects.create(
            tenant=self.tenant, insurance_company=self.company, card_number='CARD-T',
            full_name='مشترك اختبار', coverage_percent=Decimal('60'),
        )

    def make_covered_sale(self):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, customer=self.customer,
            invoice_date=date(2026, 9, 13), status='draft', payment_method='mixed',
            insurance_member=self.member, insurance_card_number='CARD-T',
            cash_amount=Decimal('40'), insurance_amount=Decimal('60'),
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=self.item,
            quantity=Decimal('1'), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        confirm_sale_invoice(invoice, self.user)
        claim = create_claim_from_sale(invoice, self.tenant, Decimal('60'), self.user)
        return invoice, claim

    def settlement_total(self):
        return InsuranceClaimSettlement.objects.filter(
            tenant=self.tenant, insurance_company=self.company
        ).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    def test_partial_approval_writes_off_the_shortfall(self):
        invoice, claim = self.make_covered_sale()
        submit_claim(claim, self.user)
        record_claim_response(claim, Decimal('40'), 'partially_approved', '', self.user)
        claim.refresh_from_db()
        invoice.refresh_from_db()

        self.assertEqual(claim.status, 'partially_approved')
        self.assertEqual(claim.approved_amount, Decimal('40.00'))
        # المطالَب به 60 والمعتمد 40 → خسارة 20 تُشطب من رصيد الفاتورة
        self.assertEqual(invoice.remaining_amount, Decimal('40.00'))  # 100 - 40(cash) - 20(writeoff) = 40

    def test_full_rejection_writes_off_the_entire_covered_amount(self):
        invoice, claim = self.make_covered_sale()
        submit_claim(claim, self.user)
        record_claim_response(claim, Decimal('0'), 'rejected', 'وثائق ناقصة', self.user)
        claim.refresh_from_db()
        invoice.refresh_from_db()

        self.assertEqual(claim.status, 'rejected')
        self.assertEqual(claim.rejection_reason, 'وثائق ناقصة')
        self.assertEqual(invoice.remaining_amount, Decimal('0.00'))  # 100 - 40(cash) - 60(writeoff) = 0

    def test_cancel_draft_claim_has_no_ledger_effect(self):
        invoice, claim = self.make_covered_sale()
        cancel_claim(claim, self.user, 'إلغاء قبل التقديم')
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'cancelled')
        self.assertEqual(self.settlement_total(), Decimal('0'))

    def test_cancel_submitted_claim_reverses_the_submission_ledger_entry(self):
        invoice, claim = self.make_covered_sale()
        submit_claim(claim, self.user)
        self.assertEqual(self.settlement_total(), Decimal('60.00'))

        cancel_claim(claim, self.user, 'العميل عدل رأيه')
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'cancelled')
        self.assertEqual(self.settlement_total(), Decimal('0.00'))

    def test_cancel_rejected_when_partially_paid(self):
        invoice, claim = self.make_covered_sale()
        submit_claim(claim, self.user)
        record_claim_response(claim, Decimal('60'), 'approved', '', self.user)
        settle_claim_payment(
            claim, Decimal('30'), date(2026, 9, 13), 'SET-1', self.user,
            treasury=self.treasury, received_method='cash',
        )
        claim.refresh_from_db()
        with self.assertRaises(ValueError):
            cancel_claim(claim, self.user, 'محاولة إلغاء بعد دفعة جزئية')

    def test_settlement_rejects_amount_beyond_remaining(self):
        invoice, claim = self.make_covered_sale()
        submit_claim(claim, self.user)
        record_claim_response(claim, Decimal('60'), 'approved', '', self.user)
        with self.assertRaises(ValueError):
            settle_claim_payment(
                claim, Decimal('100'), date(2026, 9, 13), 'SET-2', self.user,
                treasury=self.treasury, received_method='cash',
            )
