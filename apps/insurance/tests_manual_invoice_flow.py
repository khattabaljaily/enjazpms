"""
فاتورة تأمين يدوية (مسودة ← تعديل ← تأكيد): مبلغ التأمين يُعاد حسابه عند تعديل المسودة،
والتأكيد من زر الفاتورة ينشئ مطالبة التأمين، ويُخصم المخزون ويُرحَّل تحمل المريض للخزينة.
"""
import json
from decimal import Decimal

from django.urls import reverse

from apps.core.test_utils import TenantTestCase, make_item
from apps.insurance.models import InsuranceClaim, InsuranceCompany, InsuranceMember
from apps.sales.models import SaleInvoice
from apps.stocks.models import StockQuantity
from apps.treasury.models import Treasury


class ManualInsuranceInvoiceFlowTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, cost_price='40', selling_price='100')
        self.set_quantity(self.item, self.stock, '100')
        company = InsuranceCompany.objects.create(
            tenant=self.tenant, name='شركة تأمين', code='INS', default_coverage_percent=Decimal('70'),
            settlement_period_days=30,
        )
        self.member = InsuranceMember.objects.create(
            tenant=self.tenant, insurance_company=company, card_number='C1', full_name='مشترك',
            coverage_percent=Decimal('70'),
        )

    def body(self, action, qty=1, cash='30'):
        return json.dumps({
            'action': action,
            'header': {'stock_id': self.stock.id, 'invoice_date': '2026-10-19', 'payment_method': 'mixed',
                       'cash_amount': cash, 'bank_amount': '0', 'customer_id': None,
                       'insurance_member_id': self.member.id, 'insurance_card_number': 'C1'},
            'lines': [{'item_id': self.item.id, 'quantity': qty, 'unit_price': '100', 'tax_rate': 0,
                       'cost_price_snapshot': 40}],
        })

    def post(self, url, payload):
        return self.client.post(url, payload, content_type='application/json')

    def state(self):
        invoice = SaleInvoice.objects.filter(tenant=self.tenant).order_by('-id').first()
        qty = StockQuantity.objects.get(stock=self.stock, item=self.item).quantity
        cash = sum(t.current_balance for t in Treasury.objects.filter(tenant=self.tenant))
        return invoice, qty, cash

    def test_confirm_button_on_draft_creates_claim_deducts_stock_and_posts_patient_share(self):
        self.post(reverse('sales:invoice_create'), self.body('save_draft'))
        invoice, qty, cash = self.state()
        self.assertEqual((invoice.status, qty, cash), ('draft', Decimal('100'), Decimal('0')))
        self.assertFalse(InsuranceClaim.objects.filter(tenant=self.tenant).exists())

        resp = self.post(reverse('sales:invoice_confirm', args=[invoice.pk]), '{}')
        self.assertTrue(resp.json()['success'], resp.content)

        invoice, qty, cash = self.state()
        self.assertEqual(invoice.status, 'confirmed')
        self.assertEqual(qty, Decimal('99'))
        self.assertEqual(cash, Decimal('30'))
        claim = InsuranceClaim.objects.get(tenant=self.tenant)
        self.assertEqual(claim.covered_amount, Decimal('70'))

    def test_editing_draft_recomputes_expected_insurance_amount(self):
        self.post(reverse('sales:invoice_create'), self.body('save_draft'))
        invoice, _, _ = self.state()
        self.assertEqual(invoice.insurance_amount, Decimal('70'))

        resp = self.post(reverse('sales:invoice_edit', args=[invoice.pk]), self.body('confirm', qty=2, cash='60'))
        self.assertTrue(resp.json()['success'], resp.content)

        invoice, qty, cash = self.state()
        self.assertEqual((invoice.grand_total, invoice.insurance_amount), (Decimal('200'), Decimal('140')))
        self.assertEqual((qty, cash), (Decimal('98'), Decimal('60')))
        self.assertEqual(InsuranceClaim.objects.get(tenant=self.tenant).covered_amount, Decimal('140'))
