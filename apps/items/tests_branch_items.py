"""نسخة المؤسسات: السماح للفروع بإضافة منتجات للكتالوج المركزي (Settings.branches_can_manage_items)."""
from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.core.models import Branch, Settings
from apps.core.test_utils import TenantTestCase, make_item
from apps.items.models import Item


class BranchItemsBase(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.sup1 = User.objects.create_user(
            username='bi-sup1', password='x12345678', tenant=self.tenant, branch=self.b1, is_branch_supervisor=True)
        self.sup2 = User.objects.create_user(
            username='bi-sup2', password='x12345678', tenant=self.tenant, branch=self.b2, is_branch_supervisor=True)
        self.central_item = make_item(self.tenant, name='منتج مركزي')
        self.own_item = make_item(self.tenant, name='منتج الشمال', branch=self.b1)
        self.other_item = make_item(self.tenant, name='منتج الجنوب', branch=self.b2)

    def allow(self, value=True):
        Settings.objects.update_or_create(tenant=self.tenant, defaults={'branches_can_manage_items': value})

    def create_payload(self, name='جديد'):
        return {'name': name, 'item_type': 'product', 'cost_price': '1', 'selling_price': '2',
                'min_selling_price': '0', 'tax_rate': '0', 'min_quantity': '0', 'max_quantity': '0',
                'is_active': 'on'}


class BranchItemsDisabledTests(BranchItemsBase):
    def test_branch_cannot_add_edit_or_delete(self):
        self.assertFalse(self.sup1.has_perm_key('import_items'))
        self.client.force_login(self.sup1)
        before = Item.objects.count()
        for url, data in ((reverse('items:create_api'), self.create_payload()),
                          (reverse('items:update_api', args=[self.own_item.pk]), {}),
                          (reverse('items:delete_api', args=[self.own_item.pk]), {})):
            self.assertNotEqual(self.client.post(url, data).status_code, 200)
        self.assertEqual(Item.objects.count(), before)

    def test_sidebar_link_hidden_for_branch(self):
        self.client.force_login(self.sup1)
        self.assertNotContains(self.client.get(reverse('core:dashboard')), reverse('items:list'))


class BranchItemsEnabledTests(BranchItemsBase):
    def setUp(self):
        super().setUp()
        self.allow()

    def test_branch_add_is_tagged_with_its_branch(self):
        self.client.force_login(self.sup1)
        resp = self.client.post(reverse('items:create_api'), self.create_payload('فرعي'))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Item.objects.get(name='فرعي').branch, self.b1)

    def test_branch_edits_and_deletes_only_own_items(self):
        self.client.force_login(self.sup1)
        for item in (self.central_item, self.other_item):
            self.assertEqual(self.client.post(reverse('items:update_api', args=[item.pk]), {}).status_code, 403)
            self.assertEqual(self.client.post(reverse('items:delete_api', args=[item.pk])).status_code, 403)
            self.assertTrue(Item.objects.filter(pk=item.pk).exists())
        resp = self.client.post(reverse('items:delete_api', args=[self.own_item.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(Item.objects.filter(pk=self.own_item.pk).exists())

    def test_branch_can_import_along_with_add(self):
        self.assertTrue(self.sup1.has_perm_key('import_items'))
        self.assertTrue(self.sup1.has_perm_key('add_items'))

    def test_mine_filter_and_can_manage_flag(self):
        self.client.force_login(self.sup1)
        rows = self.client.get(reverse('items:table_api'), {'length': 50}).json()['data']
        by_name = {r['name']: r['can_manage'] for r in rows}
        self.assertEqual(by_name, {'منتج مركزي': False, 'منتج الشمال': True, 'منتج الجنوب': False})
        rows = self.client.get(reverse('items:table_api'), {'length': 50, 'mine': '1'}).json()['data']
        self.assertEqual([r['name'] for r in rows], ['منتج الشمال'])

    def test_owner_manages_everything(self):
        self.client.force_login(self.user)
        rows = self.client.get(reverse('items:table_api'), {'length': 50}).json()['data']
        self.assertTrue(all(r['can_manage'] for r in rows))

    def test_sidebar_link_shown_for_branch_without_categories(self):
        self.client.force_login(self.sup1)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn(f'href="{reverse("items:list")}"', html)
        self.assertNotIn(f'href="{reverse("items:category_list")}"', html)

    def test_items_page_hides_categories_button_for_branch(self):
        self.client.force_login(self.sup1)
        link = f'href="{reverse("items:category_list")}"'
        self.assertNotContains(self.client.get(reverse('items:list')), link)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('items:list')), link)

    def _employee(self, perms):
        group = PermissionGroup.objects.create(tenant=self.tenant, name='موظف منتجات', scope='branch', permissions=perms)
        emp = User.objects.create_user(username='bi-emp', password='x12345678', tenant=self.tenant, branch=self.b1)
        group.users.add(emp)
        return emp

    def test_add_items_alone_implies_list_access_and_sidebar_link(self):
        emp = self._employee({'add_items': True})
        self.assertTrue(emp.has_perm_key('view_items'))
        self.assertIn('view_items', emp.get_permission_keys())
        self.client.force_login(emp)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn(f'href="{reverse("items:list")}"', html)
        self.assertEqual(self.client.get(reverse('items:list')).status_code, 200)

    def test_group_without_item_keys_gets_no_list_access(self):
        emp = self._employee({'view_dashboard': True})
        self.assertFalse(emp.has_perm_key('view_items'))


class NonEnterpriseUnaffectedTests(TenantTestCase):
    def test_flag_has_no_effect_outside_enterprise(self):
        Settings.objects.update_or_create(tenant=self.tenant, defaults={'branches_can_manage_items': True})
        self.assertFalse(self.tenant.branches_manage_items())
