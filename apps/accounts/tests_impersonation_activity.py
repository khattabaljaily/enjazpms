"""وضع انتحال الهوية: نشاط المشرف وهو يتصفح كصاحب نشاط لا يُسجَّل في سجل النشاط."""
from django.test import RequestFactory

from apps.accounts.activity_service import log_activity
from apps.accounts.models import User, UserActivity
from apps.core.test_utils import TenantTestCase


class ImpersonationActivityTests(TenantTestCase):
    def _request(self, impersonating):
        user = User.objects.create_user(username=f'u{impersonating}', password='x12345678', tenant=self.tenant)
        request = RequestFactory().post('/')
        request.user = user
        request.tenant = self.tenant
        request.session = {'_impersonator_id': 1} if impersonating else {}
        return request

    def test_normal_activity_is_logged(self):
        log_activity(self._request(False), 'عمل عادي')
        self.assertTrue(UserActivity.objects.filter(tenant=self.tenant, title='عمل عادي').exists())

    def test_impersonated_activity_is_not_logged(self):
        request = self._request(True)
        log_activity(request, 'عمل منتحل')
        self.assertFalse(UserActivity.objects.filter(tenant=self.tenant, title='عمل منتحل').exists())
        self.assertTrue(request._activity_logged)
