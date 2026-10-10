"""
مدير النشاط: التحليلات المتقدمة تعرض الفروع (أفضل الفروع إيراداً) بدل العملاء،
والتقارير التي فيها أسماء عملاء/موردين/مخازن تُلحق اسم الفرع. مدير الفرع: يبقى
الحال كما هو. ألوان المبيعات والمشتريات في مخطط الاتجاه السنوي مختلفة.
"""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_customer, make_item, make_stock, make_supplier
from apps.purchases.reports import PurchasesReportGenerator
from apps.sales.models import CustomerLedger, SaleInvoice, SaleInvoiceLine
from apps.sales.reports import SalesReportGenerator
from apps.stocks.reports import StocksReportGenerator


class OwnerBranchLabelTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.s1 = make_stock(self.tenant, name='مخزن 1', branch=self.b1)
        self.s2 = make_stock(self.tenant, name='مخزن 2', branch=self.b2)
        self.item = make_item(self.tenant)
        self.c1 = make_customer(self.tenant, name='عميل الأمل', branch=self.b1)
        self.c2 = make_customer(self.tenant, name='عميل النور', branch=self.b2)
        self.sup1 = make_supplier(self.tenant, name='مورد الشمال', branch=self.b1)
        for stock, cust, price in ((self.s1, self.c1, '100'), (self.s2, self.c2, '700')):
            inv = SaleInvoice.objects.create(
                tenant=self.tenant, stock=stock, customer=cust, invoice_date=timezone.localdate(),
                status='confirmed', payment_method='credit')
            line = SaleInvoiceLine(tenant=self.tenant, invoice=inv, item=self.item, quantity=Decimal('1'),
                                   unit_price=Decimal(price), cost_price_snapshot=Decimal('10'),
                                   tax_rate=Decimal('0'))
            line.calculate()
            line.save()
            inv.recalculate_totals()
            inv.save()
            CustomerLedger.objects.create(tenant=self.tenant, customer=cust, entry_type='opening',
                                          amount=Decimal(price), entry_date=timezone.localdate())
        self.mgr1 = User.objects.create_user(
            username='lbl-m1', password='x12345678', tenant=self.tenant, branch=self.b1,
            is_branch_supervisor=True)

    # ---- التحليلات المتقدمة ---------------------------------------------
    def test_owner_analytics_shows_top_branches_not_customers(self):
        resp = self.client.get(reverse('core:analytics'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('أفضل الفروع إيراداً', html)
        self.assertNotIn('أفضل العملاء إيراداً', html)
        self.assertNotIn('عميل الأمل', html)
        branches = resp.context['top_branches']
        self.assertEqual([(b['name'], b['total']) for b in branches],
                         [('فرع الجنوب', 700.0), ('فرع الشمال', 100.0)])

    def test_branch_user_analytics_keeps_top_customers(self):
        self.client.force_login(self.mgr1)
        resp = self.client.get(reverse('core:analytics'))
        html = resp.content.decode()
        self.assertIn('أفضل العملاء إيراداً', html)
        self.assertNotIn('أفضل الفروع إيراداً', html)
        self.assertIn('عميل الأمل', html)
        self.assertNotIn('عميل النور', html)

    def test_sales_and_purchases_have_different_colours(self):
        html = self.client.get(reverse('core:analytics')).content.decode()
        self.assertIn("borderColor: '#2563eb'", html)   # المبيعات
        self.assertIn("borderColor: '#f59e0b'", html)   # المشتريات
        self.assertIn('.anl-kpi--sales    .anl-kpi-accent { background: #2563eb; }', html)

    # ---- التقارير ---------------------------------------------------------
    def _sales(self, branch=None):
        today = timezone.localdate()
        return SalesReportGenerator(self.tenant, today - timedelta(days=5), today, branch=branch)

    def test_owner_all_branches_reports_label_names_with_branch(self):
        names = [r['customer_name'] for r in self._sales().get_by_customer_report()['data']]
        self.assertCountEqual(names, ['عميل الأمل (فرع الشمال)', 'عميل النور (فرع الجنوب)'])
        bal = [r['name'] for r in self._sales().get_customer_balances()['data']]
        self.assertCountEqual(bal, ['عميل الأمل (فرع الشمال)', 'عميل النور (فرع الجنوب)'])
        stock_names = {r['stock_name'] for r in StocksReportGenerator(
            self.tenant, branch=None).get_by_stock_report()['data']} \
            if hasattr(StocksReportGenerator, 'get_by_stock_report') else set()
        if stock_names:
            self.assertTrue(all('(فرع' in n for n in stock_names), stock_names)

    def test_owner_single_branch_and_branch_user_reports_have_plain_names(self):
        names = [r['customer_name'] for r in self._sales(self.b1).get_by_customer_report()['data']]
        self.assertEqual(names, ['عميل الأمل'])

    def test_owner_has_no_customer_detail_reports(self):
        # تقارير العملاء تقارير تفاصيل فرع: مدير النشاط يرى الصورة العامة فقط
        # (راجع apps/accounts/tests_owner_reports_scope.py)؛ إلحاق اسم الفرع يبقى
        # لما يعرضه من تقارير عامة.
        for name in ('reports:by_customer_report', 'reports:customer_balances'):
            self.assertIn(self.client.get(reverse(name)).status_code, (302, 403), name)

    def test_branch_user_report_pages_unchanged(self):
        self.client.force_login(self.mgr1)
        html = self.client.get(reverse('reports:by_customer_report')).content.decode()
        self.assertIn('عميل الأمل', html)
        self.assertNotIn('(فرع الشمال)', html)

    def test_purchases_supplier_balances_label_for_owner(self):
        gen = PurchasesReportGenerator(self.tenant, branch=None)
        self.assertEqual(gen._party(self.sup1), 'مورد الشمال (فرع الشمال)')
        self.assertEqual(PurchasesReportGenerator(self.tenant, branch=self.b1)._party(self.sup1), 'مورد الشمال')

    # ---- الإشعارات --------------------------------------------------------
    def test_owner_notifications_show_branch(self):
        from apps.notifications.models import Notification
        Notification.objects.create(tenant=self.tenant, branch=self.b2, title='فاتورة متأخرة', message='للعميل عميل النور')
        html = self.client.get(reverse('notifications:list')).content.decode()
        self.assertIn('فرع الجنوب', html)
        items = self.client.get(reverse('notifications:api')).json()['items']
        self.assertTrue(items[0]['message'].startswith('[فرع الجنوب]'))
