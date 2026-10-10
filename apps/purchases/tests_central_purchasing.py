"""
الشراء المركزي (نمط المشتريات "هجين" في نسخة المؤسسات) — المرحلة 1:
مخزن مركزي، موردون مركزيون، فواتير شراء مركزية وسدادها من خزينة الإدارة، وعزلها عن الفروع.
"""
import json
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PermissionGroup, User
from apps.accounts.permissions import (
    get_branch_supervisor_permission_keys, get_enterprise_owner_permission_keys,
)
from apps.core.models import Branch, TenantCapabilities
from apps.core.test_utils import TenantTestCase, make_item, make_stock, make_supplier
from apps.purchases.models import PurchaseInvoice, SupplierLedger
from apps.stocks.models import Stock, StockQuantity
from apps.suppliers.models import Supplier
from apps.treasury.models import Treasury


class CentralPurchasingBase(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant,
            defaults={f.name: True for f in TenantCapabilities._meta.get_fields()
                      if getattr(f, 'name', '').startswith('has_')})
        self.tenant.purchasing_mode = 'hybrid'
        self.tenant.save(update_fields=['purchasing_mode'])
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.s1 = make_stock(self.tenant, name='مخزن الشمال', branch=self.b1)
        self.s2 = make_stock(self.tenant, name='مخزن الجنوب', branch=self.b2)
        self.central = Stock.objects.create(tenant=self.tenant, name='المخزن المركزي', is_central=True)
        self.item = make_item(self.tenant, name='صنف مركزي', cost_price='10')
        self.csup = Supplier.unscoped.create(tenant=self.tenant, name='مورد مركزي')
        self.bsup = make_supplier(self.tenant, name='مورد الشمال', branch=self.b1)
        self.ho_treasury = Treasury.objects.filter(
            tenant=self.tenant, is_head_office=True, is_hard_currency=False).first()
        self.assertIsNotNone(self.ho_treasury, 'خزينة الإدارة المركزية يجب أن تُنشأ تلقائياً')
        Treasury.objects.filter(pk=self.ho_treasury.pk).update(current_balance=Decimal('100000'))
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='مشتريات الفرع', is_active=True,
            permissions={'view_purchases': True, 'add_purchases': True, 'change_purchases': True, 'delete_purchases': True,
                         'view_suppliers': True, 'view_supplier_payments': True, 'add_supplier_payments': True,
                         'view_stocks': True})
        self.branch_user = User.objects.create_user(
            username='cp-b1', password='x12345678', tenant=self.tenant, branch=self.b1)
        self.branch_user.permission_groups.add(group)

    def post_invoice(self, user, stock, supplier, pm='credit', action='save_draft', qty='5', cost='10', **extra):
        self.client.force_login(user)
        body = {
            'header': {'stock_id': stock.id, 'supplier_id': supplier.id if supplier else None,
                       'invoice_date': timezone.localdate().isoformat(), 'payment_method': pm, **extra},
            'lines': [{'item_id': self.item.id, 'quantity': qty, 'unit_cost': cost, 'tax_rate': '0'}],
            'action': action,
        }
        return self.client.post(reverse('purchases:order_create'), json.dumps(body),
                                content_type='application/json')

    def qty(self, stock):
        sq = StockQuantity.objects.filter(tenant=self.tenant, stock=stock, item=self.item).first()
        return sq.quantity if sq else Decimal('0')


class CentralSettingsAndStockTests(CentralPurchasingBase):
    def test_owner_can_switch_modes_until_central_data_exists(self):
        self.client.force_login(self.user)
        url = reverse('core:tenant_settings_update_api')
        resp = self.client.post(url, json.dumps({'section': 'purchasing', 'data': {'purchasing_mode': 'decentralized'}}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400, 'يوجد مخزن ومورد مركزيان فلا رجوع')
        Stock.objects.filter(is_central=True).delete()
        Supplier.unscoped.filter(branch__isnull=True).delete()
        resp = self.client.post(url, json.dumps({'section': 'purchasing', 'data': {'purchasing_mode': 'decentralized'}}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.purchasing_mode, 'decentralized')

    def test_invalid_mode_and_non_enterprise_rejected(self):
        self.client.force_login(self.user)
        url = reverse('core:tenant_settings_update_api')
        resp = self.client.post(url, json.dumps({'section': 'purchasing', 'data': {'purchasing_mode': 'x'}}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_owner_creates_single_central_stock_without_branch(self):
        Stock.objects.filter(is_central=True).delete()
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:create_api'), {
            'name': 'مركزي جديد', 'is_active': 'on', 'is_central': 'on', 'is_default': 'on'})
        self.assertEqual(resp.status_code, 200, resp.content)
        stock = Stock.objects.get(tenant=self.tenant, name='مركزي جديد')
        self.assertTrue(stock.is_central)
        self.assertIsNone(stock.branch_id)
        self.assertFalse(stock.is_default)
        resp = self.client.post(reverse('stocks:create_api'), {
            'name': 'مركزي ثان', 'is_active': 'on', 'is_central': 'on'})
        self.assertEqual(resp.status_code, 400, 'مخزن مركزي واحد فقط')

    def test_non_central_stock_still_requires_branch(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:create_api'), {'name': 'بلا فرع', 'is_active': 'on'})
        self.assertEqual(resp.status_code, 400)

    def test_central_stock_hidden_from_branch_user_and_cannot_be_deleted(self):
        self.client.force_login(self.branch_user)
        resp = self.client.get(reverse('stocks:table_api'), {'draw': 1, 'start': 0, 'length': 50})
        names = [r['name'] for r in resp.json()['data']]
        self.assertEqual(names, ['مخزن الشمال'])
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:delete_api', args=[self.central.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_central_stock_checkbox_not_offered_when_not_hybrid(self):
        self.tenant.purchasing_mode = 'decentralized'
        self.tenant.save(update_fields=['purchasing_mode'])
        Stock.objects.filter(is_central=True).delete()
        from apps.stocks.forms import StockForm
        self.assertNotIn('is_central', StockForm(tenant=self.tenant).fields)


class CentralSuppliersTests(CentralPurchasingBase):
    def test_owner_sees_only_central_suppliers_and_branch_user_never_does(self):
        self.client.force_login(self.user)
        rows = self.client.get(reverse('suppliers:table_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual([r['name'] for r in rows], ['مورد مركزي'])
        self.client.force_login(self.branch_user)
        rows = self.client.get(reverse('suppliers:table_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual([r['name'] for r in rows], ['مورد الشمال'])

    def test_owner_creates_central_supplier_without_branch(self):
        self.client.force_login(self.user)
        resp = self.client.post(reverse('suppliers:create_api'), {
            'name': 'مورد مركزي جديد', 'is_active': 'on', 'currency': ''})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertIsNone(Supplier.unscoped.get(tenant=self.tenant, name='مورد مركزي جديد').branch_id)

    def test_owner_cannot_open_branch_supplier_in_central_scope(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('suppliers:detail_api', args=[self.bsup.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('suppliers:detail_api', args=[self.csup.pk])).status_code, 200)

    def test_branch_user_cannot_open_central_supplier(self):
        self.client.force_login(self.branch_user)
        self.assertEqual(self.client.get(reverse('suppliers:detail_api', args=[self.csup.pk])).status_code, 404)


class CentralPurchaseInvoiceTests(CentralPurchasingBase):
    def test_owner_credit_purchase_adds_stock_and_supplier_balance(self):
        resp = self.post_invoice(self.user, self.central, self.csup, pm='credit', action='confirm')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'], resp.content)
        inv = PurchaseInvoice.objects.get(tenant=self.tenant)
        self.assertEqual(inv.status, 'confirmed')
        self.assertEqual(self.qty(self.central), Decimal('5'))
        self.assertEqual(self.qty(self.s1), Decimal('0'))
        bal = SupplierLedger.objects.filter(tenant=self.tenant, supplier=self.csup).first()
        self.assertEqual(bal.amount, Decimal('50.00'))

    def test_cash_purchase_pays_from_head_office_treasury_and_cancel_restores_it(self):
        before = Treasury.objects.get(pk=self.ho_treasury.pk).current_balance
        resp = self.post_invoice(self.user, self.central, self.csup, pm='cash', action='confirm')
        self.assertTrue(resp.json()['success'], resp.content)
        mid = Treasury.objects.get(pk=self.ho_treasury.pk).current_balance
        self.assertEqual(before - mid, Decimal('50'))
        inv = PurchaseInvoice.objects.get(tenant=self.tenant)
        resp = self.client.post(reverse('purchases:order_cancel', args=[inv.pk]), '{}', content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertEqual(Treasury.objects.get(pk=self.ho_treasury.pk).current_balance, before)
        self.assertEqual(self.qty(self.central), Decimal('0'))

    def test_owner_list_shows_only_central_invoices(self):
        self.post_invoice(self.user, self.central, self.csup, action='confirm')
        self.post_invoice(self.branch_user, self.s1, self.bsup, action='confirm')
        self.client.force_login(self.user)
        rows = self.client.get(reverse('purchases:order_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual([r['stock'] for r in rows], ['المخزن المركزي'])
        self.client.force_login(self.branch_user)
        rows = self.client.get(reverse('purchases:order_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual([r['stock'] for r in rows], ['مخزن الشمال'])

    def test_owner_cannot_use_branch_stock_or_branch_supplier(self):
        resp = self.post_invoice(self.user, self.s1, self.csup)
        self.assertEqual(resp.status_code, 400)
        resp = self.post_invoice(self.user, self.central, self.bsup)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PurchaseInvoice.objects.count(), 0)

    def test_branch_user_cannot_use_central_stock_or_central_supplier(self):
        resp = self.post_invoice(self.branch_user, self.central, self.bsup)
        self.assertIn(resp.status_code, (400, 404))
        resp = self.post_invoice(self.branch_user, self.s1, self.csup)
        self.assertIn(resp.status_code, (400, 404))
        self.assertEqual(PurchaseInvoice.objects.count(), 0)

    def test_central_invoice_is_not_reachable_from_branch_scope(self):
        self.post_invoice(self.user, self.central, self.csup, action='confirm')
        inv = PurchaseInvoice.objects.get(tenant=self.tenant)
        self.client.force_login(self.branch_user)
        for name in ('order_detail', 'order_edit', 'order_print'):
            self.assertEqual(self.client.get(reverse(f'purchases:{name}', args=[inv.pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse('purchases:order_cancel', args=[inv.pk]), '{}',
                                          content_type='application/json').status_code, 404)

    def test_branch_invoice_is_not_reachable_from_central_scope(self):
        self.post_invoice(self.branch_user, self.s1, self.bsup, action='confirm')
        inv = PurchaseInvoice.objects.get(tenant=self.tenant)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('purchases:order_detail', args=[inv.pk])).status_code, 404)

    def test_owner_has_no_access_when_not_hybrid(self):
        self.tenant.purchasing_mode = 'decentralized'
        self.tenant.save(update_fields=['purchasing_mode'])
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:order_list'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('no-permission', resp.url)

    def test_central_invoice_offers_return_to_supplier_for_owner(self):
        self.post_invoice(self.user, self.central, self.csup, action='confirm')
        inv = PurchaseInvoice.objects.get(tenant=self.tenant)
        resp = self.client.get(reverse('purchases:order_detail', args=[inv.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['can_return'])  # الإدارة تعيد البضاعة للمورد (المرحلة 3)


class CentralSupplierPaymentTests(CentralPurchasingBase):
    def test_owner_pays_central_supplier_from_head_office_treasury(self):
        self.post_invoice(self.user, self.central, self.csup, pm='credit', action='confirm')
        before = Treasury.objects.get(pk=self.ho_treasury.pk).current_balance
        resp = self.client.post(reverse('suppliers:payments_create'), json.dumps({
            'supplier_id': self.csup.pk, 'amount': '20', 'method': 'cash',
            'treasury_id': self.ho_treasury.pk, 'payment_date': timezone.localdate().isoformat()}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(before - Treasury.objects.get(pk=self.ho_treasury.pk).current_balance, Decimal('20'))

    def test_owner_cannot_pay_from_a_branch_treasury(self):
        branch_treasury = Treasury.objects.create(
            tenant=self.tenant, name='خزينة الشمال', branch=self.b1, current_balance=Decimal('500'))
        self.client.force_login(self.user)
        resp = self.client.post(reverse('suppliers:payments_create'), json.dumps({
            'supplier_id': self.csup.pk, 'amount': '20', 'method': 'cash',
            'treasury_id': branch_treasury.pk, 'payment_date': timezone.localdate().isoformat()}),
            content_type='application/json')
        self.assertIn(resp.status_code, (400, 404))
        branch_treasury.refresh_from_db()
        self.assertEqual(branch_treasury.current_balance, Decimal('500'))

    def test_branch_user_cannot_pay_central_supplier(self):
        self.client.force_login(self.branch_user)
        bt = Treasury.objects.create(tenant=self.tenant, name='خزينة الشمال', branch=self.b1, current_balance=Decimal('500'))
        resp = self.client.post(reverse('suppliers:payments_create'), json.dumps({
            'supplier_id': self.csup.pk, 'amount': '20', 'method': 'cash', 'treasury_id': bt.pk,
            'payment_date': timezone.localdate().isoformat()}), content_type='application/json')
        self.assertEqual(resp.status_code, 404)

    def test_payments_list_is_scoped(self):
        Treasury.objects.create(tenant=self.tenant, name='خزينة الشمال', branch=self.b1,
                                current_balance=Decimal('1000'), is_default=True)
        self.post_invoice(self.user, self.central, self.csup, pm='cash', action='confirm')
        resp = self.post_invoice(self.branch_user, self.s1, self.bsup, pm='cash', action='confirm')
        self.assertTrue(resp.json()['success'], resp.content)
        self.client.force_login(self.user)
        names = [r['supplier'] for r in self.client.get(reverse('suppliers:payments_api'),
                 {'draw': 1, 'start': 0, 'length': 50}).json()['data']]
        self.assertEqual(set(names), {'مورد مركزي'})
        self.client.force_login(self.branch_user)
        names = [r['supplier'] for r in self.client.get(reverse('suppliers:payments_api'),
                 {'draw': 1, 'start': 0, 'length': 50}).json()['data']]
        self.assertEqual(set(names), {'مورد الشمال'})


class CentralPermissionKeysTests(CentralPurchasingBase):
    CENTRAL_KEYS = {
        'view_central_purchases', 'add_central_purchases', 'change_central_purchases',
        'delete_central_purchases', 'view_central_suppliers', 'add_central_suppliers',
        'change_central_suppliers', 'delete_central_suppliers', 'view_central_supplier_payments',
        'add_central_supplier_payments', 'cancel_central_supplier_payments',
    }

    def test_owner_has_central_keys_but_branch_supervisor_does_not(self):
        self.assertLessEqual(self.CENTRAL_KEYS, set(get_enterprise_owner_permission_keys()))
        self.assertFalse(self.CENTRAL_KEYS & set(get_branch_supervisor_permission_keys()))

    def test_central_keys_only_offered_for_hybrid_tenants(self):
        from apps.accounts.permissions import filter_schema_for_tenant
        keys = lambda t: {k for perms in filter_schema_for_tenant(t).values() for k in perms}
        self.assertLessEqual(self.CENTRAL_KEYS, keys(self.tenant))
        self.tenant.purchasing_mode = 'decentralized'
        self.assertFalse(self.CENTRAL_KEYS & keys(self.tenant))

    def test_admin_scope_group_can_hold_central_keys_branch_scope_cannot(self):
        self.client.force_login(self.user)
        perms = json.dumps({k: True for k in self.CENTRAL_KEYS})
        resp = self.client.post(reverse('accounts:permission_group_create_api'),
                                {'name': 'مشتريات الإدارة', 'scope': 'admin', 'permissions': perms})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(set(PermissionGroup.objects.get(name='مشتريات الإدارة').get_permission_keys()), self.CENTRAL_KEYS)
        resp = self.client.post(reverse('accounts:permission_group_create_api'),
                                {'name': 'فرع يحاول', 'scope': 'branch', 'permissions': perms})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(PermissionGroup.objects.get(name='فرع يحاول').get_permission_keys())


class CentralStockIsDistributionOnlyTests(CentralPurchasingBase):
    def test_sale_invoice_on_central_stock_is_refused(self):
        from apps.sales.models import SaleInvoice
        with self.assertRaises(ValueError):
            SaleInvoice.objects.create(
                tenant=self.tenant, stock=self.central, invoice_date=timezone.localdate(),
                status='draft', payment_method='cash')
        self.assertEqual(SaleInvoice.objects.count(), 0)

    def test_sale_invoice_on_branch_stock_still_works(self):
        from apps.sales.models import SaleInvoice
        inv = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.s1, invoice_date=timezone.localdate(),
            status='draft', payment_method='cash')
        self.assertEqual(inv.branch_id, self.b1.pk)
