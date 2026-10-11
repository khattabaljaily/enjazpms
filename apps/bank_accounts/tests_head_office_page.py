"""شاشة الحسابات البنكية لمدير النشاط (نسخة المؤسسات): زر الإضافة وإجراءات الصف تتبع صلاحيات الإدارة المركزية."""
import json
import re

from django.urls import reverse

from apps.bank_accounts.models import BankAccount
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase


class HeadOfficeBankAccountsPageTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def _perm_keys_on_page(self, html):
        match = re.search(r'new Set\((\[.*?\])\)', html)
        return set(json.loads(match.group(1)))

    def test_owner_page_has_add_button_wired_to_head_office_keys(self):
        resp = self.client.get(reverse('bank_accounts:list'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="btnOpenCreateBankAccount" data-require-any-perm="add_bank_accounts,add_head_office_bank_accounts"', html)
        keys = self._perm_keys_on_page(html)
        self.assertIn('add_head_office_bank_accounts', keys)
        self.assertNotIn('add_bank_accounts', keys)  # لذلك كان الزر مخفياً

    def test_owner_creates_head_office_account(self):
        resp = self.client.post(reverse('bank_accounts:create_api'), {
            'name': 'بنك الإدارة', 'bank_name': 'بنك', 'account_number': '123', 'is_active': 'on'})
        self.assertEqual(resp.status_code, 200, resp.content)
        account = BankAccount.objects.get(tenant=self.tenant, name='بنك الإدارة')
        self.assertTrue(account.is_head_office)
        self.assertIsNone(account.branch_id)

    def test_table_rows_expose_is_head_office_flag(self):
        BankAccount.objects.create(tenant=self.tenant, name='حساب إدارة', is_head_office=True)
        resp = self.client.get(reverse('bank_accounts:table_api'), {'draw': 1, 'start': 0, 'length': 25})
        rows = resp.json()['data']
        self.assertTrue(rows and all(r['is_head_office'] for r in rows))

    def test_row_actions_use_the_matching_permission_family(self):
        html = self.client.get(reverse('bank_accounts:list')).content.decode()
        self.assertIn("canOn(r, 'change')", html)
        self.assertIn("canOn(row, 'delete')", html)
        self.assertNotIn("hasPerm('change_bank_accounts')", html)


class TransferFixtureBase(TenantTestCase):
    """نافذة التحويل: بيانات كل دور، وقواعد الخادم التي تبني عليها الواجهة."""
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        from decimal import Decimal
        from apps.accounts.models import User
        from apps.treasury.models import Treasury
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع 1')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع 2')
        self.ho_bank = BankAccount.objects.create(
            tenant=self.tenant, name='بنك الإدارة', is_head_office=True, current_balance=Decimal('1000'))
        self.ho_bank2 = BankAccount.objects.create(
            tenant=self.tenant, name='بنك الإدارة 2', is_head_office=True, current_balance=Decimal('1000'))
        self.bank1 = BankAccount.objects.create(tenant=self.tenant, name='بنك فرع 1', branch=self.b1,
                                                current_balance=Decimal('1000'))
        self.bank1b = BankAccount.objects.create(tenant=self.tenant, name='بنك فرع 1 ب', branch=self.b1,
                                                 current_balance=Decimal('1000'))
        self.bank2 = BankAccount.objects.create(tenant=self.tenant, name='بنك فرع 2', branch=self.b2,
                                                current_balance=Decimal('1000'))
        self.ho_treasury = Treasury.objects.filter(tenant=self.tenant, is_head_office=True).first()
        Treasury.objects.filter(pk=self.ho_treasury.pk).update(current_balance=Decimal('1000'))
        self.t1 = Treasury.objects.create(tenant=self.tenant, name='خزينة فرع 1', branch=self.b1,
                                          current_balance=Decimal('1000'))
        self.sup1 = User.objects.create_user(username='tm-sup1', password='x12345678', tenant=self.tenant,
                                             branch=self.b1, is_branch_supervisor=True)

    def data(self, user):
        self.client.force_login(user)
        resp = self.client.get(reverse('bank_accounts:list'))
        self.assertEqual(resp.status_code, 200)
        return resp.context['transfer_data'], resp.content.decode()

    def post_transfer(self, user, frm, to, amount='100'):
        import json
        self.client.force_login(user)
        return self.client.post(reverse('bank_accounts:transfer_api'), json.dumps({
            'from_bank_account': frm.pk, 'to_bank_account': to.pk, 'from_amount': amount, 'to_amount': amount,
            'exchange_rate': '1', 'transfer_date': '2026-10-11'}), content_type='application/json')

    def post_tb(self, user, treasury, bank, direction='treasury_to_bank'):
        import json
        self.client.force_login(user)
        return self.client.post(reverse('bank_accounts:treasury_transfer_api'), json.dumps({
            'direction': direction, 'treasury_id': treasury.pk, 'bank_account_id': bank.pk, 'amount': '50',
            'transfer_date': '2026-10-11'}), content_type='application/json')


class TransferModalDataTests(TransferFixtureBase):
    """نافذة التحويل: بيانات كل دور، وقواعد الخادم التي تبني عليها الواجهة."""

    def test_owner_gets_all_accounts_and_ho_only_sources(self):
        data, html = self.data(self.user)
        names = {b['name'] for b in data['banks']}
        self.assertEqual(names, {'بنك الإدارة', 'بنك الإدارة 2', 'بنك فرع 1', 'بنك فرع 1 ب', 'بنك فرع 2'})
        self.assertIsNone(data['branch_id'])
        self.assertFalse(data['can_from_any'])
        self.assertIn('id="transferData"', html)
        self.assertIn('function bankSources()', html)

    def test_branch_user_gets_only_own_branch_accounts(self):
        data, _ = self.data(self.sup1)
        names = {b['name'] for b in data['banks']}
        self.assertEqual(names, {'بنك فرع 1', 'بنك فرع 1 ب'})
        self.assertEqual(data['branch_id'], self.b1.pk)
        self.assertEqual({t['name'] for t in data['treasuries']}, {'خزينة فرع 1'})

    def test_owner_transfers_between_own_accounts_but_not_to_a_branch_from_this_screen(self):
        self.assertEqual(self.post_transfer(self.user, self.ho_bank, self.ho_bank2).status_code, 200)
        resp = self.post_transfer(self.user, self.ho_bank, self.bank1)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('تحويلات الفروع', resp.json()['message'])

    def test_owner_cannot_transfer_from_a_branch_account(self):
        self.assertEqual(self.post_transfer(self.user, self.bank1, self.ho_bank).status_code, 403)

    def test_branch_moves_only_inside_its_own_accounts(self):
        self.assertEqual(self.post_transfer(self.sup1, self.bank1, self.bank2).status_code, 400)
        self.assertEqual(self.post_transfer(self.sup1, self.bank1, self.ho_bank).status_code, 400)  # من «تحويلات الفروع»
        self.assertEqual(self.post_transfer(self.sup1, self.bank1, self.bank1b).status_code, 200)
        self.assertIn(self.post_transfer(self.sup1, self.ho_bank, self.bank1).status_code, (403, 404))

    def test_owner_treasury_bank_transfer_is_head_office_only(self):
        for direction in ('treasury_to_bank', 'bank_to_treasury'):
            self.assertEqual(self.post_tb(self.user, self.ho_treasury, self.ho_bank, direction).status_code, 200,
                             direction)
        self.assertEqual(self.post_tb(self.user, self.ho_treasury, self.bank1).status_code, 403)
        self.assertEqual(self.post_tb(self.user, self.t1, self.ho_bank).status_code, 403)

    def test_branch_treasury_bank_transfer_stays_within_the_branch(self):
        self.assertEqual(self.post_tb(self.sup1, self.t1, self.bank1).status_code, 200)
        self.assertEqual(self.post_tb(self.sup1, self.t1, self.ho_bank).status_code, 403)


class TransferCurrencyValidationTests(TransferFixtureBase):
    """العملة: لا تحويل خزينة↔بنك بعملتين مختلفتين، ونفس العملة بلا سعر صرف، والمستلَم = المحوَّل × السعر."""

    def setUp(self):
        super().setUp()
        from decimal import Decimal
        from apps.treasury.models import Treasury
        self.usd_bank = BankAccount.objects.create(
            tenant=self.tenant, name='بنك دولار', currency='USD', is_head_office=True, current_balance=Decimal('1000'))
        self.usd_bank2 = BankAccount.objects.create(
            tenant=self.tenant, name='بنك دولار 2', currency='USD', is_head_office=True, current_balance=Decimal('1000'))
        self.usd_treasury = Treasury.objects.create(
            tenant=self.tenant, name='خزينة دولار', currency='USD', is_head_office=True, is_hard_currency=True,
            current_balance=Decimal('1000'))

    def test_treasury_and_bank_must_share_the_currency_both_directions(self):
        for direction in ('treasury_to_bank', 'bank_to_treasury'):
            resp = self.post_tb(self.user, self.usd_treasury, self.ho_bank, direction)  # دولار ↔ جنيه
            self.assertEqual(resp.status_code, 400, direction)
            self.assertIn('تتطابق العملتان', resp.json()['message'])
            resp = self.post_tb(self.user, self.ho_treasury, self.usd_bank, direction)  # جنيه ↔ دولار
            self.assertEqual(resp.status_code, 400, direction)
            self.assertEqual(self.post_tb(self.user, self.usd_treasury, self.usd_bank, direction).status_code, 200)
            self.assertEqual(self.post_tb(self.user, self.ho_treasury, self.ho_bank, direction).status_code, 200)

    def test_rejected_transfer_moves_nothing(self):
        from apps.treasury.models import Treasury
        self.post_tb(self.user, self.usd_treasury, self.ho_bank)
        self.usd_treasury.refresh_from_db()
        self.ho_bank.refresh_from_db()
        self.assertEqual(self.usd_treasury.current_balance, 1000)
        self.assertEqual(self.ho_bank.current_balance, 1000)

    def post_bank(self, frm, to, from_amount, to_amount, rate):
        import json
        self.client.force_login(self.user)
        return self.client.post(reverse('bank_accounts:transfer_api'), json.dumps({
            'from_bank_account': frm.pk, 'to_bank_account': to.pk, 'from_amount': from_amount,
            'to_amount': to_amount, 'exchange_rate': rate, 'transfer_date': '2026-10-11'}),
            content_type='application/json')

    def test_same_currency_forces_rate_one_and_equal_amounts(self):
        from apps.bank_accounts.models import BankAccountTransfer
        resp = self.post_bank(self.ho_bank, self.ho_bank2, '100', '500', '5')  # قيم متضاربة لنفس العملة
        self.assertEqual(resp.status_code, 200, resp.content)
        transfer = BankAccountTransfer.objects.get(tenant=self.tenant)
        self.assertEqual((transfer.from_amount, transfer.to_amount, transfer.exchange_rate), (100, 100, 1))
        self.ho_bank2.refresh_from_db()
        self.assertEqual(self.ho_bank2.current_balance, 1100)

    def test_different_currencies_require_consistent_amounts(self):
        resp = self.post_bank(self.ho_bank, self.usd_bank, '100', '999', '0.5')
        self.assertEqual(resp.status_code, 400)  # بين حسابين للإدارة بعملتين: يلزم المستلَم = المحوَّل × السعر
        self.assertIn('لا يطابق', resp.json()['message'])
        self.assertEqual(self.post_bank(self.ho_bank, self.usd_bank, '100', '50', '0.5').status_code, 200)
