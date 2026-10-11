"""
مراجعة صلاحيات إدارة النشاط (نسخة المؤسسات): كل ما يخص الإدارة المركزية متاح لمدير النشاط ومجموعات «إدارة النشاط»،
ومحجوب عن الفروع، وتقارير حسابات الإدارة تقتصر على خزائنها وحساباتها البنكية.
"""
from decimal import Decimal

from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.accounts.permissions import (
    filter_schema_for_tenant, get_branch_supervisor_permission_keys, get_enterprise_owner_permission_keys,
    get_scope_excluded_permission_keys,
)
from apps.bank_accounts.models import BankAccount, BankAccountMovement
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase
from apps.treasury.models import Treasury, TreasuryMovement

# كل مفاتيح الإدارة المركزية التي أُضيفت أو وُجدت: يجب أن يملكها مدير النشاط وألا يملكها مشرف الفرع.
HEAD_OFFICE_KEYS = [
    'view_head_office_treasury', 'transfer_head_office_treasury',
    'view_head_office_bank_accounts', 'add_head_office_bank_accounts', 'change_head_office_bank_accounts',
    'delete_head_office_bank_accounts', 'transfer_head_office_bank_accounts', 'view_head_office_account_reports',
    'view_head_office_expenses', 'add_head_office_expenses', 'change_head_office_expenses',
    'delete_head_office_expenses', 'view_head_office_expenses_report',
    'view_branch_requisitions', 'review_branch_requisitions',
    'view_central_shipments', 'add_central_shipments',
]
# مفاتيح مشتركة بين الإدارة والفرع.
SHARED_KEYS = ['view_branch_transfers', 'add_branch_transfers', 'receive_branch_transfers', 'cancel_branch_transfers']


class HeadOfficePermissionReviewTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.purchasing_mode = 'central'
        self.tenant.save()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.sup = User.objects.create_user(username='hp-sup', password='x12345678', tenant=self.tenant,
                                            branch=self.branch, is_branch_supervisor=True)

    def test_owner_has_every_head_office_key_and_branch_supervisor_has_none(self):
        owner_keys = self.user.get_permission_keys()
        sup_keys = self.sup.get_permission_keys()
        for key in HEAD_OFFICE_KEYS:
            self.assertIn(key, owner_keys, key)
            self.assertNotIn(key, sup_keys, key)
        for key in SHARED_KEYS:
            self.assertIn(key, owner_keys, key)
            self.assertIn(key, sup_keys, key)

    def test_head_office_keys_are_admin_only_in_the_group_screen(self):
        branch_hidden = set(get_scope_excluded_permission_keys('admin_only', True))
        sections = filter_schema_for_tenant(self.tenant)
        all_tree_keys = {k for perms in sections.values() for k in perms}
        for key in HEAD_OFFICE_KEYS:
            self.assertIn(key, all_tree_keys, f'{key} ناقص من شجرة الصلاحيات')
        admin_keys = set(get_enterprise_owner_permission_keys(True))
        branch_keys = set(get_branch_supervisor_permission_keys(True))
        for key in HEAD_OFFICE_KEYS:
            self.assertIn(key, admin_keys, key)
            self.assertNotIn(key, branch_keys, key)
        self.assertIn('مصروفات الإدارة المركزية', sections)
        self.assertIn('تحويلات الفروع', sections)

    def test_every_head_office_screen_opens_for_the_owner(self):
        self.client.force_login(self.user)
        for name in ('expenses:list', 'expenses:reports:summary_report', 'expenses:reports:details_report',
                     'expenses:reports:by_category_report', 'expenses:reports:by_date_report',
                     'branch_transfers:list', 'branch_transfers:create', 'treasury:list', 'bank_accounts:list',
                     'treasury:reports:movements_report', 'bank_accounts:reports:movements_report',
                     'bank_accounts:reports:statement_report', 'requisitions:list', 'purchases:order_list',
                     'suppliers:list', 'stocks:shipment_list'):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)

    def test_branch_supervisor_cannot_open_head_office_only_screens(self):
        self.client.force_login(self.sup)
        for name in ('requisitions:create',):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)   # شاشات الفرع تعمل
        for name in ('purchases:order_list', 'suppliers:list'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)   # شراء المركزي للإدارة فقط


class HeadOfficeAccountReportsTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.sup = User.objects.create_user(username='ar-sup', password='x12345678', tenant=self.tenant,
                                            branch=self.branch, is_branch_supervisor=True)
        self.ho_cash = Treasury.objects.get(tenant=self.tenant, is_head_office=True)
        self.b_cash = Treasury.objects.get(tenant=self.tenant, branch=self.branch)
        self.ho_bank = BankAccount.objects.create(tenant=self.tenant, name='بنك الإدارة', is_head_office=True,
                                                  current_balance=Decimal('500'))
        self.b_bank = BankAccount.objects.create(tenant=self.tenant, name='بنك الفرع', branch=self.branch,
                                                 current_balance=Decimal('500'))
        from django.utils import timezone
        today = timezone.localdate()
        for t in (self.ho_cash, self.b_cash):
            TreasuryMovement.objects.create(tenant=self.tenant, treasury=t, movement_type='receipt', amount=Decimal('10'),
                                            movement_date=today, description=f'حركة {t.name}', running_balance=Decimal('10'))
        for b in (self.ho_bank, self.b_bank):
            BankAccountMovement.objects.create(tenant=self.tenant, bank_account=b, movement_type='receipt',
                                               amount=Decimal('10'), movement_date=today, description=f'حركة {b.name}',
                                               running_balance=Decimal('10'))

    def test_owner_movement_reports_only_cover_head_office_accounts(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('treasury:reports:movements_report'), {'treasury_id': self.ho_cash.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual({t.pk for t in resp.context['treasuries']}, {self.ho_cash.pk})
        self.assertEqual(len(resp.context['report']['data']), 1)
        # معرّف خزينة فرع لا يُفتح (يعود بلا بيانات).
        resp = self.client.get(reverse('treasury:reports:movements_report'), {'treasury_id': self.b_cash.pk})
        self.assertEqual(resp.context['report']['data'], [])
        resp = self.client.get(reverse('bank_accounts:reports:movements_report'), {'bank_account_id': self.ho_bank.pk})
        self.assertEqual(len(resp.context['report']['data']), 1)
        resp = self.client.get(reverse('bank_accounts:reports:movements_report'), {'bank_account_id': self.b_bank.pk})
        self.assertEqual(resp.context['report']['data'], [])

    def test_owner_bank_statement_only_for_head_office_accounts(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('bank_accounts:reports:statement_report'), {'bank_account_id': self.ho_bank.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context['report'])
        self.assertEqual({a.pk for a in resp.context['accounts']}, {self.ho_bank.pk})
        resp = self.client.get(reverse('bank_accounts:reports:statement_report'), {'bank_account_id': self.b_bank.pk})
        self.assertIsNone(resp.context['report'])
        for name in ('treasury:reports:movements_report_export', 'bank_accounts:reports:movements_report_export',
                     'bank_accounts:reports:statement_report_export'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_branch_reports_are_unchanged(self):
        self.client.force_login(self.sup)
        resp = self.client.get(reverse('treasury:reports:movements_report'), {'treasury_id': self.b_cash.pk})
        self.assertEqual(len(resp.context['report']['data']), 1)
        self.assertEqual(self.client.get(reverse('treasury:reports:movements_report'),
                                         {'treasury_id': self.ho_cash.pk}).context['report']['data'], [])
        resp = self.client.get(reverse('bank_accounts:reports:statement_report'), {'bank_account_id': self.b_bank.pk})
        self.assertIsNotNone(resp.context['report'])

    def test_user_with_neither_key_is_denied(self):
        group = PermissionGroup.objects.create(tenant=self.tenant, name='لا شيء', is_active=True, scope='admin',
                                               permissions={'view_head_office_treasury': True})
        u = User.objects.create_user(username='ar-none', password='x12345678', tenant=self.tenant)
        u.permission_groups.add(group)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('treasury:reports:movements_report')).status_code, 302)
        # مستخدم فرع بمفتاح الإدارة وحده مرفوض أيضاً.
        g2 = PermissionGroup.objects.create(tenant=self.tenant, name='فرع بمفتاح إدارة', is_active=True,
                                            permissions={'view_head_office_account_reports': True})
        v = User.objects.create_user(username='ar-bad', password='x12345678', tenant=self.tenant, branch=self.branch)
        v.permission_groups.add(g2)
        self.client.force_login(v)
        self.assertEqual(self.client.get(reverse('treasury:reports:movements_report')).status_code, 302)
