"""
نسخة المؤسسات: مدير الفرع لا يتعامل إلا مع خزائن/حسابات فرعه في العمليات
اليومية، ولا تذهب الحركات النقدية بلا خزينة محددة إلى خزينة الإدارة المركزية
أو خزينة فرع آخر.
"""
import json
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch, BusinessType, Tenant
from apps.customers.models import Customer
from apps.treasury.models import Treasury
from apps.treasury.services import get_or_create_default_treasury, post_treasury_receipt


class BranchMoneyScopeTests(TestCase):
    def setUp(self):
        bt = BusinessType.objects.create(name='bt-money', name_ar='نوع', slug='bt-money')
        terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.tenant = Tenant.objects.create(
            name='Ent Money', business_type=bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **terms,
        )
        self.tenant.save()  # يضمن خزينة الإدارة المركزية
        self.branch_a = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.branch_b = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        self.hq = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=False)
        self.ta = Treasury.objects.get(tenant=self.tenant, branch=self.branch_a)
        self.tb = Treasury.objects.get(tenant=self.tenant, branch=self.branch_b)
        self.mgr_a = User.objects.create_user(username='money-a', password='s3cret123', tenant=self.tenant)
        Branch.assign_manager(self.branch_a, self.mgr_a)
        self.mgr_a.refresh_from_db()

    def test_default_treasury_for_branch_user_is_branch_treasury(self):
        self.assertEqual(get_or_create_default_treasury(self.tenant, user=self.mgr_a), self.ta)

    def test_cash_receipt_without_treasury_lands_in_branch_treasury(self):
        post_treasury_receipt(self.tenant, Decimal('500'), timezone.localdate(), user=self.mgr_a)
        self.ta.refresh_from_db(); self.hq.refresh_from_db(); self.tb.refresh_from_db()
        self.assertEqual(self.ta.current_balance, Decimal('500'))
        self.assertEqual(self.hq.current_balance, Decimal('0'))
        self.assertEqual(self.tb.current_balance, Decimal('0'))

    def _pay(self, treasury):
        customer = Customer.objects.create(tenant=self.tenant, name='عميل', branch=self.branch_a)
        return self.client.post('/customers/payments/create/', data=json.dumps({
            'customer_id': customer.id, 'amount': '100', 'method': 'cash', 'treasury_id': treasury.id,
        }), content_type='application/json')

    def test_branch_user_cannot_use_head_office_or_other_branch_treasury(self):
        self.client.force_login(self.mgr_a)
        for foreign in (self.hq, self.tb):
            resp = self._pay(foreign)
            self.assertIn(resp.status_code, (403, 404), foreign.name)
            foreign.refresh_from_db()
            self.assertEqual(foreign.current_balance, Decimal('0'))

    def test_branch_user_can_use_own_treasury(self):
        self.client.force_login(self.mgr_a)
        resp = self._pay(self.ta)
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.ta.refresh_from_db()
        self.assertEqual(self.ta.current_balance, Decimal('100'))

    def test_payment_screen_lists_only_branch_treasuries(self):
        self.client.force_login(self.mgr_a)
        resp = self.client.get('/customers/payments/')
        self.assertEqual(list(resp.context['treasuries']), [self.ta])
