"""
مستخدمو إدارة النشاط (نسخة المؤسسات): مدير النشاط يضيف موظفي الإدارة بلا فرع، ولا يُسند
لهم إلا مجموعات «إدارة النشاط»، ولا يرى موظفي الفروع (يديرهم مشرف كل فرع).
"""
from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase


class HeadOfficeUsersTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.admin_group = PermissionGroup.objects.create(
            tenant=self.tenant, name='محاسبة الإدارة', scope='admin', is_active=True,
            permissions={'view_dashboard': True, 'view_admin_users': True})
        self.branch_group = PermissionGroup.objects.create(
            tenant=self.tenant, name='كاشير', scope='branch', is_active=True,
            permissions={'view_sales': True, 'add_sales': True})
        self.supervisor = User.objects.create_user(
            username='ho-sup', password='x12345678', tenant=self.tenant,
            branch=self.branch, is_branch_supervisor=True)
        self.cashier = User.objects.create_user(
            username='ho-cashier', password='x12345678', tenant=self.tenant, branch=self.branch)

    def create(self, **extra):
        data = {'username': 'ho-new', 'first_name': 'سارة', 'last_name': 'علي', 'is_active': 'on',
                'password': 'Strong-pass-123', 'password_confirm': 'Strong-pass-123'}
        data.update(extra)
        return self.client.post(reverse('accounts:user_create_api'), data)

    def rows(self):
        resp = self.client.get(reverse('accounts:user_table_api'), {'draw': 1, 'start': 0, 'length': 100})
        self.assertEqual(resp.status_code, 200)
        return {r['username'] for r in resp.json()['data']}

    def test_owner_opens_head_office_users_screen(self):
        page = self.client.get(reverse('accounts:user_list'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'مستخدمو إدارة النشاط')
        self.assertContains(page, 'محاسبة الإدارة')
        self.assertNotContains(page, 'كاشير')
        self.assertNotContains(page, 'id="id_branch"')
        self.assertContains(self.client.get(reverse('core:dashboard')), reverse('accounts:user_list'))

    def test_owner_creates_branchless_user_with_admin_group(self):
        resp = self.create(branch=self.branch.pk, is_branch_supervisor='on',
                           permission_groups=[self.admin_group.pk])
        self.assertTrue(resp.json()['success'], resp.content)
        user = User.objects.get(username='ho-new')
        self.assertIsNone(user.branch_id)
        self.assertFalse(user.is_branch_supervisor)
        self.assertEqual(list(user.permission_groups.all()), [self.admin_group])
        self.assertEqual(self.rows(), {'ho-new'})

    def test_owner_cannot_assign_branch_group(self):
        resp = self.create(permission_groups=[self.branch_group.pk])
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(username='ho-new').exists())

    def test_owner_cannot_touch_branch_users(self):
        self.assertNotIn('ho-cashier', self.rows())
        self.assertEqual(self.client.get(reverse('accounts:user_detail_api', args=[self.cashier.pk])).status_code, 404)
        resp = self.client.post(reverse('accounts:user_update_api', args=[self.cashier.pk]),
                                {'username': 'ho-cashier', 'is_active': 'on'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(self.client.post(reverse('accounts:user_delete_api', args=[self.cashier.pk])).status_code, 404)

    def test_head_office_staff_gets_admin_scope_only(self):
        self.create(permission_groups=[self.admin_group.pk])
        staff = User.objects.get(username='ho-new')
        self.assertFalse(staff.has_perm_key('add_sales'))
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('accounts:user_list')).status_code, 200)
        self.assertEqual(self.rows(), {'ho-new'})
        # بلا صلاحية الإضافة في مجموعته
        self.assertNotEqual(self.create(username='ho-other').status_code, 200)

    def test_branch_supervisor_keeps_branch_scope(self):
        self.create(permission_groups=[self.admin_group.pk])
        self.client.force_login(self.supervisor)
        self.assertEqual(self.rows(), {'ho-cashier', 'ho-sup'})  # كما كان: موظفو فرعه ونفسه، لا موظفو الإدارة
        page = self.client.get(reverse('accounts:user_list'))
        self.assertNotContains(page, 'مستخدمو إدارة النشاط')
        self.assertNotContains(page, 'محاسبة الإدارة')


class NonEnterpriseUsersUnchangedTests(TenantTestCase):
    def test_owner_sees_all_users_as_before(self):
        User.objects.create_user(username='plain-u', password='x12345678', tenant=self.tenant)
        resp = self.client.get(reverse('accounts:user_table_api'), {'draw': 1, 'start': 0, 'length': 100})
        self.assertIn('plain-u', {r['username'] for r in resp.json()['data']})
        self.assertNotContains(self.client.get(reverse('accounts:user_list')), 'مستخدمو إدارة النشاط')
