"""
عزل الفروع: عملاء/موردون/مناديب/موظفون/مصروفات كل فرع خاصة به — لا يراها فرع
آخر، ولا تظهر سجلات "بلا فرع" لأي فرع. الفلترة تلقائية على مستوى الـ Manager
(BranchScopedManager) فتشمل القوائم المنسدلة في الفواتير والبحث بالمعرّف.
"""
from django.urls import reverse

from apps.accounts.models import User
from apps.agents.models import Agent
from apps.core.models import Branch, current_branch_id
from apps.core.test_utils import (
    TenantTestCase, make_agent, make_customer, make_employee, make_supplier,
)
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.suppliers.models import Supplier


class BranchIsolationTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع 1')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع 2')
        self.c1 = make_customer(self.tenant, name='عميل ف1', branch=self.b1)
        self.c2 = make_customer(self.tenant, name='عميل ف2', branch=self.b2)
        self.c0 = make_customer(self.tenant, name='عميل بلا فرع')
        self.s1 = make_supplier(self.tenant, name='مورد ف1', branch=self.b1)
        self.s2 = make_supplier(self.tenant, name='مورد ف2', branch=self.b2)
        self.s0 = make_supplier(self.tenant, name='مورد بلا فرع')
        self.a1 = make_agent(self.tenant, name='مندوب ف1', branch=self.b1)
        self.a2 = make_agent(self.tenant, name='مندوب ف2', branch=self.b2)
        self.a0 = make_agent(self.tenant, name='مندوب بلا فرع')
        self.e1 = make_employee(self.tenant, name='موظف ف1', branch=self.b1)
        self.e2 = make_employee(self.tenant, name='موظف ف2', branch=self.b2)
        self.e0 = make_employee(self.tenant, name='موظف بلا فرع')

    def _as_branch(self, branch):
        token = current_branch_id.set(branch.id if branch else None)
        self.addCleanup(current_branch_id.reset, token)

    def test_central_scope_sees_everything(self):
        self.assertEqual(Customer.objects.filter(tenant=self.tenant).count(), 3)
        self.assertEqual(Supplier.objects.filter(tenant=self.tenant).count(), 3)

    def test_branch_scope_hides_other_branches_and_unbranched(self):
        self._as_branch(self.b1)
        self.assertEqual(list(Customer.objects.filter(tenant=self.tenant)), [self.c1])
        self.assertEqual(list(Supplier.objects.for_tenant(self.tenant)), [self.s1])
        self.assertEqual(list(Agent.objects.filter(tenant=self.tenant)), [self.a1])
        self.assertEqual(list(Employee.objects.filter(tenant=self.tenant)), [self.e1])

    def test_lookup_by_id_of_other_branch_record_fails(self):
        self._as_branch(self.b1)
        with self.assertRaises(Customer.DoesNotExist):
            Customer.objects.get(pk=self.c2.pk, tenant=self.tenant)
        with self.assertRaises(Supplier.DoesNotExist):
            Supplier.objects.get(pk=self.s0.pk, tenant=self.tenant)

    def test_for_branch_is_strict_for_partners(self):
        self.assertEqual(
            list(Customer.objects.for_tenant(self.tenant).for_branch(self.b2)), [self.c2],
        )

    def test_code_generation_is_not_affected_by_scope(self):
        self._as_branch(self.b1)
        c = make_customer(self.tenant, name='عميل جديد', branch=self.b1)
        codes = list(Customer.unscoped.filter(tenant=self.tenant).values_list('code', flat=True))
        self.assertEqual(len(codes), len(set(codes)))
        self.assertIn(c.code, codes)

    def test_branch_user_screens_show_only_own_branch(self):
        user = User.objects.create_user(
            username='b1sup', password='secret123', tenant=self.tenant,
            branch=self.b1, is_branch_supervisor=True,
        )
        self.client.force_login(user)

        resp = self.client.get(reverse('customers:table_api'), {'draw': 1, 'start': 0, 'length': 50})
        names = [row['name'] for row in resp.json()['data']]
        self.assertEqual(names, ['عميل ف1'])

        # قائمة العملاء المنسدلة في شاشة فواتير المبيعات
        resp = self.client.get('/sales/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([c['name'] for c in resp.context['customers']], ['عميل ف1'])

    def test_branch_user_cannot_open_other_branch_customer(self):
        user = User.objects.create_user(
            username='b1sup2', password='secret123', tenant=self.tenant,
            branch=self.b1, is_branch_supervisor=True,
        )
        self.client.force_login(user)
        resp = self.client.get(reverse('customers:detail_api', args=[self.c2.pk]))
        self.assertEqual(resp.status_code, 404)
