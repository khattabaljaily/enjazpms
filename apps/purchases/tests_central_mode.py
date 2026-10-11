"""
النمط المركزي (purchasing_mode == 'central') — المرحلة 1:
الإدارة وحدها تشتري وتدير الموردين وحساباتهم وتقاريرهم، بنفس الشاشات والأسماء والصلاحيات المعتادة،
ولا يظهر شيء منها في لوحة الفرع. وتأكيد أن الباقتين الأساسية والاحترافية والنمطين الهجين
واللامركزي لم يتأثرا.
"""
import json

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PermissionGroup, User
from apps.accounts.permissions import (
    filter_schema_for_tenant, get_branch_supervisor_permission_keys,
    get_central_mode_admin_only_keys, get_enterprise_owner_permission_keys,
)
from apps.core.models import Branch, TenantCapabilities
from apps.core.test_utils import TenantTestCase, make_item, make_stock, make_supplier
from apps.purchases.models import PurchaseInvoice, SupplierLedger
from apps.stocks.models import Stock
from apps.suppliers.models import Supplier

# صلاحيات المشتريات والموردين العادية التي تصبح للإدارة وحدها في النمط المركزي.
PURCHASING_KEYS = [
    'view_purchases', 'add_purchases', 'change_purchases', 'delete_purchases',
    'view_purchase_returns', 'add_purchase_returns',
    'view_suppliers', 'add_suppliers', 'change_suppliers', 'delete_suppliers',
    'view_supplier_payments', 'add_supplier_payments', 'cancel_supplier_payments',
    'view_supplier_transactions', 'import_suppliers', 'export_suppliers',
    'view_purchases_summary_report', 'view_purchases_by_supplier_report', 'view_purchases_by_item_report',
    'view_purchases_by_date_report', 'view_purchases_price_history_report', 'view_purchases_by_user_report',
    'view_purchases_supplier_statement_report', 'view_purchases_supplier_balances_report',
    'view_purchases_payments_report', 'view_purchases_returns_report',
]


class CentralModeBase(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'
    mode = 'central'

    def setUp(self):
        super().setUp()
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant,
            defaults={f.name: True for f in TenantCapabilities._meta.get_fields()
                      if getattr(f, 'name', '').startswith('has_')})
        self.tenant.purchasing_mode = self.mode
        self.tenant.save(update_fields=['purchasing_mode'])
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.s1 = make_stock(self.tenant, name='مخزن الشمال', branch=self.b1)
        self.item = make_item(self.tenant, name='صنف مركزي', cost_price='10')
        self.csup = Supplier.unscoped.create(tenant=self.tenant, name='مورد الإدارة')
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='مجموعة فرع', is_active=True,
            permissions={k: True for k in PURCHASING_KEYS + ['view_stocks']})
        self.branch_user = User.objects.create_user(
            username='cm-b1', password='x12345678', tenant=self.tenant, branch=self.b1)
        self.branch_user.permission_groups.add(group)
        self.supervisor = User.objects.create_user(
            username='cm-sup', password='x12345678', tenant=self.tenant, branch=self.b1,
            is_branch_supervisor=True)

    @property
    def central_stock(self):
        return Stock.objects.get(tenant=self.tenant, is_central=True)

    def table(self, name):
        return self.client.get(reverse(name), {'draw': 1, 'start': 0, 'length': 50})


class CentralModeModelTests(CentralModeBase):
    def test_flags(self):
        self.assertTrue(self.tenant.is_central_purchasing())
        self.assertTrue(self.tenant.has_central_warehouse())
        self.assertFalse(self.tenant.is_hybrid_purchasing())

    def test_central_stock_created_automatically_once_and_not_default(self):
        stocks = Stock.objects.filter(tenant=self.tenant, is_central=True)
        self.assertEqual(stocks.count(), 1)
        stock = stocks.get()
        self.assertIsNone(stock.branch_id)
        self.assertFalse(stock.is_default)
        self.tenant.save()  # حفظ لاحق لا ينشئ مخزناً ثانياً
        self.assertEqual(Stock.objects.filter(tenant=self.tenant, is_central=True).count(), 1)

    def test_central_mode_requires_enterprise(self):
        self.tenant.version_type = 'multi_stock'
        self.assertFalse(self.tenant.is_central_purchasing())


class CentralModePermissionTests(CentralModeBase):
    def test_owner_has_standard_purchasing_keys(self):
        for key in PURCHASING_KEYS:
            self.assertTrue(self.user.has_perm_key(key), key)
            self.assertIn(key, self.user.get_permission_keys())

    def test_branch_user_and_supervisor_never_get_purchasing_keys(self):
        for key in PURCHASING_KEYS:
            self.assertFalse(self.branch_user.has_perm_key(key), key)
            self.assertFalse(self.supervisor.has_perm_key(key), key)
            self.assertNotIn(key, self.branch_user.get_permission_keys())
            self.assertNotIn(key, self.supervisor.get_permission_keys())

    def test_admin_only_key_set_matches_expected_keys(self):
        self.assertEqual(get_central_mode_admin_only_keys(), set(PURCHASING_KEYS))

    def test_scope_key_sets_flip_only_in_central_mode(self):
        self.assertNotIn('view_purchases', get_enterprise_owner_permission_keys())
        self.assertIn('view_purchases', get_enterprise_owner_permission_keys(True))
        self.assertIn('view_purchases', get_branch_supervisor_permission_keys())
        self.assertNotIn('view_purchases', get_branch_supervisor_permission_keys(True))
        # باقي عمليات الفرع تبقى كما هي لمدير النشاط.
        self.assertNotIn('view_sales', get_enterprise_owner_permission_keys(True))
        self.assertIn('view_sales', get_branch_supervisor_permission_keys(True))

    def test_permission_tree_for_central_tenant_uses_standard_names_without_central_word(self):
        schema = filter_schema_for_tenant(self.tenant)
        self.assertIn('المشتريات', schema)
        self.assertIn('الموردين', schema)
        self.assertIn('view_purchases', schema['المشتريات'])
        all_keys = {k for perms in schema.values() for k in perms}
        for key in ('view_central_purchases', 'view_central_suppliers', 'view_central_supplier_payments',
                    'view_central_purchase_returns', 'view_central_purchases_report'):
            self.assertNotIn(key, all_keys, key)
        # مفاتيح الشحنات (توزيع الفروع) تبقى متاحة بقسم باسم لا يذكر المشتريات المركزية.
        self.assertIn('view_central_shipments', all_keys)
        self.assertNotIn('المشتريات والمخزن المركزي', schema)

    def test_group_screen_excludes_purchasing_from_branch_groups_in_central_mode(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('accounts:permission_group_list'))
        self.assertEqual(resp.status_code, 200)
        excluded = json.loads(resp.context['group_scope_excluded'])
        self.assertIn('المشتريات', excluded['branch'])
        self.assertIn('الموردين', excluded['branch'])
        self.assertNotIn('المشتريات', excluded['admin'])
        self.assertNotIn('الموردين', excluded['admin'])
        excluded_keys = json.loads(resp.context['group_scope_excluded_keys'])
        self.assertIn('view_purchases_supplier_statement_report', excluded_keys['branch'])
        self.assertNotIn('view_purchases_supplier_statement_report', excluded_keys['admin'])

    def test_owner_group_includes_purchasing_keys(self):
        group = PermissionGroup.create_owner_group(self.tenant, name='مدير 2')
        for key in PURCHASING_KEYS:
            self.assertTrue(group.permissions.get(key), key)


class CentralModeScreensTests(CentralModeBase):
    def test_owner_opens_standard_screens_in_central_scope(self):
        self.client.force_login(self.user)
        for name in ('purchases:order_list', 'purchases:order_create', 'purchases:return_list',
                     'purchases:rfq_list', 'suppliers:list', 'suppliers:payments',
                     'purchases:reports:summary_report', 'purchases:reports:supplier_statement',
                     'purchases:reports:supplier_balances', 'purchases:reports:payments_report',
                     'purchases:reports:returns_report'):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)
            self.assertTrue(resp.wsgi_request.central_scope, name)

    def test_branch_user_and_supervisor_denied_everywhere(self):
        for user in (self.branch_user, self.supervisor):
            self.client.force_login(user)
            for name in ('purchases:order_list', 'purchases:order_create', 'purchases:rfq_list',
                         'suppliers:list', 'suppliers:payments', 'purchases:reports:summary_report',
                         'purchases:reports:supplier_statement', 'purchases:reports:by_item_report'):
                resp = self.client.get(reverse(name))
                self.assertEqual(resp.status_code, 302, (user.username, name))
                self.assertEqual(resp.url, reverse('core:no_permission'), (user.username, name))
            resp = self.client.get(reverse('suppliers:table_api'), {'draw': 1, 'start': 0, 'length': 10},
                                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            self.assertEqual(resp.status_code, 403)

    def test_order_form_has_fixed_central_stock_and_no_stock_choice(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:order_create'))
        self.assertContains(resp, f'<input type="hidden" id="stockId" value="{self.central_stock.id}">', html=False)
        self.assertNotContains(resp, 'مخزن الشمال')

    def test_owner_creates_credit_invoice_on_central_stock_and_supplier_balance(self):
        self.client.force_login(self.user)
        body = {
            'header': {'stock_id': self.central_stock.id, 'supplier_id': self.csup.id,
                       'invoice_date': timezone.localdate().isoformat(), 'payment_method': 'credit'},
            'lines': [{'item_id': self.item.id, 'quantity': '5', 'unit_cost': '10', 'tax_rate': '0'}],
            'action': 'confirm',
        }
        resp = self.client.post(reverse('purchases:order_create'), json.dumps(body),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        invoice = PurchaseInvoice.objects.get(tenant=self.tenant)
        self.assertTrue(invoice.stock.is_central)
        self.assertEqual(invoice.status, 'confirmed')
        ledger = SupplierLedger.objects.filter(tenant=self.tenant, supplier=self.csup).first()
        self.assertEqual(ledger.amount, 50)

    def test_owner_cannot_buy_into_branch_stock(self):
        self.client.force_login(self.user)
        body = {
            'header': {'stock_id': self.s1.id, 'supplier_id': self.csup.id,
                       'invoice_date': timezone.localdate().isoformat(), 'payment_method': 'credit'},
            'lines': [{'item_id': self.item.id, 'quantity': '1', 'unit_cost': '10', 'tax_rate': '0'}],
            'action': 'save_draft',
        }
        resp = self.client.post(reverse('purchases:order_create'), json.dumps(body),
                                content_type='application/json')
        self.assertGreaterEqual(resp.status_code, 400)
        self.assertFalse(PurchaseInvoice.objects.filter(tenant=self.tenant).exists())

    def test_rfq_works_on_central_stock_for_owner_only(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:rfq_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([s.pk for s in resp.context['stocks']], [self.central_stock.pk])
        body = {'supplier_id': self.csup.id, 'stock_id': self.central_stock.id,
                'lines': [{'item_id': self.item.id, 'quantity': '3'}]}
        resp = self.client.post(reverse('purchases:rfq_create'), json.dumps(body),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_report_branch_filter_is_hidden_in_central_mode(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:reports:supplier_balances'))
        self.assertFalse(resp.context['is_central_admin'])

    def test_hybrid_only_report_is_not_available_in_central_mode(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:reports:central_purchases_report'))
        self.assertEqual(resp.status_code, 404)

    def test_shipments_available_for_head_office_in_central_mode(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('stocks:shipment_list'))
        self.assertEqual(resp.status_code, 200)


class CentralModeSidebarTests(CentralModeBase):
    def sidebar(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('core:dashboard')).content.decode()

    def test_owner_sidebar_uses_standard_names_and_no_central_wording(self):
        html = self.sidebar(self.user)
        for text in ('المشتريات', 'أمر شراء جديد', 'قائمة أوامر الشراء', 'الموردين', 'مدفوعات الموردين',
                     'تقارير المشتريات', 'كشف حساب مورد', 'مديونيات الموردين'):
            self.assertIn(text, html, text)
        for text in ('المشتريات المركزية', 'الموردون المركزيون', 'فواتير الشراء المركزية',
                     'مدفوعات الموردين المركزيين', 'مرتجعات المشتريات المركزية'):
            self.assertNotIn(text, html, text)
        self.assertIn('شحنات الفروع ومرتجعاتها', html)

    def test_owner_standard_blocks_keep_the_standard_order(self):
        html = self.sidebar(self.user)
        order = [html.index(t) for t in ('>\n                المشتريات', '>\n                الموردين')]
        self.assertEqual(order, sorted(order))

    def test_branch_sidebar_has_no_purchasing_or_supplier_entries(self):
        html = self.sidebar(self.supervisor)
        for url in (reverse('suppliers:list'), reverse('purchases:order_list'),
                    reverse('purchases:order_create'), reverse('suppliers:payments'),
                    reverse('purchases:reports:summary_report'), reverse('purchases:reports:supplier_statement')):
            self.assertNotIn(f'href="{url}"', html, url)


class OtherModesUnaffectedTests(TenantTestCase):
    """الباقتان الأساسية والاحترافية: لا أثر إطلاقاً للنمط المركزي."""

    def test_basic_and_pro_style_tenant_is_unaffected(self):
        self.assertFalse(self.tenant.is_central_purchasing())
        self.assertFalse(self.tenant.has_central_warehouse())
        self.assertTrue(self.user.has_perm_key('view_purchases'))
        self.assertTrue(self.user.has_perm_key('view_suppliers'))
        for name in ('purchases:order_list', 'suppliers:list', 'purchases:rfq_list',
                     'purchases:reports:summary_report'):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)
            self.assertFalse(getattr(resp.wsgi_request, 'central_scope', False), name)

    def test_non_enterprise_tenant_never_becomes_central_even_with_the_field_set(self):
        self.tenant.purchasing_mode = 'central'
        self.tenant.save(update_fields=['purchasing_mode'])
        self.assertFalse(self.tenant.is_central_purchasing())
        self.assertFalse(Stock.objects.filter(tenant=self.tenant, is_central=True).exists())
        self.assertEqual(self.client.get(reverse('suppliers:list')).status_code, 200)


class ProTenantUnaffectedTests(OtherModesUnaffectedTests):
    version_type = 'multi_stock'
    subscription_plan = 'pro'


class HybridModeUnchangedTests(CentralModeBase):
    mode = 'hybrid'

    def test_hybrid_owner_still_has_no_standard_purchasing_keys_and_uses_central_keys(self):
        self.assertFalse(self.user.has_perm_key('view_purchases'))
        self.assertTrue(self.user.has_perm_key('view_central_purchases'))
        self.assertTrue(self.user.has_perm_key('view_central_suppliers'))

    def test_hybrid_branch_supervisor_keeps_purchasing_keys(self):
        for key in ('view_purchases', 'view_suppliers', 'add_supplier_payments'):
            self.assertTrue(self.supervisor.has_perm_key(key), key)

    def test_hybrid_central_screens_and_labels_unchanged(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:order_list'))
        self.assertEqual(resp.status_code, 200)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('المشتريات المركزية', html)
        self.assertIn('الموردون المركزيون', html)

    def test_hybrid_permission_tree_keeps_central_section(self):
        schema = filter_schema_for_tenant(self.tenant)
        self.assertIn('المشتريات والمخزن المركزي', schema)
        self.assertIn('view_central_purchases', schema['المشتريات والمخزن المركزي'])
        self.assertIn('view_central_shipments', schema['المشتريات والمخزن المركزي'])

    def test_hybrid_branch_user_still_buys_locally(self):
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='مشتريات الفرع', is_active=True,
            permissions={'view_purchases': True, 'add_purchases': True, 'view_suppliers': True})
        user = User.objects.create_user(username='hy-b1', password='x12345678', tenant=self.tenant, branch=self.b1)
        user.permission_groups.add(group)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('purchases:order_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('suppliers:list')).status_code, 200)


class CentralModeEndToEndReportsTests(CentralModeBase):
    """دورة كاملة بالنمط المركزي (مورد ← شراء ← دفعة ← مرتجع) ثم كل التقارير بأرقامها."""

    def setUp(self):
        super().setUp()
        from decimal import Decimal
        from apps.treasury.models import Treasury
        self.ho = Treasury.objects.filter(tenant=self.tenant, is_head_office=True, is_hard_currency=False).first()
        Treasury.objects.filter(pk=self.ho.pk).update(current_balance=Decimal('100000'))
        self.client.force_login(self.user)
        resp = self.client.post(reverse('suppliers:create_api'), {
            'name': 'مورد التقارير', 'is_active': 'on', 'currency': ''})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.sup = Supplier.unscoped.get(tenant=self.tenant, name='مورد التقارير')
        body = {
            'header': {'stock_id': self.central_stock.id, 'supplier_id': self.sup.id,
                       'invoice_date': timezone.localdate().isoformat(), 'payment_method': 'credit'},
            'lines': [{'item_id': self.item.id, 'quantity': '10', 'unit_cost': '10', 'tax_rate': '0'}],
            'action': 'confirm',
        }
        resp = self.client.post(reverse('purchases:order_create'), json.dumps(body),
                                content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.content)
        self.invoice = PurchaseInvoice.objects.get(tenant=self.tenant)
        resp = self.client.post(reverse('suppliers:payments_create'), json.dumps({
            'supplier_id': self.sup.pk, 'amount': '30', 'method': 'cash', 'treasury_id': self.ho.pk,
            'payment_date': timezone.localdate().isoformat()}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_supplier_is_central_and_hidden_from_branch(self):
        self.assertIsNone(self.sup.branch_id)
        self.client.force_login(self.branch_user)
        resp = self.client.get(reverse('suppliers:table_api'), {'draw': 1, 'start': 0, 'length': 50},
                               HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 403)

    def test_every_report_and_export_renders_for_owner(self):
        names = ['summary_report', 'by_supplier_report', 'by_item_report', 'by_date_report',
                 'supplier_statement', 'supplier_balances', 'payments_report', 'returns_report',
                 'by_user_report', 'price_history_report']
        for name in names:
            resp = self.client.get(reverse(f'purchases:reports:{name}'))
            self.assertEqual(resp.status_code, 200, name)
            self.assertNotContains(resp, 'المشتريات المركزية', msg_prefix=name)
            resp = self.client.get(reverse(f'purchases:reports:{name}_export'))
            self.assertEqual(resp.status_code, 200, f'{name}_export')

    def test_supplier_balances_report_shows_the_remaining_balance(self):
        resp = self.client.get(reverse('purchases:reports:supplier_balances'))
        self.assertContains(resp, 'مورد التقارير')
        self.assertContains(resp, '70')  # 100 مشتريات − 30 دفعة

    def test_summary_and_payments_reports_include_the_data(self):
        resp = self.client.get(reverse('purchases:reports:summary_report'))
        self.assertContains(resp, self.invoice.invoice_number)
        resp = self.client.get(reverse('purchases:reports:payments_report'))
        self.assertContains(resp, 'مورد التقارير')

    def test_supplier_statement_for_central_supplier(self):
        resp = self.client.get(reverse('purchases:reports:supplier_statement'),
                               {'supplier_id': self.sup.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'مورد التقارير')

    def test_purchase_return_flow_and_report(self):
        from apps.purchases.models import PurchaseInvoiceLine
        line = PurchaseInvoiceLine.objects.get(invoice=self.invoice)
        body = {'header': {'refund_method': 'balance', 'return_date': timezone.localdate().isoformat()},
                'lines': [{'invoice_line_id': line.pk, 'returned_quantity': '2'}], 'action': 'confirm'}
        resp = self.client.post(reverse('purchases:return_create', args=[self.invoice.pk]),
                                json.dumps(body), content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertEqual(self.client.get(reverse('purchases:return_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('purchases:reports:returns_report')).status_code, 200)

    def test_owner_dashboard_renders_with_suppliers(self):
        resp = self.client.get(reverse('core:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['stats']['total_suppliers'], 2)


class CentralModeAnalyticsTests(CentralModeBase):
    def test_branch_analytics_uses_cogs_and_hides_supplier_debt(self):
        self.client.force_login(self.supervisor)
        resp = self.client.get(reverse('core:analytics'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['cost_is_cogs'])
        self.assertContains(resp, 'تكلفة البضاعة المباعة')
        self.assertNotContains(resp, 'مستحقات للموردين')

    def test_head_office_analytics_keeps_purchases_and_supplier_debt(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('core:analytics'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['cost_is_cogs'])
        self.assertContains(resp, 'إجمالي المشتريات')
        self.assertContains(resp, 'مستحقات للموردين')

    def test_head_office_filtering_a_branch_uses_cogs(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('core:analytics'), {'branch': self.b1.pk})
        self.assertTrue(resp.context['cost_is_cogs'])


class HybridAnalyticsUnchangedTests(CentralModeBase):
    mode = 'hybrid'

    def test_hybrid_analytics_still_uses_purchases(self):
        self.client.force_login(self.supervisor)
        resp = self.client.get(reverse('core:analytics'))
        self.assertFalse(resp.context['cost_is_cogs'])
        self.assertContains(resp, 'إجمالي المشتريات')


class SwitchingToCentralModeTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع')
        self.branch_stock = make_stock(self.tenant, name='مخزن الفرع', branch=self.branch)

    def switch(self):
        self.tenant.purchasing_mode = 'central'
        self.tenant.save()

    def test_legacy_tenant_without_branch_purchasing_can_switch_once(self):
        self.assertIsNone(self.tenant.central_mode_blocker())
        self.switch()
        self.assertTrue(Stock.objects.filter(tenant=self.tenant, is_central=True).exists())

    def test_branch_invoices_block_the_switch(self):
        PurchaseInvoice.objects.create(
            tenant=self.tenant, stock=self.branch_stock, invoice_date=timezone.localdate(), status='draft')
        self.assertIn('فواتير شراء', self.tenant.central_mode_blocker())
        with self.assertRaises(ValueError):
            self.switch()
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.purchasing_mode, 'decentralized')

    def test_branch_suppliers_block_the_switch(self):
        make_supplier(self.tenant, name='مورد فرع', branch=self.branch)
        self.assertIn('موردون', self.tenant.central_mode_blocker())
        with self.assertRaises(ValueError):
            self.switch()

    def test_central_invoices_and_suppliers_do_not_block(self):
        central = Stock.objects.create(tenant=self.tenant, name='مركزي', is_central=True)
        PurchaseInvoice.objects.create(
            tenant=self.tenant, stock=central, invoice_date=timezone.localdate(), status='draft')
        Supplier.unscoped.create(tenant=self.tenant, name='مورد إدارة')
        self.assertIsNone(self.tenant.central_mode_blocker())

    def test_locked_mode_never_changes(self):
        self.tenant.purchasing_mode = 'hybrid'
        self.tenant.purchasing_mode_locked = True
        self.tenant.save()
        self.tenant.purchasing_mode = 'central'
        with self.assertRaises(ValueError):
            self.tenant.save()

    def test_platform_form_requires_a_real_mode_for_new_enterprise_tenant(self):
        from apps.core.forms import TenantForm
        from apps.core.models import BusinessType
        bt = BusinessType.objects.first()
        data = {
            'name': 'جديد', 'business_type': bt.pk, 'email': 'n@example.com', 'phone': '1', 'city': 'x',
            'country': 'x', 'address': 'x', 'subscription_plan': 'enterprise', 'currency': 'SDG',
            'purchasing_mode': 'decentralized', 'timezone': 'Africa/Khartoum', 'language': 'ar',
        }
        form = TenantForm(data)
        form.is_valid()
        self.assertIn('purchasing_mode', form.errors)


class PurchaseReturnsReportMenuTests(CentralModeBase):
    """تقرير مرتجعات المشتريات ضمن «تقارير المشتريات» لا «تقارير الحسابات»."""
    mode = 'central'

    def test_returns_report_lives_in_purchases_reports_menu(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        url = reverse('purchases:reports:returns_report')
        self.assertEqual(html.count(f'href="{url}"'), 1)
        pos = html.index(f'href="{url}"')
        purchases_menu = html.index('تقارير المشتريات')
        accounts_menu = html.index('تقارير الحسابات')
        self.assertTrue(purchases_menu < pos < accounts_menu or accounts_menu < purchases_menu < pos)
        self.assertLess(purchases_menu, pos)
        self.assertTrue(pos < accounts_menu or purchases_menu > accounts_menu)

    def test_permission_key_moved_to_purchases_reports_section(self):
        from apps.accounts.permissions import filter_schema_for_tenant
        schema = filter_schema_for_tenant(self.tenant)
        self.assertIn('view_purchases_returns_report', schema['تقارير المشتريات'])
        self.assertNotIn('view_purchases_returns_report', schema['تقارير الحسابات'])


class PurchaseReturnsReportMenuOtherPlansTests(TenantTestCase):
    def test_single_store_tenant_sees_it_under_purchases_reports(self):
        html = self.client.get(reverse('core:dashboard')).content.decode()
        url = reverse('purchases:reports:returns_report')
        self.assertEqual(html.count(f'href="{url}"'), 1)
        self.assertLess(html.index('تقارير المشتريات'), html.index(f'href="{url}"'))


class MainStockLockedTests(CentralModeBase):
    """المخزن الرئيسي في النمط المركزي لا يُعدَّل ولا يُحذف ولا يُعيَّن افتراضياً."""

    def test_cannot_edit_delete_or_set_default(self):
        stock = self.central_stock
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:update_api', args=[stock.pk]), {'name': 'اسم جديد', 'is_active': 'on'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('لا يمكن تعديله', resp.json()['message'])
        resp = self.client.post(reverse('stocks:delete_api', args=[stock.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertIn('لا يمكن تعديله أو حذفه', resp.json()['message'])
        resp = self.client.post(reverse('stocks:set_default_api', args=[stock.pk]))
        self.assertEqual(resp.status_code, 400)
        stock.refresh_from_db()
        self.assertEqual((stock.name, stock.is_active, stock.is_default), ('المخزن الرئيسي', True, False))

    def test_branch_stocks_remain_editable(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:update_api', args=[self.s1.pk]),
                                {'name': 'مخزن الشمال 2', 'is_active': 'on', 'branch': self.b1.pk})
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_ui_hides_edit_and_delete_for_it(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('stocks:list')).content.decode()
        self.assertIn('const CENTRAL_MODE_LOCK = true;', page)


class MainStockHybridUnchangedTests(CentralModeBase):
    mode = 'hybrid'

    def test_hybrid_central_stock_still_editable_and_flag_off(self):
        stock = Stock.objects.create(tenant=self.tenant, name='مركزي هجين', is_central=True)
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:update_api', args=[stock.pk]),
                                {'name': 'مركزي معدّل', 'is_active': 'on'})
        self.assertNotIn('نظامي', resp.json()['message'])  # قفل المخزن الرئيسي للنمط المركزي فقط
        page = self.client.get(reverse('stocks:list')).content.decode()
        self.assertIn('const CENTRAL_MODE_LOCK = false;', page)
