"""
اختبار resolve_report_scope (apps/core/utils.py) — نقطة الدخول الموحّدة
لفلترة Dashboard/التقارير حسب الفرع (خطة تنفيذ Enterprise، القسم 8.1).
"""
from django.conf import settings
from django.test import TestCase, RequestFactory
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch, BusinessType, Tenant
from apps.core.utils import resolve_report_scope


class ResolveReportScopeTests(TestCase):
    def setUp(self):
        self.bt = BusinessType.objects.create(name='bt-scope', name_ar='نوع', slug='bt-scope')
        self.terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.rf = RequestFactory()

    def _request(self, tenant, branch):
        req = self.rf.get('/dashboard/')
        req.tenant = tenant
        req.branch = branch
        return req

    def test_single_store_unaffected(self):
        tenant = Tenant.objects.create(
            name='Single', business_type=self.bt, subscription_plan='basic',
            version_type='single_store', currency='SDG', **self.terms,
        )
        branch, is_central, branches = resolve_report_scope(self._request(tenant, None))
        self.assertIsNone(branch)
        self.assertFalse(is_central)
        self.assertEqual(branches.count(), 0)

    def test_branch_scoped_user_locked_to_own_branch_even_with_querystring(self):
        tenant = Tenant.objects.create(
            name='Ent', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        b1 = Branch.objects.create(tenant=tenant, name='Branch 1')
        b2 = Branch.objects.create(tenant=tenant, name='Branch 2')
        req = self._request(tenant, b1)
        req = self.rf.get('/dashboard/', {'branch': str(b2.id)})
        req.tenant = tenant
        req.branch = b1
        branch, is_central, branches = resolve_report_scope(req)
        self.assertEqual(branch, b1)
        self.assertFalse(is_central)

    def test_central_admin_defaults_to_all_branches(self):
        tenant = Tenant.objects.create(
            name='Ent2', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        Branch.objects.create(tenant=tenant, name='Branch 1')
        Branch.objects.create(tenant=tenant, name='Branch 2')
        branch, is_central, branches = resolve_report_scope(self._request(tenant, None))
        self.assertIsNone(branch)
        self.assertTrue(is_central)
        self.assertEqual(branches.count(), 2)

    def test_central_admin_can_pick_one_branch_via_querystring(self):
        tenant = Tenant.objects.create(
            name='Ent3', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        b1 = Branch.objects.create(tenant=tenant, name='Branch 1')
        req = self.rf.get('/dashboard/', {'branch': str(b1.id)})
        req.tenant = tenant
        req.branch = None
        branch, is_central, branches = resolve_report_scope(req)
        self.assertEqual(branch, b1)
        self.assertTrue(is_central)

    def test_central_admin_invalid_branch_id_falls_back_to_all(self):
        tenant = Tenant.objects.create(
            name='Ent4', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        other_tenant = Tenant.objects.create(
            name='Other', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        foreign_branch = Branch.objects.create(tenant=other_tenant, name='Foreign')
        req = self.rf.get('/dashboard/', {'branch': str(foreign_branch.id)})
        req.tenant = tenant
        req.branch = None
        branch, is_central, branches = resolve_report_scope(req)
        self.assertIsNone(branch)
        self.assertTrue(is_central)


class DashboardBranchFilterViewTests(TestCase):
    """اختبار تكامل بسيط: الـ Dashboard الحقيقي يستخدم resolve_report_scope فعلياً."""

    def setUp(self):
        self.bt = BusinessType.objects.create(name='bt-dash', name_ar='نوع', slug='bt-dash')
        self.terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.tenant = Tenant.objects.create(
            name='Ent Dash', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        self.branch = Branch.objects.create(tenant=self.tenant, name='Branch 1')
        self.admin = User.objects.create_user(
            username='dash-admin', password='secret123', tenant=self.tenant, is_tenant_admin=True,
        )

    def test_dashboard_shows_branch_selector_for_central_admin(self):
        self.client.force_login(self.admin)
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_central_admin'])
        self.assertIn(self.branch, resp.context['branches_for_filter'])

    def test_dashboard_no_selector_for_single_store(self):
        bt2 = BusinessType.objects.create(name='bt-dash2', name_ar='نوع2', slug='bt-dash2')
        single_tenant = Tenant.objects.create(
            name='Single Dash', business_type=bt2, subscription_plan='basic',
            version_type='single_store', currency='SDG', **self.terms,
        )
        admin = User.objects.create_user(
            username='dash-admin-single', password='secret123', tenant=single_tenant, is_tenant_admin=True,
        )
        self.client.force_login(admin)
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['is_central_admin'])
