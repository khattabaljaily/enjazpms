"""
اختبارات عمولة المندوب عبر كل تركيبة (commission_basis × commission_type)
المتاحة فعلياً في النظام — apps/agents/services.py + apps/agents/models.py.

ملاحظة عن bug #8 من مراجعة الكود: مندوب بأساس "الفاتورة والتحصيل معاً" (both)
ونوع "مبلغ ثابت" (fixed) يُصرف له عند تأكيد الفاتورة commission_rate،
وعند اكتمال التحصيل commission_rate_collection — مبلغان مختلفان من حقلين
منفصلين في النظام أصلاً وليس نفس المبلغ مرتين. تم التأكد من المستخدم أن هذا
هو السلوك المقصود (راجع test_both_fixed_pays_two_distinct_configured_amounts) —
لم يُعدَّل أي كود لهذا البند، فقط وُثِّق بالاختبار.
"""
from datetime import date
from decimal import Decimal

from apps.agents.models import AgentLedger
from apps.core.test_utils import TenantTestCase, make_agent, make_customer, make_item
from apps.sales.models import SaleInvoice, SaleInvoiceLine, SaleReturn, SaleReturnLine
from apps.sales.services import cancel_sale_invoice, confirm_sale_invoice, confirm_sale_return, record_customer_payment


class AgentCommissionTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, cost_price='40', selling_price='100')
        self.set_quantity(self.item, self.stock, '100')
        self.customer = make_customer(self.tenant, credit_limit='10000')

    def make_sale(self, agent, payment_method='cash', quantity='10', customer=None):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, agent=agent,
            customer=customer or self.customer, invoice_date=date(2026, 9, 13),
            status='draft', payment_method=payment_method,
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=self.item,
            quantity=Decimal(quantity), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        return invoice  # grand_total = 1000

    def commission_total(self, agent):
        from django.db.models import Sum
        return AgentLedger.objects.filter(tenant=self.tenant, agent=agent).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    def test_invoice_percentage_commission_posted_once_at_confirm(self):
        agent = make_agent(self.tenant, commission_basis='invoice', commission_type='percentage', commission_rate='10')
        invoice = self.make_sale(agent, payment_method='cash')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('100.00'))  # 10% of 1000

    def test_invoice_fixed_commission_ignores_grand_total(self):
        agent = make_agent(self.tenant, commission_basis='invoice', commission_type='fixed', commission_rate='75')
        invoice = self.make_sale(agent, payment_method='cash')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('75.00'))

    def test_none_type_pays_no_commission_regardless_of_basis(self):
        agent = make_agent(self.tenant, commission_basis='both', commission_type='none', commission_rate='10')
        invoice = self.make_sale(agent, payment_method='cash')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('0'))

    def test_collection_percentage_commission_accrues_per_payment(self):
        agent = make_agent(self.tenant, commission_basis='collection', commission_type='percentage', commission_rate='5')
        invoice = self.make_sale(agent, payment_method='credit')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('0'), 'لا عمولة عند التأكيد لأساس collection')

        record_customer_payment(invoice, Decimal('600'), 'cash', date(2026, 9, 13))
        self.assertEqual(self.commission_total(agent), Decimal('30.00'))  # 5% of 600

        record_customer_payment(invoice, Decimal('400'), 'cash', date(2026, 9, 13))
        self.assertEqual(self.commission_total(agent), Decimal('50.00'))  # + 5% of 400

    def test_collection_fixed_commission_fires_once_on_full_payment_only(self):
        agent = make_agent(self.tenant, commission_basis='collection', commission_type='fixed', commission_rate='100')
        invoice = self.make_sale(agent, payment_method='credit')
        confirm_sale_invoice(invoice, self.user)

        record_customer_payment(invoice, Decimal('400'), 'cash', date(2026, 9, 13))
        self.assertEqual(self.commission_total(agent), Decimal('0'), 'لم يكتمل التحصيل بعد')

        record_customer_payment(invoice, Decimal('600'), 'cash', date(2026, 9, 13))
        self.assertEqual(self.commission_total(agent), Decimal('100.00'))

    def test_both_percentage_pays_invoice_and_collection_independently(self):
        agent = make_agent(
            self.tenant, commission_basis='both', commission_type='percentage',
            commission_rate='10', commission_rate_collection='4',
        )
        invoice = self.make_sale(agent, payment_method='credit')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('100.00'), 'عمولة الفاتورة: 10% من 1000')

        record_customer_payment(invoice, Decimal('1000'), 'cash', date(2026, 9, 13))
        self.assertEqual(self.commission_total(agent), Decimal('140.00'), '+ عمولة تحصيل: 4% من 1000')

    def test_both_fixed_pays_two_distinct_configured_amounts(self):
        """
        السلوك المؤكَّد من المستخدم كمقصود: مبلغان مختلفان من حقلين منفصلين،
        وليس نفس المبلغ مرتين.
        """
        agent = make_agent(
            self.tenant, commission_basis='both', commission_type='fixed',
            commission_rate='50', commission_rate_collection='30',
        )
        invoice = self.make_sale(agent, payment_method='credit')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('50.00'), 'عمولة الفاتورة الثابتة')

        record_customer_payment(invoice, Decimal('1000'), 'cash', date(2026, 9, 13))
        self.assertEqual(self.commission_total(agent), Decimal('80.00'), '50 + 30 — مبلغان منفصلان فعلاً')

    def test_cancel_invoice_reverses_invoice_commission_fully(self):
        agent = make_agent(self.tenant, commission_basis='invoice', commission_type='percentage', commission_rate='10')
        invoice = self.make_sale(agent, payment_method='cash')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('100.00'))

        cancel_sale_invoice(invoice, self.user, 'اختبار')
        self.assertEqual(self.commission_total(agent), Decimal('0.00'))

    def test_partial_return_reverses_invoice_commission_proportionally(self):
        agent = make_agent(self.tenant, commission_basis='invoice', commission_type='percentage', commission_rate='10')
        invoice = self.make_sale(agent, payment_method='cash', quantity='10')
        confirm_sale_invoice(invoice, self.user)
        self.assertEqual(self.commission_total(agent), Decimal('100.00'))

        line = invoice.lines.get()
        sale_return = SaleReturn.objects.create(
            tenant=self.tenant, original_invoice=invoice, return_date=date(2026, 9, 13), refund_method='cash',
        )
        SaleReturnLine.objects.create(
            tenant=self.tenant, sale_return=sale_return, invoice_line=line,
            item=self.item, returned_quantity=Decimal('4'), unit_price=Decimal('100'),  # 40% returned
        )
        confirm_sale_return(sale_return, self.user)
        # عمولة الفاتورة الأصلية 100 - (100 * 40%) = 60
        self.assertEqual(self.commission_total(agent), Decimal('60.00'))
