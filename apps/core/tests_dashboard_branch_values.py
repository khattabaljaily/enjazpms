"""
اختبار أن أرقام لوحة التحكم والتحليلات المتقدمة تتغير فعلاً عند تغيير الفرع
(?branch=) في نسخة المؤسسات — وليس فقط أن الصفحة تُعرض بنجاح.
"""
from datetime import date
from decimal import Decimal

from django.utils import timezone

from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_item, make_stock, make_customer
from apps.sales.models import SaleInvoice, SaleInvoiceLine


class DashboardBranchValuesTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع 1')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع 2')
        self.s1 = make_stock(self.tenant, name='مخزن 1', branch=self.b1)
        self.s2 = make_stock(self.tenant, name='مخزن 2', branch=self.b2)
        self.item = make_item(self.tenant)
        self.c1 = make_customer(self.tenant, name='عميل 1', branch=self.b1)
        self.c2 = make_customer(self.tenant, name='عميل 2', branch=self.b2)
        self._sale(self.s1, self.c1, '100')
        self._sale(self.s2, self.c2, '700')
        self._sale(self.s2, self.c2, '300')

    def _sale(self, stock, customer, price):
        inv = SaleInvoice.objects.create(
            tenant=self.tenant, stock=stock, customer=customer,
            invoice_date=timezone.localdate(), status='confirmed', payment_method='credit',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=inv, item=self.item, quantity=Decimal('1'),
            unit_price=Decimal(price), cost_price_snapshot=Decimal('10'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        inv.recalculate_totals()
        inv.save()
        return inv

    def _stats(self, url, branch=None):
        resp = self.client.get(url, {'branch': branch.id} if branch else {})
        self.assertEqual(resp.status_code, 200)
        return resp.context

    def test_dashboard_values_change_with_branch(self):
        all_ = self._stats('/')['stats']
        one = self._stats('/', self.b1)['stats']
        two = self._stats('/', self.b2)['stats']
        self.assertEqual((all_['today_sales'], one['today_sales'], two['today_sales']), (1100.0, 100.0, 1000.0))
        self.assertEqual((all_['today_invoices'], one['today_invoices'], two['today_invoices']), (3, 1, 2))
        self.assertEqual((one['total_customers'], two['total_customers']), (1, 1))

    def test_analytics_values_change_with_branch(self):
        url = '/analytics/'
        k = lambda b=None: self._stats(url, b)['kpis']['sales']['value']
        a, o, t = k(), k(self.b1), k(self.b2)
        self.assertEqual(a, 1100.0)
        self.assertEqual((o, t), (100.0, 1000.0))

    def test_unbranched_stock_records_are_excluded_when_a_branch_is_selected(self):
        orphan_stock = make_stock(self.tenant, name='مخزن بلا فرع')
        self._sale(orphan_stock, self.c1, '5000')
        all_ = self._stats('/')['stats']
        one = self._stats('/', self.b1)['stats']
        two = self._stats('/', self.b2)['stats']
        self.assertEqual(all_['today_sales'], 6100.0)
        self.assertEqual((one['today_sales'], two['today_sales']), (100.0, 1000.0))
        k = lambda b=None: self._stats('/analytics/', b)['kpis']['sales']['value']
        self.assertEqual((k(), k(self.b1), k(self.b2)), (6100.0, 100.0, 1000.0))

    def test_total_products_follows_branch(self):
        from apps.stocks.models import StockQuantity
        item2 = make_item(self.tenant, name='صنف آخر')
        StockQuantity.objects.filter(tenant=self.tenant, stock=self.s1, item=self.item).update(quantity=5)
        StockQuantity.objects.filter(tenant=self.tenant, stock=self.s2, item=item2).update(quantity=3)
        self.assertEqual(self._stats('/')['stats']['total_products'], 2)
        self.assertEqual(self._stats('/', self.b1)['stats']['total_products'], 1)
        self.assertEqual(self._stats('/', self.b2)['stats']['total_products'], 1)
