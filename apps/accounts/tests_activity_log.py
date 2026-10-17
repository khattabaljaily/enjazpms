"""سجل النشاط: مدير الفرع يرى نشاط مستخدمي فرعه، مع تصفية بالتاريخ وترقيم الصفحات (ومدير النشاط كذلك)."""
from datetime import datetime, timedelta

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User, UserActivity
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase


class ActivityLogTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.a = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.b = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        mk = lambda name, branch, sup=False: User.objects.create_user(
            username=name, password='x12345678', tenant=self.tenant, branch=branch, is_branch_supervisor=sup)
        self.mgr_a = mk('mgr-a', self.a, True)
        self.emp_a = mk('emp-a', self.a)
        self.mgr_b = mk('mgr-b', self.b, True)

    def _act(self, user, title, days_ago=0):
        act = UserActivity.objects.create(tenant=self.tenant, user=user, branch=user.branch, title=title)
        UserActivity.objects.filter(pk=act.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return act

    def _page(self, viewer, **params):
        self.client.force_login(viewer)
        return self.client.get(reverse('accounts:activity_log'), params)

    def test_branch_manager_sees_branch_users_only(self):
        self._act(self.emp_a, 'عمل-موظف-أ')
        self._act(self.mgr_a, 'عمل-مدير-أ')
        self._act(self.mgr_b, 'عمل-مدير-ب')
        resp = self._page(self.mgr_a)
        self.assertContains(resp, 'عمل-موظف-أ')
        self.assertContains(resp, 'عمل-مدير-أ')
        self.assertNotContains(resp, 'عمل-مدير-ب')
        self.assertContains(resp, 'سجل نشاط الفرع')

    def test_branch_manager_cannot_filter_by_user_of_other_branch(self):
        self._act(self.mgr_b, 'عمل-مدير-ب')
        self._act(self.emp_a, 'عمل-موظف-أ')
        resp = self._page(self.mgr_a, user=self.mgr_b.pk)
        self.assertNotContains(resp, 'عمل-مدير-ب')
        self.assertContains(resp, 'عمل-موظف-أ')   # الفلتر الغريب يُتجاهل

    def test_regular_branch_employee_sees_only_own(self):
        self._act(self.emp_a, 'عمل-موظف-أ')
        self._act(self.mgr_a, 'عمل-مدير-أ')
        resp = self._page(self.emp_a)
        self.assertContains(resp, 'عمل-موظف-أ')
        self.assertNotContains(resp, 'عمل-مدير-أ')

    def test_date_range_filter(self):
        self._act(self.emp_a, 'نشاط-ماضٍ-٩٨٧', days_ago=30)
        self._act(self.emp_a, 'نشاط-قريب-٧٨٩', days_ago=1)
        today = timezone.localdate()
        resp = self._page(self.mgr_a, date_from=(today - timedelta(days=5)).isoformat(), date_to=today.isoformat())
        self.assertContains(resp, 'نشاط-قريب-٧٨٩')
        self.assertNotContains(resp, 'نشاط-ماضٍ-٩٨٧')
        resp = self._page(self.mgr_a, date_to=(today - timedelta(days=10)).isoformat())
        self.assertContains(resp, 'نشاط-ماضٍ-٩٨٧')
        self.assertNotContains(resp, 'نشاط-قريب-٧٨٩')

    def test_invalid_dates_are_ignored(self):
        self._act(self.emp_a, 'عمل')
        self.assertContains(self._page(self.mgr_a, date_from='ليس-تاريخًا', date_to='99-99-99'), 'عمل')

    def test_pagination_for_branch_manager_and_owner(self):
        for i in range(30):
            self._act(self.emp_a, f'ن{i:02d}ن')
        for who in (self.mgr_a, self.user):
            first = self._page(who)
            self.assertEqual(first.context['page_obj'].paginator.num_pages, 2)
            self.assertEqual(len(first.context['page_obj']), 24)
            self.assertContains(first, 'page=2')
            second = self._page(who, page=2)
            self.assertEqual(len(second.context['page_obj']), 6)
            self.assertEqual(self._page(who, page=999).context['page_obj'].number, 2)   # صفحة خارج النطاق ← الأخيرة

    def test_owner_filters_by_date_and_keeps_filter_in_page_links(self):
        for i in range(30):
            self._act(self.emp_a, f'جديد{i:02d}', days_ago=1)
        self._act(self.mgr_b, 'قديم-جدًا', days_ago=60)
        today = timezone.localdate()
        resp = self._page(self.user, date_from=(today - timedelta(days=5)).isoformat())
        self.assertNotContains(resp, 'قديم-جدًا')
        self.assertEqual(resp.context['total_count'], 30)
        self.assertContains(resp, 'date_from=')
        self.assertContains(resp, 'page=2')
