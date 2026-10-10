"""
تقارير مدير النشاط في نسخة المؤسسات: صورة عامة فقط — لا تقارير تفاصيل الفرع (عملاء،
موردو الفرع، مناديب، مستخدمون، كشوف وحركات). مشرف الفرع يحتفظ بكل تقاريره كما هي،
ومجموعات «إدارة النشاط» لا تمنح إلا ما يملكه مدير النشاط.
"""
from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.accounts.permissions import (
    get_branch_supervisor_permission_keys, get_enterprise_owner_permission_keys,
    get_scope_excluded_permission_keys,
)
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase

BRANCH_DETAIL_REPORTS = {
    'view_sales_by_customer_report': 'reports:by_customer_report',
    'view_sales_by_user_report': 'reports:by_user_report',
    'view_purchases_by_supplier_report': 'purchases:reports:by_supplier_report',
    'view_purchases_by_user_report': 'purchases:reports:by_user_report',
    'view_sales_customer_statement_report': 'reports:customer_statement',
    'view_sales_customer_balances_report': 'reports:customer_balances',
    'view_purchases_supplier_statement_report': 'purchases:reports:supplier_statement',
    'view_purchases_supplier_balances_report': 'purchases:reports:supplier_balances',
    'view_expenses_details_report': 'expenses:reports:details_report',
    'view_treasury_movements_report': 'treasury:reports:movements_report',
    'view_bank_account_movements_report': 'bank_accounts:reports:movements_report',
}
OVERVIEW_REPORTS = [
    'reports:summary_report', 'reports:by_item_report', 'reports:by_date_report',
    'purchases:reports:summary_report', 'purchases:reports:by_item_report', 'purchases:reports:by_date_report',
    'stocks:reports:summary_report', 'stocks:reports:valuation_report',
    'expenses:reports:summary_report', 'treasury:reports:balances_report', 'reports:income_statement',
]


class OwnerReportsScopeTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def test_owner_keeps_overview_and_loses_branch_detail_reports(self):
        owner = set(get_enterprise_owner_permission_keys())
        supervisor = set(get_branch_supervisor_permission_keys())
        for key in BRANCH_DETAIL_REPORTS:
            self.assertNotIn(key, owner, key)
            self.assertIn(key, supervisor, key)
        self.assertNotIn('view_agent_statement_report', owner)
        for key in ('view_central_reports', 'view_central_purchases_report'):
            self.assertIn(key, owner)
            self.assertNotIn(key, supervisor)
        self.assertEqual(set(get_scope_excluded_permission_keys('branch_ops')), set(BRANCH_DETAIL_REPORTS) | {
            'view_sales_payments_report', 'view_sales_returns_report', 'view_purchases_payments_report',
            'view_purchases_returns_report', 'view_treasury_statement_report', 'view_bank_account_statement_report',
            # طلبات المتجر يستقبلها الفرع ويقبلها (راجع apps/store/tests_store_branch_orders.py)
            'view_store_orders', 'manage_store_orders'})

    def test_owner_routes_follow_the_split(self):
        for name in OVERVIEW_REPORTS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        for key, name in BRANCH_DETAIL_REPORTS.items():
            self.assertIn(self.client.get(reverse(name)).status_code, (302, 403), name)
        dash = self.client.get(reverse('core:dashboard'))
        for name in BRANCH_DETAIL_REPORTS.values():
            self.assertNotContains(dash, f'href="{reverse(name)}"', msg_prefix=name)
        self.assertContains(dash, f'href="{reverse("reports:by_item_report")}"')

    def test_branch_supervisor_keeps_all_reports(self):
        branch = Branch.objects.create(tenant=self.tenant, name='فرع')
        sup = User.objects.create_user(username='rs-sup', password='x12345678', tenant=self.tenant,
                                       branch=branch, is_branch_supervisor=True)
        self.client.force_login(sup)
        for name in BRANCH_DETAIL_REPORTS.values():
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_admin_group_never_grants_branch_detail_keys(self):
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='قديمة', scope='admin', is_active=True,
            permissions={'view_sales_summary_report': True, 'view_sales_by_customer_report': True})
        staff = User.objects.create_user(username='rs-staff', password='x12345678', tenant=self.tenant)
        staff.permission_groups.add(group)
        self.assertTrue(staff.has_perm_key('view_sales_summary_report'))
        self.assertFalse(staff.has_perm_key('view_sales_by_customer_report'))
        self.assertNotIn('view_sales_by_customer_report', staff.get_permission_keys())

    def test_group_screen_hides_branch_detail_keys_for_admin_scope(self):
        page = self.client.get(reverse('accounts:permission_group_list'))
        self.assertContains(page, 'GROUP_SCOPE_EXCLUDED_KEYS')
        self.assertContains(page, '"view_sales_by_customer_report"')
