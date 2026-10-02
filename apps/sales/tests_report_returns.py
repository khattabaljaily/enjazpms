"""
الفواتير المرتجعة جزئياً أو كلياً يجب أن تبقى ضمن المبيعات في التقارير، وأن
يُطرح المرتجع مرة واحدة فقط في قائمة الدخل — كانت التقارير تحتسب status=confirmed
فقط، فتختفي الفاتورة كلها من المبيعات ثم يُطرح مرتجعها مرة ثانية.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_item
from apps.sales.models import SaleInvoice, SaleInvoiceLine, SaleReturn, SaleReturnLine
from apps.sales.reports import IncomeStatementGenerator, SalesReportGenerator
from apps.sales.services import confirm_sale_invoice, confirm_sale_return

DAY = date(2026, 9, 15)


class SalesReportsWithReturnsTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.item = make_item(self.tenant, name='شاحن', cost_price='40', selling_price='100')
        self.set_quantity(self.item, self.default_stock, '50')

    def _sale(self, qty):
        inv = SaleInvoice.objects.create(tenant=self.tenant, stock=self.default_stock, invoice_date=DAY,
                                         status='draft', payment_method='cash')
        line = SaleInvoiceLine(tenant=self.tenant, invoice=inv, item=self.item, quantity=Decimal(qty),
                               unit_price=Decimal('100'), cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'))
        line.calculate()
        line.save()
        inv.recalculate_totals()
        inv.save()
        confirm_sale_invoice(inv, self.user)
        return inv

    def _return(self, inv, qty):
        ret = SaleReturn.objects.create(tenant=self.tenant, original_invoice=inv, return_date=DAY, refund_method='cash')
        SaleReturnLine.objects.create(tenant=self.tenant, sale_return=ret, invoice_line=inv.lines.get(), item=self.item,
                                      returned_quantity=Decimal(qty), unit_price=Decimal('100'))
        confirm_sale_return(ret, self.user)

    def test_partially_and_fully_returned_invoices_stay_in_sales_and_net_once(self):
        a = self._sale('10')      # 1000، يُرجع منها 3
        b = self._sale('2')       # 200، تُرجع كلها
        self._sale('1')           # 100
        self._return(a, '3')
        self._return(b, '2')

        summary = SalesReportGenerator(self.tenant, DAY, DAY).get_summary_report()
        total = Decimal(str(summary['summary']['total_amount']).replace(',', ''))
        self.assertEqual(total, Decimal('1300.00'))  # إجمالي المبيعات قبل المرتجعات

        report = IncomeStatementGenerator(self.tenant, DAY, DAY).get_report()
        net_revenue = Decimal(str(report['revenue']['net_revenue']).replace(',', ''))
        cogs = Decimal(str(report['cost']['total_purchases']).replace(',', ''))
        self.assertEqual(net_revenue, Decimal('800.00'))   # 1300 − 500 مرتجعات
        self.assertEqual(cogs, Decimal('320.00'))          # 8 قطع صافية × 40
