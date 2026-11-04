"""الملف الشخصي (تعديل البيانات وتغيير كلمة المرور) وتسجيل الدخول باسم المستخدم أو البريد."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User, UserActivity
from apps.core.test_utils import TenantTestCase


class ProfileTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.member = User.objects.create_user(
            username='member1', password='OldPass#2026', email='m1@example.com', tenant=self.tenant)
        self.client.force_login(self.member)
        self.url = reverse('accounts:profile')

    def test_page_renders_real_forms(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'ستكون متاحة قريباً')
        self.assertContains(resp, 'name="old_password"')
        self.assertContains(resp, 'name="first_name"')

    def test_change_password(self):
        resp = self.client.post(self.url, {
            'action': 'password', 'old_password': 'OldPass#2026',
            'new_password1': 'NewPass#2027x', 'new_password2': 'NewPass#2027x'})
        self.assertRedirects(resp, self.url)
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password('NewPass#2027x'))
        # الجلسة الحالية تبقى صالحة
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertTrue(UserActivity.objects.filter(user=self.member, title='تغيير كلمة المرور').exists())

    def test_change_password_wrong_old_password(self):
        resp = self.client.post(self.url, {
            'action': 'password', 'old_password': 'wrong',
            'new_password1': 'NewPass#2027x', 'new_password2': 'NewPass#2027x'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'كلمة المرور الحالية غير صحيحة')
        self.assertEqual(resp.context['open_modal'], 'changePasswordModal')
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password('OldPass#2026'))

    def test_change_password_mismatch_and_weak(self):
        resp = self.client.post(self.url, {
            'action': 'password', 'old_password': 'OldPass#2026',
            'new_password1': '12345678', 'new_password2': '12345678'})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['password_form'].errors)
        resp = self.client.post(self.url, {
            'action': 'password', 'old_password': 'OldPass#2026',
            'new_password1': 'NewPass#2027x', 'new_password2': 'Other#2027x'})
        self.assertContains(resp, 'غير متطابقتين')

    def test_edit_profile(self):
        resp = self.client.post(self.url, {
            'action': 'profile', 'first_name': 'أحمد', 'last_name': 'علي',
            'email': 'new@example.com', 'phone': '0912345678'})
        self.assertRedirects(resp, self.url)
        self.member.refresh_from_db()
        self.assertEqual((self.member.first_name, self.member.email, self.member.phone),
                         ('أحمد', 'new@example.com', '0912345678'))
        self.assertEqual(self.member.username, 'member1')

    def test_edit_profile_ignores_privileged_fields(self):
        self.client.post(self.url, {
            'action': 'profile', 'first_name': 'x', 'email': '', 'username': 'hacker',
            'is_tenant_admin': 'on', 'is_superuser': 'on'})
        self.member.refresh_from_db()
        self.assertEqual(self.member.username, 'member1')
        self.assertFalse(self.member.is_tenant_admin)
        self.assertFalse(self.member.is_superuser)

    def test_edit_profile_rejects_email_used_by_another_user(self):
        User.objects.create_user(username='other', password='x12345678', email='Taken@example.com')
        resp = self.client.post(self.url, {'action': 'profile', 'email': 'taken@example.com'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'البريد الإلكتروني مستخدم بالفعل')
        self.assertEqual(resp.context['open_modal'], 'editProfileModal')


class LoginUsernameOrEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='ahmed', password='Pass#2026x', email='Ahmed@Example.com')
        self.url = reverse('accounts:login')

    def _login(self, username, password='Pass#2026x'):
        self.client.post(self.url, {'username': username, 'password': password})
        return self.client.session.get('_auth_user_id')

    def test_login_with_username(self):
        self.assertEqual(self._login('ahmed'), str(self.user.pk))

    def test_login_with_email_case_insensitive(self):
        self.assertEqual(self._login('ahmed@example.com'), str(self.user.pk))
        self.client.logout()
        self.assertEqual(self._login('  AHMED@example.com '), str(self.user.pk))

    def test_wrong_password_with_email(self):
        self.assertIsNone(self._login('ahmed@example.com', 'bad'))

    def test_inactive_user_cannot_login_with_email(self):
        self.user.is_active = False
        self.user.save()
        self.assertIsNone(self._login('ahmed@example.com'))

    def test_shared_email_logs_in_the_account_whose_password_matches(self):
        other = User.objects.create_user(username='ahmed2', password='Other#2026x', email='ahmed@example.com')
        self.assertEqual(self._login('ahmed@example.com', 'Other#2026x'), str(other.pk))

    def test_shared_email_with_same_password_is_rejected(self):
        User.objects.create_user(username='ahmed2', password='Pass#2026x', email='ahmed@example.com')
        self.assertIsNone(self._login('ahmed@example.com'))
        # اسم المستخدم يبقى يعمل
        self.assertEqual(self._login('ahmed'), str(self.user.pk))


class AdminAuditLogTests(TestCase):
    def setUp(self):
        self.su = User.objects.create_superuser('root-audit', 'root@example.com', 'Root#2026x')
        self.client.force_login(self.su)

    def test_audit_log_opens_and_ignores_bad_filters(self):
        url = reverse('core:admin_audit_log')
        self.assertEqual(self.client.get(url).status_code, 200)
        for params in ({'tenant': 'abc'}, {'tenant': '1 OR 1'}, {'action': 'zzz'},
                       {'date_from': 'bad', 'date_to': '2026-13-40'}):
            self.assertEqual(self.client.get(url, params).status_code, 200, params)

    def test_activity_report_opens(self):
        self.assertEqual(self.client.get(reverse('core:admin_report_activity')).status_code, 200)

    def test_superuser_profile_opens(self):
        self.assertEqual(self.client.get(reverse('accounts:profile')).status_code, 200)
