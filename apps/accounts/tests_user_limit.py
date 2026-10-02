"""حد المستخدمين في الباقة يحسب النشطين فقط، ويُفحص عند التفعيل."""
from apps.accounts.models import User
from apps.core.test_utils import TenantTestCase


class ActiveUserLimitTests(TenantTestCase):
    def _fill_to_limit(self):
        for i in range(User.objects.filter(tenant=self.tenant, is_active=True).count(), self.tenant.max_users):
            User.objects.create_user(username=f'u{i}', password='s3cret123', tenant=self.tenant)

    def _post(self, url, username, active):
        data = {'username': username, 'first_name': 'م', 'last_name': 'س',
                'password': 'Pass#12345', 'password_confirm': 'Pass#12345'}
        if active:
            data['is_active'] = 'on'
        return self.client.post(url, data)

    def test_inactive_users_do_not_count_and_activation_is_checked(self):
        self._fill_to_limit()
        resp = self._post('/accounts/users/api/create/', 'extra-active', active=True)
        self.assertEqual(resp.status_code, 403)
        resp = self._post('/accounts/users/api/create/', 'extra-inactive', active=False)
        self.assertTrue(resp.json().get('success'), resp.content[:300])
        extra = User.objects.get(tenant=self.tenant, username='extra-inactive')
        self.assertFalse(extra.is_active)
        resp = self._post(f'/accounts/users/api/{extra.pk}/update/', 'extra-inactive', active=True)
        self.assertEqual(resp.status_code, 403)
        User.objects.filter(tenant=self.tenant, username='u1').update(is_active=False)
        resp = self._post(f'/accounts/users/api/{extra.pk}/update/', 'extra-inactive', active=True)
        self.assertTrue(resp.json().get('success'), resp.content[:300])
