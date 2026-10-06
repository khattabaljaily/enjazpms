"""
إدارة المستخدمين في نسخة المؤسسات: مدير الفرع هو من يضيف مستخدمي فرعه، ومدير
النشاط لا يدير المستخدمين بل يعيّن مدير كل فرع من شاشة الفروع.
"""
from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase


class BranchUserManagementTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع 1')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع 2')
        self.mgr1 = User.objects.create_user(
            username='mgr1', password='secret123', tenant=self.tenant,
            branch=self.b1, is_branch_supervisor=True,
        )
        self.staff1 = User.objects.create_user(
            username='staff1', password='secret123', tenant=self.tenant, branch=self.b1,
        )
        self.staff2 = User.objects.create_user(
            username='staff2', password='secret123', tenant=self.tenant, branch=self.b2,
        )

    def _new_user_data(self, **extra):
        data = {
            'username': 'newbie', 'first_name': 'جديد', 'last_name': 'موظف',
            'email': '', 'phone': '', 'password': 'secret12345',
            'password_confirm': 'secret12345', 'is_active': 'on',
        }
        data.update(extra)
        return data

    # ── مدير النشاط ────────────────────────────────────────────
    def test_tenant_admin_no_longer_manages_users(self):
        self.assertFalse(self.user.has_perm_key('view_users'))
        self.assertFalse(self.user.has_perm_key('add_users'))
        resp = self.client.post(reverse('accounts:user_create_api'), self._new_user_data(branch=self.b1.pk))
        self.assertNotEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_tenant_admin_creates_branch_manager_from_branch_form(self):
        resp = self.client.post(reverse('core:branch_create_api'), {
            'name': 'فرع 3', 'is_active': 'on',
            'manager_username': 'mgr3', 'manager_first_name': 'مدير', 'manager_last_name': 'ثلاثة',
            'manager_password': 'secret12345', 'manager_password_confirm': 'secret12345',
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        branch = Branch.objects.get(tenant=self.tenant, name='فرع 3')
        mgr = User.objects.get(username='mgr3')
        self.assertEqual((mgr.branch_id, mgr.is_branch_supervisor, mgr.tenant_id), (branch.id, True, self.tenant.id))
        self.assertEqual(branch.manager_id, mgr.id)

    def test_invalid_manager_rolls_back_branch_creation(self):
        resp = self.client.post(reverse('core:branch_create_api'), {
            'name': 'فرع 4', 'is_active': 'on',
            'manager_username': 'mgr4', 'manager_password': 'a', 'manager_password_confirm': 'b',
        })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Branch.objects.filter(name='فرع 4').exists())
        self.assertFalse(User.objects.filter(username='mgr4').exists())

    # ── مدير الفرع ─────────────────────────────────────────────
    def test_branch_manager_has_user_permissions(self):
        for key in ('view_users', 'add_users', 'change_users', 'delete_users'):
            self.assertTrue(self.mgr1.has_perm_key(key), key)

    def test_branch_manager_lists_only_his_branch_users(self):
        self.client.force_login(self.mgr1)
        resp = self.client.get(reverse('accounts:user_table_api'), {'draw': 1, 'start': 0, 'length': 50})
        names = sorted(r['username'] for r in resp.json()['data'])
        self.assertEqual(names, ['mgr1', 'staff1'])

    def test_new_user_is_forced_into_managers_branch(self):
        self.client.force_login(self.mgr1)
        resp = self.client.post(
            reverse('accounts:user_create_api'),
            self._new_user_data(branch=self.b2.pk, is_branch_supervisor='on'),
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        created = User.objects.get(username='newbie')
        self.assertEqual(created.branch_id, self.b1.id)
        self.assertFalse(created.is_branch_supervisor)
        self.assertFalse(created.is_tenant_admin)

    def test_cannot_touch_other_branch_users(self):
        self.client.force_login(self.mgr1)
        self.assertEqual(self.client.get(reverse('accounts:user_detail_api', args=[self.staff2.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('accounts:user_update_api', args=[self.staff2.pk]),
                                          self._new_user_data(username='staff2')).status_code, 404)
        self.assertEqual(self.client.post(reverse('accounts:user_delete_api', args=[self.staff2.pk])).status_code, 404)
        self.assertTrue(User.objects.filter(pk=self.staff2.pk).exists())

    def test_cannot_touch_tenant_admin_or_self_or_supervisor(self):
        self.client.force_login(self.mgr1)
        self.assertEqual(self.client.post(reverse('accounts:user_delete_api', args=[self.user.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('accounts:user_update_api', args=[self.mgr1.pk]),
                                          self._new_user_data(username='mgr1')).status_code, 403)

    def test_can_update_and_delete_own_branch_staff(self):
        self.client.force_login(self.mgr1)
        resp = self.client.post(reverse('accounts:user_update_api', args=[self.staff1.pk]),
                                self._new_user_data(username='staff1', first_name='معدّل', branch=self.b2.pk))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.staff1.refresh_from_db()
        self.assertEqual((self.staff1.first_name, self.staff1.branch_id), ('معدّل', self.b1.id))
        self.assertEqual(self.client.post(reverse('accounts:user_delete_api', args=[self.staff1.pk])).status_code, 200)

    def test_manager_cannot_assign_group_with_admin_permissions(self):
        safe = PermissionGroup.objects.create(tenant=self.tenant, name='كاشير', scope='branch',
                                              permissions={'view_sales': True})
        risky = PermissionGroup.objects.create(tenant=self.tenant, name='مخاطر', scope='',
                                               permissions={'view_sales': True, 'add_branches': True})
        self.client.force_login(self.mgr1)
        resp = self.client.post(reverse('accounts:user_create_api'),
                                self._new_user_data(permission_groups=[risky.pk]))
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(reverse('accounts:user_create_api'),
                                self._new_user_data(permission_groups=[safe.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(list(User.objects.get(username='newbie').permission_groups.all()), [safe])

    def test_user_list_page_renders_for_branch_manager(self):
        self.client.force_login(self.mgr1)
        resp = self.client.get(reverse('accounts:user_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'لا يوجد فروع بعد')
        self.assertNotContains(resp, 'id_is_branch_supervisor')
