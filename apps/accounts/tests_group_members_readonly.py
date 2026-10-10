"""
شاشة مجموعات الصلاحيات تعرض الأعضاء الحاليين فقط: لا مودال لتعديلهم، والإنشاء/التعديل
يتجاهلان أي users[] — العضوية تُسند من شاشة إدارة المستخدمين.
"""
from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.core.test_utils import TenantTestCase


class GroupMembersReadOnlyTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.group = PermissionGroup.objects.create(
            tenant=self.tenant, name='كاشير', permissions={'view_dashboard': True})
        self.member = User.objects.create_user(username='gm-member', password='x12345678', tenant=self.tenant)
        self.other = User.objects.create_user(username='gm-other', password='x12345678', tenant=self.tenant)
        self.group.users.add(self.member)
        self.client.force_login(self.user)

    def test_page_lists_members_without_edit_modal(self):
        page = self.client.get(reverse('accounts:permission_group_list'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="membersChips"')
        self.assertNotContains(page, 'membersModal')
        self.assertNotContains(page, 'btnSaveMembers')
        detail = self.client.get(reverse('accounts:permission_group_detail_api', args=[self.group.pk])).json()
        self.assertIn(self.member.pk, detail['data']['users'] if 'data' in detail else detail['users'])

    def test_update_ignores_submitted_members(self):
        resp = self.client.post(reverse('accounts:permission_group_update_api', args=[self.group.pk]),
                                {'name': 'كاشير', 'permissions': '{}', 'users[]': [self.other.pk]})
        self.assertTrue(resp.json()['success'])
        self.assertEqual(list(self.group.users.all()), [self.member])

    def test_create_ignores_submitted_members(self):
        resp = self.client.post(reverse('accounts:permission_group_create_api'),
                                {'name': 'مخازن', 'permissions': '{}', 'users[]': [self.other.pk]})
        self.assertTrue(resp.json()['success'])
        self.assertFalse(PermissionGroup.objects.get(tenant=self.tenant, name='مخازن').users.exists())
