"""
اختبار حساب الإدارة المركزية البنكي — نفس قواعد خزينة الإدارة المركزية
(apps/core/tests_head_office_treasury.py) لكن بدون إنشاء تلقائي (الحسابات
البنكية دائماً يدوية — راجع apps/bank_accounts/models.py).
"""
import json
from decimal import Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.bank_accounts.models import BankAccount
from apps.bank_accounts.services import cancel_bank_account_transfer, post_bank_account_transfer
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase


class HeadOfficeBankAccountCreationTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch_a = Branch.objects.create(tenant=self.tenant, name='Branch A')
        self.user_a = User.objects.create_user(
            username='sup-a', password='secret123', tenant=self.tenant,
            branch=self.branch_a, is_branch_supervisor=True,
        )

    def _create(self, name='حساب جديد'):
        return self.client.post('/bank-accounts/api/create/', data={
            'name': name, 'bank_name': 'بنك تجريبي', 'currency': 'SDG',
        })

    def test_owner_created_account_is_head_office(self):
        self.client.force_login(self.user)
        resp = self._create('حساب الإدارة')
        self.assertEqual(resp.status_code, 200, resp.content)
        account_id = resp.json()['id']
        account = BankAccount.objects.get(pk=account_id)
        self.assertTrue(account.is_head_office)
        self.assertIsNone(account.branch_id)

    def test_branch_user_created_account_is_not_head_office(self):
        self.client.force_login(self.user_a)
        resp = self._create('حساب الفرع')
        self.assertEqual(resp.status_code, 200, resp.content)
        account_id = resp.json()['id']
        account = BankAccount.objects.get(pk=account_id)
        self.assertFalse(account.is_head_office)
        self.assertEqual(account.branch_id, self.branch_a.id)


class HeadOfficeBankAccountTransferTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch_a = Branch.objects.create(tenant=self.tenant, name='Branch A')
        self.branch_b = Branch.objects.create(tenant=self.tenant, name='Branch B')
        self.user_a = User.objects.create_user(
            username='sup-a', password='secret123', tenant=self.tenant,
            branch=self.branch_a, is_branch_supervisor=True,
        )
        self.hq_account = BankAccount.objects.create(
            tenant=self.tenant, name='حساب الإدارة', is_head_office=True,
            currency='SDG', current_balance=Decimal('1000'),
        )
        self.branch_a_account = BankAccount.objects.create(
            tenant=self.tenant, branch=self.branch_a, name='حساب فرع أ',
            currency='SDG', current_balance=Decimal('500'),
        )
        self.branch_b_account = BankAccount.objects.create(
            tenant=self.tenant, branch=self.branch_b, name='حساب فرع ب',
            currency='SDG', current_balance=Decimal('500'),
        )
        self.branch_a_usd_account = BankAccount.objects.create(
            tenant=self.tenant, branch=self.branch_a, name='حساب فرع أ دولار',
            currency='USD', current_balance=Decimal('200'),
        )

    def _transfer(self, from_id, to_id, from_amount, to_amount=None, exchange_rate='1'):
        return self.client.post(
            '/bank-accounts/api/transfer/',
            data=json.dumps({
                'from_bank_account': from_id, 'to_bank_account': to_id,
                'from_amount': str(from_amount), 'to_amount': str(to_amount or from_amount),
                'exchange_rate': exchange_rate, 'transfer_date': timezone.localdate().isoformat(),
            }),
            content_type='application/json',
        )

    def test_branch_to_head_office_succeeds(self):
        self.client.force_login(self.user_a)
        resp = self._transfer(self.branch_a_account.id, self.hq_account.id, '100')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])

    def test_direct_branch_to_branch_rejected(self):
        self.client.force_login(self.user_a)
        resp = self._transfer(self.branch_a_account.id, self.branch_b_account.id, '100')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('مباشر', resp.json()['message'])

    def test_currency_mismatch_with_head_office_rejected(self):
        self.client.force_login(self.user_a)
        resp = self._transfer(self.branch_a_usd_account.id, self.hq_account.id, '50')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('عملة', resp.json()['message'])

    def test_owner_cannot_source_from_branch_account(self):
        self.client.force_login(self.user)
        resp = self._transfer(self.branch_a_account.id, self.hq_account.id, '50')
        self.assertEqual(resp.status_code, 403)


class HeadOfficeBankAccountCancelTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch_a = Branch.objects.create(tenant=self.tenant, name='Branch A')
        self.hq_account = BankAccount.objects.create(
            tenant=self.tenant, name='حساب الإدارة', is_head_office=True,
            currency='SDG', current_balance=Decimal('1000'),
        )
        self.branch_a_account = BankAccount.objects.create(
            tenant=self.tenant, branch=self.branch_a, name='حساب فرع أ',
            currency='SDG', current_balance=Decimal('500'),
        )

    def test_cancel_reverses_balances(self):
        transfer = post_bank_account_transfer(
            tenant=self.tenant, from_account=self.branch_a_account, to_account=self.hq_account,
            from_amount=Decimal('100'), to_amount=Decimal('100'), exchange_rate=Decimal('1'),
            transfer_date=timezone.localdate(), user=self.user,
        )
        self.branch_a_account.refresh_from_db()
        self.hq_account.refresh_from_db()
        self.assertEqual(self.branch_a_account.current_balance, Decimal('400'))
        self.assertEqual(self.hq_account.current_balance, Decimal('1100'))

        cancel_bank_account_transfer(transfer, user=self.user)
        self.branch_a_account.refresh_from_db()
        self.hq_account.refresh_from_db()
        self.assertEqual(self.branch_a_account.current_balance, Decimal('500'))
        self.assertEqual(self.hq_account.current_balance, Decimal('1000'))
        transfer.refresh_from_db()
        self.assertTrue(transfer.is_cancelled)

    def test_double_cancel_rejected(self):
        transfer = post_bank_account_transfer(
            tenant=self.tenant, from_account=self.branch_a_account, to_account=self.hq_account,
            from_amount=Decimal('100'), to_amount=Decimal('100'), exchange_rate=Decimal('1'),
            transfer_date=timezone.localdate(), user=self.user,
        )
        cancel_bank_account_transfer(transfer, user=self.user)
        with self.assertRaises(ValueError):
            cancel_bank_account_transfer(transfer, user=self.user)
