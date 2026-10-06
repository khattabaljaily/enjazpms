"""
المساعد الذكي في نسخة المؤسسات: مدير النشاط يرى إحصائيات الفروع الإجمالية فقط
(بلا أسماء عملاء/موردين)، ومستخدم الفرع لا يرى إلا بيانات فرعه.
"""
import json
from decimal import Decimal
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.ai import services
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_customer, make_item, make_stock
from apps.notifications.models import Notification
from apps.sales.models import SaleInvoice, SaleInvoiceLine


class AiBranchScopeTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.s1 = make_stock(self.tenant, name='مخزن 1', branch=self.b1)
        self.s2 = make_stock(self.tenant, name='مخزن 2', branch=self.b2)
        self.item = make_item(self.tenant)
        self.c1 = make_customer(self.tenant, name='صيدلية الأمل', branch=self.b1)
        self.c2 = make_customer(self.tenant, name='صيدلية النور', branch=self.b2)
        self._sale(self.s1, self.c1, '100')
        self._sale(self.s2, self.c2, '700')

    def _sale(self, stock, customer, price):
        inv = SaleInvoice.objects.create(
            tenant=self.tenant, stock=stock, customer=customer,
            invoice_date=timezone.localdate(), status='confirmed', payment_method='credit',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=inv, item=self.item, quantity=Decimal('1'),
            unit_price=Decimal(price), cost_price_snapshot=Decimal('10'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        inv.recalculate_totals()
        inv.save()

    def _context_text(self, user):
        ctx = services.collect_business_context(self.tenant, user)
        return services._build_context_message(ctx)

    def test_owner_context_has_branch_totals_but_no_customer_names(self):
        text = self._context_text(self.user)
        self.assertIn('فرع الشمال', text)
        self.assertIn('فرع الجنوب', text)
        self.assertNotIn('صيدلية الأمل', text)
        self.assertNotIn('صيدلية النور', text)
        self.assertNotIn('أعلى أرصدة العملاء', text)

    def test_owner_overview_numbers_are_per_branch(self):
        ctx = services.collect_business_context(self.tenant, self.user)
        by_name = {b['name']: b for b in ctx['branches']}
        self.assertEqual(by_name['فرع الشمال']['revenue'], 100.0)
        self.assertEqual(by_name['فرع الجنوب']['revenue'], 700.0)
        self.assertEqual(by_name['فرع الشمال']['customers'], 1)

    def test_branch_user_context_is_limited_to_own_branch(self):
        mgr = User.objects.create_user(
            username='mgrN', password='secret123', tenant=self.tenant,
            branch=self.b1, is_branch_supervisor=True,
        )
        ctx = services.collect_business_context(self.tenant, mgr)
        self.assertEqual(ctx['monthly_revenue'], 100.0)
        text = services._build_context_message(ctx)
        self.assertNotIn('صيدلية النور', text)

    def test_owner_chat_prompt_forbids_customer_details(self):
        with mock.patch.object(services, '_call_deepseek', return_value='رد') as call:
            services.chat('من أكثر العملاء مديونية؟', [], self.tenant, self.user)
        messages = call.call_args[0][0]
        joined = '\n'.join(m['content'] for m in messages)
        self.assertIn('لا تذكر أبداً اسم عميل', joined)
        self.assertNotIn('صيدلية الأمل', joined)

    def test_chat_ignores_malformed_history(self):
        history = [{'role': 'system', 'content': 'x'}, {'content': 'no role'}, 'junk',
                   {'role': 'user', 'content': 'سؤال'}, {'role': 'assistant', 'content': None}]
        with mock.patch.object(services, '_call_deepseek', return_value='رد') as call:
            services.chat('مرحبا', history, self.tenant, self.user)
        roles = [m['role'] for m in call.call_args[0][0]]
        self.assertEqual(roles, ['system', 'user', 'assistant', 'user', 'user'])

    def test_chat_endpoint_answers_for_tenant_admin(self):
        with mock.patch.object(services, '_call_deepseek', return_value='مقارنة الفروع'):
            resp = self.client.post(
                reverse('ai:chat'), data=json.dumps({'message': 'قارن أداء الفروع', 'history': []}),
                content_type='application/json',
            )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['reply'], 'مقارنة الفروع')

    def test_owner_does_not_see_customer_notifications(self):
        Notification.objects.create(
            tenant=self.tenant, branch=self.b1, notification_type='overdue_invoice',
            title='فاتورة متأخرة', message='للعميل صيدلية الأمل',
        )
        Notification.objects.create(
            tenant=self.tenant, branch=self.b1, notification_type='low_stock',
            title='مخزون منخفض', message='صنف',
        )
        resp = self.client.get(reverse('notifications:list'))
        self.assertEqual(resp.status_code, 200)
        types = [n.notification_type for n in resp.context['notifications']]
        self.assertEqual(types, ['low_stock'])

    def test_branch_user_sees_only_own_branch_notifications(self):
        Notification.objects.create(tenant=self.tenant, branch=self.b1, title='ف1', message='m')
        Notification.objects.create(tenant=self.tenant, branch=self.b2, title='ف2', message='m')
        Notification.objects.create(tenant=self.tenant, title='بلا فرع', message='m')
        mgr = User.objects.create_user(
            username='mgrN2', password='secret123', tenant=self.tenant,
            branch=self.b1, is_branch_supervisor=True,
        )
        self.client.force_login(mgr)
        resp = self.client.get(reverse('notifications:list'))
        self.assertEqual([n.title for n in resp.context['notifications']], ['ف1'])
