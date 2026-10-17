"""طلبات المناديب من البوابة تظهر في إشعارات من يتولّاها (مالك الحساب، أو مستخدمو فرع المندوب في نسخة المؤسسات)."""
import json

from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Branch, TenantCapabilities
from apps.core.test_utils import TenantTestCase, make_agent, make_customer, make_item


class _Base(TenantTestCase):
    subscription_plan = 'pro'

    def setUp(self):
        super().setUp()
        TenantCapabilities.objects.update_or_create(tenant=self.tenant, defaults={'has_agents_module': True})
        self.customer = make_customer(self.tenant, name='عميل الطلب')
        self.item = make_item(self.tenant, name='صنف الطلب')

    def submit(self, agent):
        from apps.agents import views
        portal = Client()
        session = portal.session
        session[views._SESS_AGENT] = agent.pk
        session[views._SESS_TENANT] = self.tenant.pk
        session.save()
        resp = portal.post(reverse('agents:portal_request_new'), json.dumps({
            'customer_id': self.customer.pk, 'lines': [{'item_id': self.item.pk, 'quantity': '2'}]}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)

    def bell(self, user):
        self.client.force_login(user)
        return [i['title'] for i in self.client.get(reverse('notifications:api')).json()['items']]


class BasicPlanRequestNotificationTests(_Base):
    def test_owner_sees_agent_request(self):
        self.submit(make_agent(self.tenant, name='مندوب أ'))
        self.assertIn('طلب فاتورة جديد من مندوب أ', self.bell(self.user))


class EnterpriseRequestNotificationTests(_Base):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.other = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        mk = lambda n, b: User.objects.create_user(username=n, password='x12345678', tenant=self.tenant,
                                                   branch=b, is_branch_supervisor=True)
        self.mgr = mk('rn-mgr', self.branch)
        self.other_mgr = mk('rn-other', self.other)
        self.agent = make_agent(self.tenant, name='مندوب الفرع', branch=self.branch)

    def test_branch_manager_of_agent_branch_sees_request(self):
        self.submit(self.agent)
        self.assertIn('طلب فاتورة جديد من مندوب الفرع', self.bell(self.mgr))

    def test_other_branch_does_not_see_it(self):
        self.submit(self.agent)
        self.assertNotIn('طلب فاتورة جديد من مندوب الفرع', self.bell(self.other_mgr))
