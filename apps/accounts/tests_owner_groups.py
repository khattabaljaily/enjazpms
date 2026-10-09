"""
المجموعات والصلاحيات لمدير النشاط (نسخة المؤسسات):
  - مجموعة "مدير النشاط" التلقائية مخفية ولا تُعدَّل ولا تُحذف من الشاشة.
  - مجموعات "إدارة النشاط" لا تحمل إلا صلاحيات قوائم لوحة تحكم مدير النشاط.
  - مجموعات "الفروع" لا تحمل صلاحيات إدارة النشاط.
"""
import json

from django.urls import reverse

from apps.accounts.models import PermissionGroup
from apps.accounts.permissions import (
    ENTERPRISE_OWNER_EXCLUDED_CATEGORIES, get_branch_supervisor_permission_keys,
    get_enterprise_owner_permission_keys,
)
from apps.core.models import TenantCapabilities
from apps.core.test_utils import TenantTestCase


class OwnerGroupVisibilityAndScopeTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant,
            defaults={f.name: True for f in TenantCapabilities._meta.get_fields()
                      if getattr(f, 'name', '').startswith('has_')})
        self.owner_group = PermissionGroup.create_owner_group(self.tenant)
        self.admin_group = PermissionGroup.objects.create(
            tenant=self.tenant, name='مساعد إداري', scope='admin', permissions={'view_dashboard': True})

    def _table(self):
        resp = self.client.get(reverse('accounts:permission_group_table_api'),
                               {'draw': 1, 'start': 0, 'length': 100})
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def _post(self, url_name, args=(), **data):
        return self.client.post(reverse(f'accounts:{url_name}', args=args), data)

    # ---- إخفاء مجموعة مدير النشاط -----------------------------------------
    def test_owner_group_is_hidden_from_table(self):
        payload = self._table()
        names = [row['name'] for row in payload['data']]
        self.assertNotIn('مدير النشاط', names)
        self.assertIn('مساعد إداري', names)
        self.assertEqual(payload['recordsTotal'], 1)

    def test_owner_group_hidden_from_search_too(self):
        resp = self.client.get(reverse('accounts:permission_group_table_api'),
                               {'draw': 1, 'start': 0, 'length': 100, 'search[value]': 'مدير'})
        self.assertEqual(resp.json()['data'], [])

    def test_owner_group_cannot_be_read_updated_or_deleted(self):
        pk = self.owner_group.pk
        self.assertEqual(self.client.get(reverse('accounts:permission_group_detail_api', args=[pk])).status_code, 404)
        self.assertEqual(self._post('permission_group_update_api', [pk], name='x', scope='admin').status_code, 404)
        self.assertEqual(self._post('permission_group_delete_api', [pk]).status_code, 404)
        self.assertTrue(PermissionGroup.objects.filter(pk=pk, name='مدير النشاط').exists())

    def test_other_groups_remain_manageable(self):
        pk = self.admin_group.pk
        self.assertEqual(self.client.get(reverse('accounts:permission_group_detail_api', args=[pk])).status_code, 200)

    # ---- نطاق "إدارة النشاط" ---------------------------------------------------
    def test_admin_scope_group_only_keeps_owner_dashboard_permissions(self):
        owner_keys = set(get_enterprise_owner_permission_keys())
        branch_ops_only = {'view_customers', 'view_sales', 'view_suppliers', 'view_expenses',
                           'view_users', 'view_treasuries', 'view_bank_accounts', 'view_employee_salaries'}
        self.assertFalse(branch_ops_only & owner_keys, 'مفاتيح عمليات الفرع يجب ألا تكون ضمن صلاحيات مدير النشاط')
        everything = {k: True for k in owner_keys | branch_ops_only}
        resp = self._post('permission_group_create_api', name='إدارة عليا', scope='admin',
                          permissions=json.dumps(everything))
        self.assertEqual(resp.status_code, 200, resp.content)
        saved = PermissionGroup.objects.get(tenant=self.tenant, name='إدارة عليا')
        enabled = set(saved.get_permission_keys())
        self.assertTrue(enabled)
        self.assertLessEqual(enabled, owner_keys)
        self.assertFalse(enabled & branch_ops_only)

    def test_admin_scope_update_also_drops_branch_operation_permissions(self):
        resp = self._post('permission_group_update_api', [self.admin_group.pk], name='مساعد إداري',
                          scope='admin', is_active='on',
                          permissions=json.dumps({'view_dashboard': True, 'view_sales': True, 'view_customers': True}))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.admin_group.refresh_from_db()
        self.assertEqual(set(self.admin_group.get_permission_keys()), {'view_dashboard'})

    def test_branch_scope_group_cannot_carry_admin_only_permissions(self):
        resp = self._post('permission_group_create_api', name='موظف فرع', scope='branch',
                          permissions=json.dumps({'view_sales': True, 'view_branches': True,
                                                  'view_tenant_settings': True, 'view_permissiongroups': True}))
        self.assertEqual(resp.status_code, 200, resp.content)
        saved = PermissionGroup.objects.get(tenant=self.tenant, name='موظف فرع')
        enabled = set(saved.get_permission_keys())
        self.assertTrue(enabled <= set(get_branch_supervisor_permission_keys()))
        self.assertIn('view_sales', enabled)
        self.assertFalse(enabled & {'view_branches', 'view_tenant_settings', 'view_permissiongroups'})

    def test_enterprise_group_requires_scope(self):
        resp = self._post('permission_group_create_api', name='بلا نطاق', permissions='{}')
        self.assertEqual(resp.status_code, 400)

    def test_page_excludes_branch_operation_categories_for_admin_scope(self):
        resp = self.client.get(reverse('accounts:permission_group_list'))
        self.assertEqual(resp.status_code, 200)
        excluded = json.loads(resp.context['group_scope_excluded'])
        self.assertEqual(set(excluded['admin']), set(ENTERPRISE_OWNER_EXCLUDED_CATEGORIES))
        for cat in ('العملاء', 'الموردين', 'المبيعات', 'المشتريات', 'المصروفات', 'الخزائن', 'رواتب الموظفين'):
            self.assertIn(cat, excluded['admin'])
        for cat in ('الفروع', 'المتجر الإلكتروني', 'إعدادات النشاط التجاري'):
            self.assertNotIn(cat, excluded['admin'])
        self.assertContains(resp, 'لوحة تحكم مدير النشاط')

    def test_owner_group_not_offered_when_creating_users(self):
        from apps.accounts.forms import UserManagementForm
        form = UserManagementForm(tenant=self.tenant)
        names = list(form.fields['permission_groups'].queryset.values_list('name', flat=True))
        self.assertNotIn('مدير النشاط', names)
