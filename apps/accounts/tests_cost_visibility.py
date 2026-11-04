"""صلاحية «view_cost_prices»: التكلفة والأرباح تُخفى عن كل مستخدم لا يملكها (عدا مدير النشاط)."""
import json
from decimal import Decimal

from django.urls import reverse

from apps.accounts.cost_visibility import can_view_cost, can_view_purchase_prices
from apps.accounts.models import PermissionGroup, User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_item
from apps.items.models import Category

EMPLOYEE_KEYS = ['view_items', 'change_items', 'export_items', 'view_stocks_valuation_report',
                 'view_sales_profit_margin_report', 'view_stocks_summary_report']


class CostVisibilityTests(TenantTestCase):
    subscription_plan = 'pro'

    def setUp(self):
        super().setUp()
        self.cat = Category.objects.create(tenant=self.tenant, name='عام', is_active=True)
        self.item = make_item(self.tenant, name='صنف سري', cost_price='73', selling_price='100', category=self.cat)
        self.group = PermissionGroup.objects.create(
            tenant=self.tenant, name='موظفين', permissions={k: True for k in EMPLOYEE_KEYS})
        self.emp = User.objects.create_user(username='emp-cost', password='x12345678', tenant=self.tenant)
        self.group.users.add(self.emp)

    def _as(self, user):
        self.client.force_login(user)

    def grant_cost(self):
        self.group.permissions['view_cost_prices'] = True
        self.group.save()

    def test_admin_always_sees_cost(self):
        self.assertTrue(can_view_cost(self.user))
        resp = self.client.get(reverse('items:table_api'))
        self.assertEqual(resp.json()['data'][0]['cost_price'], '73.00')

    def test_employee_without_permission(self):
        self._as(self.emp)
        self.assertFalse(can_view_cost(self.emp))
        row = self.client.get(reverse('items:table_api')).json()['data'][0]
        self.assertNotIn('cost_price', row)
        data = self.client.get(reverse('items:detail_api', args=[self.item.pk])).json()['data']
        self.assertNotIn('cost_price', data)
        self.assertNotIn('cost_price_hc', data)
        page = self.client.get(reverse('items:list'))
        self.assertNotContains(page, 'id="id_cost_price"')
        self.assertNotIn('73', self.client.get(reverse('items:export_api')).content.decode('utf-8-sig'))
        self.assertNotIn('سعر التكلفة', self.client.get(reverse('items:export_api')).content.decode('utf-8-sig'))

    def test_employee_cost_reports_blocked(self):
        self._as(self.emp)
        for name in ('stocks:reports:valuation_report', 'reports:profit_margin_report'):
            resp = self.client.get(reverse(name))
            self.assertRedirects(resp, reverse('core:no_permission'), fetch_redirect_response=False)
        resp = self.client.get(reverse('stocks:reports:summary_report'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'القيمة الإجمالية')

    def test_employee_edit_does_not_wipe_cost(self):
        self._as(self.emp)
        resp = self.client.post(reverse('items:update_api', args=[self.item.pk]), {
            'name': 'صنف سري', 'item_type': 'product', 'category': self.cat.pk,
            'selling_price': '120', 'cost_price': '1', 'is_active': 'on', 'is_sellable': 'on'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.item.refresh_from_db()
        self.assertEqual(self.item.selling_price, Decimal('120'))
        self.assertEqual(self.item.cost_price, Decimal('73'))   # لم تُقرأ من الطلب ولم تُصفَّر

    def test_employee_cannot_adjust_cost_prices(self):
        self._as(self.emp)
        resp = self.client.post(reverse('items:price_adjust_preview_api'), {'percent': '10', 'fields': ['cost']})
        self.assertEqual(resp.status_code, 400)

    def test_granted_group_sees_cost(self):
        self.grant_cost()
        self._as(self.emp)
        self.assertTrue(can_view_cost(self.emp))
        row = self.client.get(reverse('items:table_api')).json()['data'][0]
        self.assertEqual(row['cost_price'], '73.00')
        self.assertEqual(self.client.get(reverse('stocks:reports:valuation_report')).status_code, 200)

    def test_purchase_user_sees_purchase_prices_only(self):
        self.group.permissions.update({'add_purchases': True, 'view_purchases': True})
        self.group.save()
        self._as(self.emp)
        self.assertFalse(can_view_cost(self.emp))
        self.assertTrue(can_view_purchase_prices(self.emp))
        res = self.client.get(reverse('items:search_api'), {'q': 'صنف'}).json()['results']
        self.assertEqual(res[0]['cost_price'], '73.00')       # لتعبئة سعر الشراء في الفاتورة
        self.assertNotIn('cost_price', self.client.get(reverse('items:table_api')).json()['data'][0])

    def test_plain_user_search_has_no_cost(self):
        self._as(self.emp)
        res = self.client.get(reverse('items:search_api'), {'q': 'صنف'}).json()['results']
        self.assertNotIn('cost_price', res[0])


class SupervisorCostTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع')
        self.sup = User.objects.create_user(username='sup-cost', password='x12345678', tenant=self.tenant,
                                            branch=self.branch, is_branch_supervisor=True)

    def test_supervisor_not_granted_automatically(self):
        self.assertFalse(self.sup.has_perm_key('view_cost_prices'))
        self.assertNotIn('view_cost_prices', self.sup.get_permission_keys())
        self.assertTrue(self.sup.has_perm_key('view_items'))   # بقية صلاحياته كما هي

    def test_supervisor_via_group(self):
        g = PermissionGroup.objects.create(tenant=self.tenant, name='تكلفة', scope='branch',
                                           permissions={'view_cost_prices': True})
        g.users.add(self.sup)
        self.assertTrue(self.sup.has_perm_key('view_cost_prices'))
        self.assertIn('view_cost_prices', self.sup.get_permission_keys())
