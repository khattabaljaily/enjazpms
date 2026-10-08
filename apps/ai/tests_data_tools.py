"""
أدوات استعلام المساعد الذكي: النطاق إجباري (مشترك + فرع)، والحقول الحساسة محجوبة،
وحلقة function calling تعمل من طرف لطرف.
"""
import json
from decimal import Decimal
from unittest import mock

from django.utils import timezone

from apps.accounts.models import User
from apps.ai import data_tools, services
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_customer, make_item, make_stock, make_supplier
from apps.sales.models import CustomerLedger, SaleInvoice, SaleInvoiceLine


class DataToolsTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.s1 = make_stock(self.tenant, name='مخزن 1', branch=self.b1)
        self.s2 = make_stock(self.tenant, name='مخزن 2', branch=self.b2)
        self.item = make_item(self.tenant)
        self.c1 = make_customer(self.tenant, name='عميل الأمل', branch=self.b1)
        self.c2 = make_customer(self.tenant, name='عميل النور', branch=self.b2)
        make_supplier(self.tenant, name='مورد الشمال', branch=self.b1)
        make_supplier(self.tenant, name='مورد الجنوب', branch=self.b2)
        for stock, cust, price in ((self.s1, self.c1, '100'), (self.s2, self.c2, '700')):
            self._sale(stock, cust, price)
            CustomerLedger.objects.create(
                tenant=self.tenant, customer=cust, entry_type='opening', amount=Decimal(price),
                entry_date=timezone.localdate())
        self.mgr1 = User.objects.create_user(
            username='dt-m1', password='x12345678', tenant=self.tenant, branch=self.b1,
            is_branch_supervisor=True)

    def _sale(self, stock, customer, price):
        inv = SaleInvoice.objects.create(
            tenant=self.tenant, stock=stock, customer=customer, invoice_date=timezone.localdate(),
            status='confirmed', payment_method='credit')
        line = SaleInvoiceLine(tenant=self.tenant, invoice=inv, item=self.item, quantity=Decimal('1'),
                               unit_price=Decimal(price), cost_price_snapshot=Decimal('10'),
                               tax_rate=Decimal('0'))
        line.calculate()
        line.save()
        inv.recalculate_totals()
        inv.save()

    def _q(self, user, **args):
        return json.loads(data_tools.run_tool('query_data', json.dumps(args), self.tenant, user))

    # ---- النطاق -------------------------------------------------------
    def test_owner_sees_all_branches(self):
        res = self._q(self.user, entity='customers', fields=['name', 'branch__name'])
        self.assertEqual({r['name'] for r in res['rows']}, {'عميل الأمل', 'عميل النور'})

    def test_branch_user_sees_only_own_branch(self):
        res = self._q(self.mgr1, entity='customers', fields=['name'])
        self.assertEqual([r['name'] for r in res['rows']], ['عميل الأمل'])
        self.assertEqual(res['total_count'], 1)

    def test_branch_user_cannot_escape_scope_with_filters(self):
        res = self._q(self.mgr1, entity='customers', fields=['name'],
                      filters=[{'field': 'branch__name', 'op': 'eq', 'value': 'فرع الجنوب'}])
        self.assertEqual(res['rows'], [])
        res = self._q(self.mgr1, entity='customers', fields=['name'],
                      filters=[{'field': 'name', 'op': 'contains', 'value': 'النور'}])
        self.assertEqual(res['rows'], [])

    def test_branch_user_aggregates_are_scoped(self):
        res = self._q(self.mgr1, entity='sale_invoices',
                      aggregate=[{'func': 'sum', 'field': 'grand_total', 'as': 'total'}])
        self.assertEqual(res['result']['total'], 100.0)
        res = self._q(self.user, entity='sale_invoices',
                      aggregate=[{'func': 'sum', 'field': 'grand_total', 'as': 'total'}])
        self.assertEqual(res['result']['total'], 800.0)

    def test_branch_user_ledger_and_lines_are_scoped(self):
        res = self._q(self.mgr1, entity='customer_ledger', fields=['customer__name', 'amount'])
        self.assertEqual([r['customer__name'] for r in res['rows']], ['عميل الأمل'])
        res = self._q(self.mgr1, entity='sale_invoice_lines', fields=['invoice__customer__name'])
        self.assertEqual([r['invoice__customer__name'] for r in res['rows']], ['عميل الأمل'])

    def test_branches_entity_for_branch_user_is_only_own(self):
        res = self._q(self.mgr1, entity='branches', fields=['name'])
        self.assertEqual([r['name'] for r in res['rows']], ['فرع الشمال'])

    def test_other_tenant_is_never_visible(self):
        from django.conf import settings
        from apps.core.models import BusinessType, Tenant
        bt = BusinessType.objects.create(name='o', name_ar='o', slug='o-bt')
        t2 = Tenant.objects.create(
            name='T2', business_type=bt, subscription_plan='enterprise', version_type='multi_branch',
            max_stocks=10, max_branches=10, currency='SDG', terms_version=settings.TERMS_VERSION,
            terms_accepted_at=timezone.now())
        make_customer(t2, name='عميل مشترك آخر')
        res = self._q(self.user, entity='customers', fields=['name'])
        self.assertNotIn('عميل مشترك آخر', {r['name'] for r in res['rows']})

    # ---- الأمان ---------------------------------------------------------
    def test_sensitive_and_blocked_fields_are_rejected(self):
        for bad in ('password', 'created_by__password', 'tenant__name', 'tenant'):
            res = self._q(self.user, entity='customers', fields=[bad])
            self.assertIn('error', res, bad)
        res = self._q(self.user, entity='users', fields=['username', 'password'])
        self.assertIn('error', res)
        res = self._q(self.user, entity='users', fields=['username', 'branch__name'])
        self.assertNotIn('error', res)

    def test_unknown_entity_and_bad_operator_return_errors(self):
        self.assertIn('error', self._q(self.user, entity='tenants'))
        self.assertIn('error', self._q(self.user, entity='customers',
                                       filters=[{'field': 'name', 'op': 'raw', 'value': 'x'}]))
        self.assertIn('error', self._q(self.user, entity='customers', fields=['nope']))

    def test_every_entity_is_queryable_for_owner_and_branch_user(self):
        for key in data_tools.available_entities():
            for user in (self.user, self.mgr1):
                res = self._q(user, entity=key, limit=1)
                self.assertNotIn('error', res, f'{key}: {res}')
            desc = json.loads(data_tools.run_tool('describe_entity', json.dumps({'entity': key}),
                                                  self.tenant, self.user))
            self.assertNotIn('error', desc, key)

    def test_group_by_month_and_branch(self):
        res = self._q(self.user, entity='sale_invoices', group_by=['stock__branch__name', 'invoice_date:month'],
                      aggregate=[{'func': 'sum', 'field': 'grand_total', 'as': 'total'}])
        self.assertNotIn('error', res, res)
        self.assertEqual(sorted(r['total'] for r in res['rows']), [100.0, 700.0])

    def test_list_entities(self):
        res = json.loads(data_tools.run_tool('list_entities', '', self.tenant, self.user))
        keys = {e['entity'] for e in res['entities']}
        self.assertTrue({'customers', 'sale_invoices', 'customer_ledger', 'treasuries'} <= keys)

    # ---- حلقة المحادثة -------------------------------------------------
    def test_chat_runs_tool_loop_end_to_end(self):
        responses = [
            {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 'c1', 'type': 'function', 'function': {
                    'name': 'query_data', 'arguments': json.dumps({
                        'entity': 'customers', 'fields': ['name', 'branch__name']})}}]}}]},
            {'choices': [{'message': {'role': 'assistant', 'content': 'العملاء: عميل الأمل وعميل النور'}}]},
        ]
        sent = []

        def fake_post(url, headers=None, json=None, timeout=None):
            sent.append(json)
            resp = mock.Mock(status_code=200)
            resp.json.return_value = responses[len(sent) - 1]
            resp.raise_for_status.return_value = None
            return resp

        with mock.patch.object(services.requests, 'post', side_effect=fake_post), \
                mock.patch.object(services.settings, 'DEEPSEEK_API_KEY', 'k'):
            reply = services.chat('اذكر كل العملاء', [], self.tenant, self.user)
        self.assertEqual(reply, 'العملاء: عميل الأمل وعميل النور')
        tool_msgs = [m for m in sent[1]['messages'] if m['role'] == 'tool']
        self.assertEqual(len(tool_msgs), 1)
        self.assertIn('عميل الأمل', tool_msgs[0]['content'])
        self.assertIn('عميل النور', tool_msgs[0]['content'])
        self.assertIn('tools', sent[0])

    def test_branch_user_chat_tool_results_are_scoped(self):
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            msgs = json['messages']
            resp = mock.Mock(status_code=200)
            resp.raise_for_status.return_value = None
            if not any(m['role'] == 'tool' for m in msgs):
                resp.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'c1', 'type': 'function', 'function': {
                        'name': 'query_data', 'arguments': '{"entity":"customers","fields":["name"]}'}}]}}]}
            else:
                captured['tool'] = [m for m in msgs if m['role'] == 'tool'][0]['content']
                resp.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': 'تم'}}]}
            return resp

        with mock.patch.object(services.requests, 'post', side_effect=fake_post), \
                mock.patch.object(services.settings, 'DEEPSEEK_API_KEY', 'k'):
            services.chat('كل العملاء', [], self.tenant, self.mgr1)
        self.assertIn('عميل الأمل', captured['tool'])
        self.assertNotIn('عميل النور', captured['tool'])
