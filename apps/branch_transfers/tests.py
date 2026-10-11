"""تحويلات الفروع: إرسال ثم تأكيد استلام، نقداً/بنكياً، تطابق العملة، العزل، وظهور كل الحركات في كشوف الحسابات."""
import json
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.bank_accounts.models import BankAccount, BankAccountMovement
from apps.bank_accounts.reports import BankAccountReportGenerator
from apps.branch_transfers.models import BranchTransfer
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase
from apps.notifications.models import Notification
from apps.treasury.models import Treasury, TreasuryMovement
from apps.treasury.reports import TreasuryReportGenerator

D = Decimal


class BranchTransferBase(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.save()
        self.a = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.b = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        self.sup_a = User.objects.create_user(username='bt-a', password='x12345678', tenant=self.tenant,
                                              branch=self.a, is_branch_supervisor=True)
        self.sup_b = User.objects.create_user(username='bt-b', password='x12345678', tenant=self.tenant,
                                              branch=self.b, is_branch_supervisor=True)
        self.ho_cash = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=False)
        self.ho_cash_usd = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=True)
        self.a_cash = Treasury.objects.get(tenant=self.tenant, branch=self.a, is_hard_currency=False)
        self.a_cash_usd = Treasury.objects.get(tenant=self.tenant, branch=self.a, is_hard_currency=True)
        self.b_cash = Treasury.objects.get(tenant=self.tenant, branch=self.b, is_hard_currency=False)
        for t in (self.ho_cash, self.ho_cash_usd, self.a_cash, self.a_cash_usd, self.b_cash):
            Treasury.objects.filter(pk=t.pk).update(current_balance=D('1000'))
        mk = lambda **kw: BankAccount.objects.create(tenant=self.tenant, current_balance=D('1000'), **kw)
        self.ho_bank = mk(name='بنك الإدارة', is_head_office=True)
        self.ho_bank_usd = mk(name='بنك الإدارة دولار', is_head_office=True, currency='USD')
        self.a_bank = mk(name='بنك فرع أ', branch=self.a)
        self.a_bank_usd = mk(name='بنك فرع أ دولار', branch=self.a, currency='USD')
        self.b_bank = mk(name='بنك فرع ب', branch=self.b)

    # ── أدوات ──
    def bal(self, acc):
        acc.refresh_from_db()
        return acc.current_balance

    def send(self, user, src_kind, src, dst_kind, dst, amount='100', branch=None, **extra):
        self.client.force_login(user)
        body = {'branch_id': (branch or self.a).pk, 'source_kind': src_kind, 'source_id': src.pk,
                'dest_kind': dst_kind, 'dest_id': dst.pk, 'amount': amount,
                'transfer_date': timezone.localdate().isoformat(), 'notes': 'اختبار', **extra}
        return self.client.post(reverse('branch_transfers:create'), json.dumps(body), content_type='application/json')

    def receive(self, user, transfer, amount=None):
        self.client.force_login(user)
        body = {} if amount is None else {'received_amount': str(amount)}
        return self.client.post(reverse('branch_transfers:receive', args=[transfer.pk]), json.dumps(body),
                                content_type='application/json')

    def cancel(self, user, transfer):
        self.client.force_login(user)
        return self.client.post(reverse('branch_transfers:cancel', args=[transfer.pk]), '{}',
                                content_type='application/json')

    def ho_to_a_cash(self, amount='100'):
        resp = self.send(self.user, 'cash', self.ho_cash, 'cash', self.a_cash, amount)
        self.assertEqual(resp.status_code, 200, resp.content)
        return BranchTransfer.objects.get(pk=resp.json()['id'])

    def a_to_ho_cash(self, amount='100'):
        resp = self.send(self.sup_a, 'cash', self.a_cash, 'cash', self.ho_cash, amount)
        self.assertEqual(resp.status_code, 200, resp.content)
        return BranchTransfer.objects.get(pk=resp.json()['id'])


class SendAndReceiveTests(BranchTransferBase):
    def test_head_office_sends_to_branch_then_branch_confirms(self):
        t = self.ho_to_a_cash('100')
        self.assertEqual((t.status, t.direction, t.currency, t.number), ('in_transit', 'to_branch', 'SDG', 'BT-00001'))
        self.assertEqual((self.bal(self.ho_cash), self.bal(self.a_cash)), (900, 1000))  # خرج من المصدر فقط
        note = Notification.objects.get(tenant=self.tenant)
        self.assertEqual(note.branch_id, self.a.pk)
        resp = self.receive(self.sup_a, t)
        self.assertTrue(resp.json()['success'], resp.content)
        t.refresh_from_db()
        self.assertEqual((t.status, t.received_amount, t.has_difference, t.received_by_id),
                         ('received', D('100'), False, self.sup_a.pk))
        self.assertEqual((self.bal(self.ho_cash), self.bal(self.a_cash)), (900, 1100))

    def test_branch_sends_to_head_office_then_head_office_confirms(self):
        t = self.a_to_ho_cash('250')
        self.assertEqual(t.direction, 'to_head_office')
        self.assertEqual((self.bal(self.a_cash), self.bal(self.ho_cash)), (750, 1000))
        self.assertIsNone(Notification.objects.get(tenant=self.tenant).branch_id)  # إشعار للإدارة
        self.assertTrue(self.receive(self.user, t).json()['success'])
        self.assertEqual((self.bal(self.a_cash), self.bal(self.ho_cash)), (750, 1250))

    def test_cash_to_cash_and_bank_to_bank_in_both_directions(self):
        combos = [
            (self.user, 'cash', self.ho_cash, self.a_cash),
            (self.user, 'bank', self.ho_bank, self.a_bank),
            (self.sup_a, 'cash', self.a_cash, self.ho_cash),
            (self.sup_a, 'bank', self.a_bank, self.ho_bank),
        ]
        for user, kind, src, dst in combos:
            before_s, before_d = self.bal(src), self.bal(dst)
            resp = self.send(user, kind, src, kind, dst, '10')
            self.assertEqual(resp.status_code, 200, (kind, resp.content))
            t = BranchTransfer.objects.get(pk=resp.json()['id'])
            self.assertEqual((t.source_kind, t.destination_kind), (kind, kind))
            self.assertEqual((self.bal(src), self.bal(dst)), (before_s - 10, before_d))
            receiver = self.sup_a if user == self.user else self.user
            self.assertTrue(self.receive(receiver, t).json()['success'])
            self.assertEqual(self.bal(dst), before_d + 10)

    def test_cash_to_bank_and_bank_to_cash_are_refused(self):
        for user, sk, src, dk, dst in (
            (self.user, 'cash', self.ho_cash, 'bank', self.a_bank),
            (self.user, 'bank', self.ho_bank, 'cash', self.a_cash),
            (self.sup_a, 'bank', self.a_bank, 'cash', self.ho_cash),
            (self.sup_a, 'cash', self.a_cash, 'bank', self.ho_bank),
        ):
            resp = self.send(user, sk, src, dk, dst, '10')
            self.assertEqual(resp.status_code, 400, (sk, dk))
            self.assertIn('خزينة إلى خزينة', resp.json()['message'])
        self.assertEqual(BranchTransfer.objects.count(), 0)
        for acc in (self.ho_cash, self.ho_bank, self.a_cash, self.a_bank):
            self.assertEqual(self.bal(acc), 1000)

    def test_hard_currency_pairs_work_when_currencies_match(self):
        resp = self.send(self.user, 'cash', self.ho_cash_usd, 'cash', self.a_cash_usd, '40')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(BranchTransfer.objects.get().currency, 'USD')
        resp = self.send(self.user, 'bank', self.ho_bank_usd, 'bank', self.a_bank_usd, '40')
        self.assertEqual(resp.status_code, 200, resp.content)


class CurrencyAndRuleValidationTests(BranchTransferBase):
    def assert_rejected(self, resp, text=None):
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(resp.json()['success'])
        if text:
            self.assertIn(text, resp.json()['message'])
        self.assertEqual(BranchTransfer.objects.count(), 0)

    def test_currency_must_match_in_every_kind_combination(self):
        for sk, src, dk, dst in (
            ('cash', self.ho_cash, 'cash', self.a_cash_usd),          # جنيه → دولار
            ('cash', self.ho_cash_usd, 'cash', self.a_cash),          # دولار → جنيه
            ('bank', self.ho_bank, 'bank', self.a_bank_usd),
            ('bank', self.ho_bank_usd, 'bank', self.a_bank),
        ):
            self.assert_rejected(self.send(self.user, sk, src, dk, dst), 'تتطابق')
        self.assert_rejected(self.send(self.sup_a, 'bank', self.a_bank, 'bank', self.ho_bank_usd), 'تتطابق')
        self.assertEqual((self.bal(self.ho_cash), self.bal(self.a_cash_usd)), (1000, 1000))

    def test_sides_must_be_head_office_and_the_chosen_branch(self):
        # الإدارة: المصدر يجب أن يكون للإدارة، والوجهة لحسابات الفرع المختار.
        self.assert_rejected(self.send(self.user, 'cash', self.a_cash, 'cash', self.ho_cash), 'الإدارة')
        self.assert_rejected(self.send(self.user, 'cash', self.ho_cash, 'cash', self.b_cash, branch=self.a), 'فرع')
        self.assert_rejected(self.send(self.user, 'cash', self.ho_cash, 'cash', self.ho_cash_usd), None)
        # الفرع: لا يرسل من حساب فرع آخر ولا إلى فرع آخر.
        self.assert_rejected(self.send(self.sup_a, 'cash', self.b_cash, 'cash', self.ho_cash), 'فرع')
        self.assert_rejected(self.send(self.sup_a, 'cash', self.a_cash, 'cash', self.b_cash), 'الإدارة')

    def test_branch_user_is_always_pinned_to_own_branch(self):
        # يحاول فرع أ الإرسال باسم فرع ب: يُتجاهل branch_id ويُستعمل فرعه، فيفشل لأن المصدر من فرع ب.
        self.assert_rejected(self.send(self.sup_a, 'cash', self.b_cash, 'cash', self.ho_cash, branch=self.b), 'فرع')

    def test_amount_validation_and_insufficient_balance(self):
        for amount in ('0', '-5', 'abc', ''):
            self.assertEqual(self.send(self.user, 'cash', self.ho_cash, 'cash', self.a_cash, amount).status_code, 400, amount)
        resp = self.send(self.user, 'cash', self.ho_cash, 'cash', self.a_cash, '5000')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('غير كافٍ', resp.json()['message'])
        self.assertEqual(BranchTransfer.objects.count(), 0)
        self.assertEqual(self.bal(self.ho_cash), 1000)
        resp = self.send(self.user, 'bank', self.ho_bank, 'bank', self.a_bank, '5000')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.bal(self.ho_bank), 1000)

    def test_inactive_or_unknown_accounts_rejected(self):
        Treasury.objects.filter(pk=self.a_cash.pk).update(is_active=False)
        self.assert_rejected(self.send(self.user, 'cash', self.ho_cash, 'cash', self.a_cash), 'غير نشط')
        resp = self.send(self.user, 'cash', self.ho_cash, 'cash', self.ho_cash)
        resp2 = self.client.post(reverse('branch_transfers:create'), json.dumps({
            'branch_id': self.a.pk, 'source_kind': 'cash', 'source_id': 999999, 'dest_kind': 'cash',
            'dest_id': self.a_cash_usd.pk, 'amount': '1'}), content_type='application/json')
        self.assertEqual(resp2.status_code, 400)

    def test_no_exchange_rate_field_is_honoured(self):
        resp = self.send(self.user, 'cash', self.ho_cash, 'cash', self.a_cash, '100', exchange_rate='5000', to_amount='5')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.receive(self.sup_a, BranchTransfer.objects.get()).json()['success'])
        self.assertEqual(self.bal(self.a_cash), 1100)  # المبلغ نفسه بلا صرف


class ReceiptDifferenceAndCancelTests(BranchTransferBase):
    def test_partial_receipt_returns_the_difference_and_flags_it(self):
        t = self.ho_to_a_cash('100')
        self.assertTrue(self.receive(self.sup_a, t, '70').json()['success'])
        t.refresh_from_db()
        self.assertEqual((t.status, t.received_amount, t.has_difference, t.difference), ('received', D('70'), True, D('30')))
        self.assertEqual((self.bal(self.ho_cash), self.bal(self.a_cash)), (930, 1070))  # 30 عادت للمصدر
        self.assertTrue(TreasuryMovement.objects.filter(reference_type='branch_transfer_return', reference_id=t.pk,
                                                        treasury=self.ho_cash, amount=30).exists())

    def test_zero_receipt_is_a_full_rejection(self):
        t = self.ho_to_a_cash('100')
        self.assertTrue(self.receive(self.sup_a, t, '0').json()['success'])
        self.assertEqual((self.bal(self.ho_cash), self.bal(self.a_cash)), (1000, 1000))
        t.refresh_from_db()
        self.assertTrue(t.has_difference)

    def test_invalid_received_amounts(self):
        t = self.ho_to_a_cash('100')
        for bad in ('101', '-1', 'x'):
            self.assertEqual(self.receive(self.sup_a, t, bad).status_code, 400, bad)
        t.refresh_from_db()
        self.assertEqual(t.status, 'in_transit')

    def test_only_the_receiving_side_confirms(self):
        t = self.ho_to_a_cash('100')
        self.assertEqual(self.receive(self.user, t).status_code, 403)       # المُرسِل لا يؤكد استلام نفسه
        self.assertEqual(self.receive(self.sup_b, t).status_code, 404)      # فرع آخر لا يراه أصلاً
        t2 = self.a_to_ho_cash('50')
        self.assertEqual(self.receive(self.sup_a, t2).status_code, 403)
        t.refresh_from_db(); t2.refresh_from_db()
        self.assertEqual((t.status, t2.status), ('in_transit', 'in_transit'))

    def test_sender_cancels_before_receipt_and_amount_returns(self):
        t = self.ho_to_a_cash('100')
        resp = self.cancel(self.user, t)
        self.assertTrue(resp.json()['success'], resp.content)
        t.refresh_from_db()
        self.assertEqual((t.status, t.cancelled_by_id), ('cancelled', self.user.pk))
        self.assertEqual((self.bal(self.ho_cash), self.bal(self.a_cash)), (1000, 1000))
        self.assertEqual(self.receive(self.sup_a, t).status_code, 400)       # لا استلام بعد الإلغاء

    def test_branch_sender_cancels_but_receiver_cannot(self):
        t = self.a_to_ho_cash('100')
        self.assertEqual(self.cancel(self.user, t).status_code, 403)         # المستلِم لا يلغي
        self.assertTrue(self.cancel(self.sup_a, t).json()['success'])
        self.assertEqual(self.bal(self.a_cash), 1000)

    def test_cannot_cancel_after_receipt_or_twice(self):
        t = self.ho_to_a_cash('100')
        self.assertTrue(self.receive(self.sup_a, t).json()['success'])
        self.assertEqual(self.cancel(self.user, t).status_code, 400)
        t2 = self.ho_to_a_cash('10')
        self.assertTrue(self.cancel(self.user, t2).json()['success'])
        self.assertEqual(self.cancel(self.user, t2).status_code, 400)

    def test_no_double_receipt(self):
        t = self.ho_to_a_cash('100')
        self.assertTrue(self.receive(self.sup_a, t).json()['success'])
        self.assertEqual(self.receive(self.sup_a, t).status_code, 400)
        self.assertEqual(self.bal(self.a_cash), 1100)


class StatementsTests(BranchTransferBase):
    """كل حركة (إرسال، استلام، فرق، إلغاء) تظهر في كشف حساب الخزينة/الحساب البنكي بوصفها ورصيدها الجاري."""

    def treasury_rows(self, treasury):
        report = TreasuryReportGenerator(self.tenant).get_statement_report(treasury.pk)
        return report['data']

    def bank_rows(self, account):
        return BankAccountReportGenerator(self.tenant).get_statement_report(account.pk)['data']

    def refs(self, rows):
        return [r['reference_type'] for r in rows]

    def test_send_receive_and_difference_appear_in_both_treasury_statements(self):
        t = self.ho_to_a_cash('100')
        self.receive(self.sup_a, t, '70')
        src = self.treasury_rows(self.ho_cash)
        dst = self.treasury_rows(self.a_cash)
        self.assertEqual(self.refs(src), ['إعادة فرق تحويل فروع', 'تحويل فروع'])           # الأحدث أولاً
        self.assertEqual(self.refs(dst), ['تحويل فروع'])
        self.assertIn(t.number, src[1]['description'])
        self.assertEqual((src[1]['disbursement'], src[0]['receipt']), ('100.00', '30.00'))
        self.assertEqual(dst[0]['receipt'], '70.00')
        self.assertEqual(src[0]['running_balance'], '930.00')
        self.assertEqual(self.bal(self.ho_cash), D('930'))

    def test_cancel_appears_as_reversal_in_the_source_statement(self):
        t = self.ho_to_a_cash('100')
        self.cancel(self.user, t)
        rows = self.treasury_rows(self.ho_cash)
        self.assertEqual(self.refs(rows), ['إلغاء تحويل فروع', 'تحويل فروع'])
        self.assertEqual(rows[0]['running_balance'], '1,000.00')
        self.assertEqual(self.treasury_rows(self.a_cash), [])   # الوجهة لم تتحرك أصلاً

    def test_bank_statements_show_the_same_stages(self):
        resp = self.send(self.sup_a, 'bank', self.a_bank, 'bank', self.ho_bank, '200')
        t = BranchTransfer.objects.get(pk=resp.json()['id'])
        self.receive(self.user, t, '150')
        src, dst = self.bank_rows(self.a_bank), self.bank_rows(self.ho_bank)
        self.assertEqual(self.refs(src), ['إعادة فرق تحويل فروع', 'تحويل فروع'])
        self.assertEqual(self.refs(dst), ['تحويل فروع'])
        self.assertEqual((self.bal(self.a_bank), self.bal(self.ho_bank)), (D('850'), D('1150')))
        t2 = BranchTransfer.objects.get(pk=self.send(self.sup_a, 'bank', self.a_bank, 'bank', self.ho_bank, '10').json()['id'])
        self.cancel(self.sup_a, t2)
        self.assertEqual(self.refs(self.bank_rows(self.a_bank))[:2], ['إلغاء تحويل فروع', 'تحويل فروع'])

    def test_mixed_kinds_leave_no_trace_in_any_statement(self):
        self.send(self.user, 'cash', self.ho_cash, 'bank', self.a_bank, '60')
        self.assertEqual(self.treasury_rows(self.ho_cash), [])
        self.assertEqual(self.bank_rows(self.a_bank), [])

    def test_running_balance_equals_current_balance_after_every_step(self):
        t = self.ho_to_a_cash('100')
        t2 = self.ho_to_a_cash('40')
        self.receive(self.sup_a, t, '90')
        self.cancel(self.user, t2)
        for treasury in (self.ho_cash, self.a_cash):
            last = TreasuryMovement.objects.filter(treasury=treasury).order_by('-id').first()
            self.assertEqual(last.running_balance, self.bal(treasury))
        self.assertEqual(self.bal(self.ho_cash), D('910'))   # -100 -40 +10 فرق +40 إلغاء
        self.assertEqual(self.bal(self.a_cash), D('1090'))


class ScreensAndAccessTests(BranchTransferBase):
    def test_list_scoping_and_filters(self):
        t1 = self.ho_to_a_cash('10')
        resp = self.send(self.user, 'cash', self.ho_cash, 'cash', self.b_cash, '20', branch=self.b)
        t2 = BranchTransfer.objects.get(pk=resp.json()['id'])
        self.client.force_login(self.user)
        html = self.client.get(reverse('branch_transfers:list')).content.decode()
        self.assertIn(t1.number, html); self.assertIn(t2.number, html)
        html = self.client.get(reverse('branch_transfers:list'), {'branch': self.b.pk}).content.decode()
        self.assertNotIn(t1.number, html); self.assertIn(t2.number, html)
        self.client.force_login(self.sup_a)
        html = self.client.get(reverse('branch_transfers:list')).content.decode()
        self.assertIn(t1.number, html); self.assertNotIn(t2.number, html)
        self.assertEqual(self.client.get(reverse('branch_transfers:detail', args=[t2.pk])).status_code, 404)

    def test_stats_awaiting_me(self):
        self.ho_to_a_cash('10'); self.a_to_ho_cash('10')
        self.client.force_login(self.sup_a)
        self.assertEqual(self.client.get(reverse('branch_transfers:list')).context['stats']['awaiting_me'], 1)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('branch_transfers:list')).context['stats']['awaiting_me'], 1)

    def test_form_data_per_role(self):
        self.client.force_login(self.user)
        data = self.client.get(reverse('branch_transfers:create')).context['form_data']
        self.assertEqual(data['direction'], 'to_branch')
        self.assertTrue(all(s['is_head_office'] for s in data['sources']))
        self.assertTrue(all(not d['is_head_office'] for d in data['destinations']))
        self.assertEqual({b['name'] for b in data['branches']}, {'فرع أ', 'فرع ب'})
        self.client.force_login(self.sup_a)
        data = self.client.get(reverse('branch_transfers:create')).context['form_data']
        self.assertEqual(data['direction'], 'to_head_office')
        self.assertTrue(data['branch_fixed'])
        self.assertTrue(all(s['branch_id'] == self.a.pk for s in data['sources']))
        self.assertTrue(all(d['is_head_office'] for d in data['destinations']))

    def test_detail_exposes_actions_by_role(self):
        t = self.ho_to_a_cash('10')
        self.client.force_login(self.user)
        ctx = self.client.get(reverse('branch_transfers:detail', args=[t.pk])).context
        self.assertEqual((ctx['can_cancel'], ctx['can_receive']), (True, False))
        self.client.force_login(self.sup_a)
        ctx = self.client.get(reverse('branch_transfers:detail', args=[t.pk])).context
        self.assertEqual((ctx['can_cancel'], ctx['can_receive']), (False, True))

    def test_sidebar_entry_is_inside_the_finance_menu_and_not_on_the_treasury_page(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        url = reverse('branch_transfers:list')
        self.assertEqual(html.count(f'href="{url}"'), 1)
        finance = html.index('الحسابات والمالية')
        reports = html.index('التقارير', finance)
        self.assertTrue(finance < html.index(f'href="{url}"') < reports)
        treasury_page = self.client.get(reverse('treasury:list')).content.decode()
        self.assertEqual(treasury_page.count(f'href="{url}"'), 1)   # قائمة الحسابات والمالية فقط
        self.assertNotIn('hqTransferModal', treasury_page)
        self.assertNotIn('btnOpenHqTransfer', treasury_page)
        self.assertNotIn('تحويل مع الإدارة المركزية', treasury_page)

    def test_user_without_the_permission_is_denied(self):
        from apps.accounts.models import PermissionGroup
        group = PermissionGroup.objects.create(tenant=self.tenant, name='بلا تحويلات', is_active=True,
                                               permissions={'view_stocks': True})
        user = User.objects.create_user(username='bt-plain', password='x12345678', tenant=self.tenant, branch=self.a)
        user.permission_groups.add(group)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('branch_transfers:list')).status_code, 302)
        resp = self.send(user, 'cash', self.a_cash, 'cash', self.ho_cash)
        self.assertEqual(resp.status_code, 403)

    def test_permission_keys_and_tree(self):
        from apps.accounts.permissions import filter_schema_for_tenant
        keys = ('view_branch_transfers', 'add_branch_transfers', 'receive_branch_transfers', 'cancel_branch_transfers')
        for user in (self.user, self.sup_a):
            for key in keys:
                self.assertTrue(user.has_perm_key(key), (user.username, key))
        self.assertIn('تحويلات الفروع', filter_schema_for_tenant(self.tenant))

    def test_old_screens_refuse_head_office_branch_transfers(self):
        self.client.force_login(self.user)
        resp = self.client.post('/treasury/api/transfer/', json.dumps({
            'from_treasury': self.ho_cash.pk, 'to_treasury': self.a_cash.pk, 'from_amount': '10', 'to_amount': '10',
            'exchange_rate': '1', 'transfer_date': '2026-10-11'}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('تحويلات الفروع', resp.json()['message'])


class OtherPlansHaveNoBranchTransfersTests(TenantTestCase):
    """الباقتان الأساسية والاحترافية: لا قسم ولا مسارات ولا أثر."""

    def test_basic_plan(self):
        self.assertEqual(self.client.get(reverse('branch_transfers:list')).status_code, 404)
        self.assertEqual(self.client.get(reverse('branch_transfers:create')).status_code, 404)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertNotIn('تحويلات الفروع', html)
        self.assertEqual(self.client.get(reverse('treasury:list')).status_code, 200)


class ProPlanHasNoBranchTransfersTests(OtherPlansHaveNoBranchTransfersTests):
    version_type = 'multi_stock'
    subscription_plan = 'pro'
