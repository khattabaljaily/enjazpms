"""
اختبار Branch.assign_manager (القسم 7.1 من خطة تنفيذ Enterprise) وendpoint
branch_assign_manager_api: تعيين/تغيير/إزالة مدير فرع، مع تزامن
Branch.manager ↔ User.branch/is_branch_supervisor، وقاعدة "مدير واحد نشط
لكل فرع".
"""
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch, BusinessType, Tenant


class BranchAssignManagerModelTests(TestCase):
    def setUp(self):
        bt = BusinessType.objects.create(name='bt-mgr', name_ar='نوع', slug='bt-mgr')
        terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.tenant = Tenant.objects.create(
            name='Ent Mgr', business_type=bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **terms,
        )
        self.branch = Branch.objects.create(tenant=self.tenant, name='Branch A')
        self.user_x = User.objects.create_user(username='mgr-x', password='s3cret123', tenant=self.tenant)
        self.user_y = User.objects.create_user(username='mgr-y', password='s3cret123', tenant=self.tenant)

    def test_assign_manager_syncs_both_sides(self):
        Branch.assign_manager(self.branch, self.user_x)
        self.branch.refresh_from_db()
        self.user_x.refresh_from_db()
        self.assertEqual(self.branch.manager, self.user_x)
        self.assertEqual(self.user_x.branch, self.branch)
        self.assertTrue(self.user_x.is_branch_supervisor)

    def test_reassigning_demotes_previous_manager(self):
        Branch.assign_manager(self.branch, self.user_x)
        Branch.assign_manager(self.branch, self.user_y)
        self.branch.refresh_from_db()
        self.user_x.refresh_from_db()
        self.user_y.refresh_from_db()
        self.assertEqual(self.branch.manager, self.user_y)
        self.assertFalse(self.user_x.is_branch_supervisor)
        self.assertTrue(self.user_y.is_branch_supervisor)

    def test_removing_manager(self):
        Branch.assign_manager(self.branch, self.user_x)
        Branch.assign_manager(self.branch, None)
        self.branch.refresh_from_db()
        self.user_x.refresh_from_db()
        self.assertIsNone(self.branch.manager)
        self.assertFalse(self.user_x.is_branch_supervisor)

    def test_reassigning_same_user_is_noop_safe(self):
        Branch.assign_manager(self.branch, self.user_x)
        Branch.assign_manager(self.branch, self.user_x)
        self.user_x.refresh_from_db()
        self.assertTrue(self.user_x.is_branch_supervisor)


class BranchAssignManagerApiTests(TestCase):
    def setUp(self):
        bt = BusinessType.objects.create(name='bt-mgr-api', name_ar='نوع', slug='bt-mgr-api')
        terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.tenant = Tenant.objects.create(
            name='Ent Mgr Api', business_type=bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **terms,
        )
        self.branch = Branch.objects.create(tenant=self.tenant, name='Branch A')
        self.admin = User.objects.create_user(
            username='api-admin', password='s3cret123', tenant=self.tenant, is_tenant_admin=True,
        )
        self.employee = User.objects.create_user(username='api-emp', password='s3cret123', tenant=self.tenant)
        self.client.force_login(self.admin)

    def test_assign_manager_via_api(self):
        url = f'/branches/api/{self.branch.id}/assign-manager/'
        resp = self.client.post(url, {'user_id': self.employee.id})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.branch.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertEqual(self.branch.manager, self.employee)
        self.assertTrue(self.employee.is_branch_supervisor)

    def test_cannot_assign_tenant_admin_as_branch_manager(self):
        url = f'/branches/api/{self.branch.id}/assign-manager/'
        resp = self.client.post(url, {'user_id': self.admin.id})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_remove_manager_via_api(self):
        from apps.core.models import Branch as BranchModel
        BranchModel.assign_manager(self.branch, self.employee)
        url = f'/branches/api/{self.branch.id}/assign-manager/'
        resp = self.client.post(url, {})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.branch.refresh_from_db()
        self.assertIsNone(self.branch.manager)
