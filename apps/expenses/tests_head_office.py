"""مصروفات الإدارة المركزية (نسخة المؤسسات): من خزائنها وحساباتها البنكية، بتحقق الرصيد والعملة، وتقاريرها."""
import json
from datetime import date
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PermissionGroup, User
from apps.bank_accounts.models import BankAccount
from apps.bank_accounts.reports import BankAccountReportGenerator
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.reports import ExpensesReportGenerator
from apps.treasury.models import Treasury
from apps.treasury.reports import TreasuryReportGenerator

D = Decimal


class HOExpenseBase(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.save()
        self.a = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.b = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        self.sup_a = User.objects.create_user(username='ex-a', password='x12345678', tenant=self.tenant,
                                              branch=self.a, is_branch_supervisor=True)
        self.ho_cash = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=False)
        self.ho_cash_usd = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=True)
        self.a_cash = Treasury.objects.get(tenant=self.tenant, branch=self.a, is_hard_currency=False)
        for t in (self.ho_cash, self.ho_cash_usd, self.a_cash):
            Treasury.objects.filter(pk=t.pk).update(current_balance=D('1000'))
        mk = lambda **kw: BankAccount.objects.create(tenant=self.tenant, current_balance=D('1000'), **kw)
        self.ho_bank = mk(name='بنك الإدارة', is_head_office=True)
        self.ho_bank_usd = mk(name='بنك الإدارة دولار', is_head_office=True, currency='USD')
        self.a_bank = mk(name='بنك فرع أ', branch=self.a)
        self.a_bank_usd = mk(name='بنك فرع أ دولار', branch=self.a, currency='USD')
        self.cat = ExpenseCategory.objects.create(tenant=self.tenant, name='إيجار')
        self.cat2 = ExpenseCategory.objects.create(tenant=self.tenant, name='كهرباء')

    def bal(self, acc):
        acc.refresh_from_db()
        return acc.current_balance

    def post(self, user, amount='100', method='cash', treasury=None, bank=None, category=None, **extra):
        self.client.force_login(user)
        body = {'category_id': (category or self.cat).pk, 'description': 'مصروف اختبار', 'amount': amount,
                'expense_date': timezone.localdate().isoformat(), 'payment_method': method,
                'treasury_id': treasury.pk if treasury else None, 'bank_account_id': bank.pk if bank else None, **extra}
        return self.client.post(reverse('expenses:create_api'), json.dumps(body), content_type='application/json')

    def ho_expense(self, amount='100', **kw):
        resp = self.post(self.user, amount, **kw)
        self.assertEqual(resp.status_code, 200, resp.content)
        return Expense.unscoped.filter(tenant=self.tenant).order_by('-id').first()

    def branch_expense(self, amount='50', **kw):
        kw.setdefault('treasury', self.a_cash)
        resp = self.post(self.sup_a, amount, **kw)
        self.assertEqual(resp.status_code, 200, resp.content)
        return Expense.unscoped.filter(tenant=self.tenant).order_by('-id').first()


class HeadOfficeExpenseFlowTests(HOExpenseBase):
    def test_cash_expense_from_head_office_treasury(self):
        e = self.ho_expense('120', treasury=self.ho_cash)
        self.assertEqual((e.status, e.branch_id, e.treasury_id, e.payment_method), ('confirmed', None, self.ho_cash.pk, 'cash'))
        self.assertEqual(self.bal(self.ho_cash), 880)
        self.assertEqual(e.treasury_movement.reference_type, 'expense')

    def test_bank_expense_from_head_office_bank_account(self):
        e = self.ho_expense('300', method='bank', bank=self.ho_bank, reference_number='TR-1')
        self.assertEqual((e.branch_id, e.bank_account_id, e.reference_number), (None, self.ho_bank.pk, 'TR-1'))
        self.assertEqual(self.bal(self.ho_bank), 700)

    def test_cash_requires_an_explicit_head_office_treasury(self):
        resp = self.post(self.user, '10')   # بلا خزينة: لا «افتراضية» لمصروفات الإدارة
        self.assertEqual(resp.status_code, 400)
        self.assertIn('خزينة', resp.json()['message'])
        self.assertFalse(Expense.unscoped.filter(tenant=self.tenant).exists())

    def test_insufficient_balance_is_rejected_for_cash_and_bank_and_nothing_is_saved(self):
        for kw in ({'treasury': self.ho_cash}, {'method': 'bank', 'bank': self.ho_bank}):
            resp = self.post(self.user, '5000', **kw)
            self.assertEqual(resp.status_code, 400)
            self.assertIn('غير كافٍ', resp.json()['message'])
        self.assertFalse(Expense.unscoped.filter(tenant=self.tenant).exists())
        self.assertEqual((self.bal(self.ho_cash), self.bal(self.ho_bank)), (1000, 1000))

    def test_cancel_reverses_the_movement(self):
        e = self.ho_expense('100', treasury=self.ho_cash)
        self.client.force_login(self.user)
        resp = self.client.post(reverse('expenses:cancel', args=[e.pk]))
        self.assertTrue(resp.json()['success'], resp.content)
        e.refresh_from_db()
        self.assertEqual(e.status, 'cancelled')
        self.assertEqual(self.bal(self.ho_cash), 1000)
        self.assertEqual(self.client.post(reverse('expenses:cancel', args=[e.pk])).status_code, 400)

    def test_categories_via_head_office_keys(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('expenses:category_list_api')).status_code, 200)
        resp = self.client.post(reverse('expenses:category_create_api'), json.dumps({'name': 'صيانة'}),
                                content_type='application/json')
        self.assertTrue(resp.json()['success'])


class CurrencyAndAccountRulesTests(HOExpenseBase):
    def test_currency_mismatch_is_rejected_for_head_office(self):
        # حساب بنكي دولار: غير متاح لمصروف بالعملة المحلية.
        resp = self.post(self.user, '10', method='bank', bank=self.ho_bank_usd)
        self.assertEqual(resp.status_code, 400)
        # خزينة العملة الصعبة: مستبعدة من خيارات المصروف.
        resp = self.post(self.user, '10', treasury=self.ho_cash_usd)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual((self.bal(self.ho_bank_usd), self.bal(self.ho_cash_usd)), (1000, 1000))

    def test_service_rejects_a_foreign_currency_account_for_any_expense(self):
        from apps.expenses.services import confirm_expense
        for account_kw, method in (({'bank_account': self.a_bank_usd}, 'bank'),):
            exp = Expense.unscoped.create(
                tenant=self.tenant, category=self.cat, description='x', amount=D('10'), expense_date=date(2026, 10, 1),
                payment_method=method, branch=self.a, **account_kw)
            with self.assertRaises(ValueError) as ctx:
                confirm_expense(exp, self.user)
            self.assertIn('عملة', str(ctx.exception))
        exp = Expense.unscoped.create(
            tenant=self.tenant, category=self.cat, description='x', amount=D('10'), expense_date=date(2026, 10, 1),
            payment_method='cash', treasury=self.ho_cash_usd, branch=None)
        with self.assertRaises(ValueError):
            confirm_expense(exp, self.user)

    def test_head_office_expense_cannot_use_branch_accounts(self):
        for kw in ({'treasury': self.a_cash}, {'method': 'bank', 'bank': self.a_bank}):
            self.assertEqual(self.post(self.user, '10', **kw).status_code, 400)
        self.assertEqual(self.bal(self.a_cash), 1000)

    def test_branch_expense_cannot_use_head_office_or_other_branch_accounts_even_via_the_service(self):
        from apps.expenses.services import confirm_expense
        for account_kw, method in (({'treasury': self.ho_cash}, 'cash'), ({'bank_account': self.ho_bank}, 'bank')):
            exp = Expense.unscoped.create(
                tenant=self.tenant, category=self.cat, description='x', amount=D('10'), expense_date=date(2026, 10, 1),
                payment_method=method, branch=self.a, **account_kw)
            with self.assertRaises(ValueError):
                confirm_expense(exp, self.user)
        other = Treasury.objects.create(tenant=self.tenant, name='خزينة فرع ب', branch=self.b, current_balance=D('100'))
        exp = Expense.unscoped.create(
            tenant=self.tenant, category=self.cat, description='x', amount=D('10'), expense_date=date(2026, 10, 1),
            payment_method='cash', treasury=other, branch=self.a)
        with self.assertRaises(ValueError):
            confirm_expense(exp, self.user)

    def test_branch_expense_still_works_and_checks_balance_and_currency(self):
        e = self.branch_expense('50')
        self.assertEqual((e.branch_id, self.bal(self.a_cash)), (self.a.pk, 950))
        resp = self.post(self.sup_a, '5000', treasury=self.a_cash)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('غير كافٍ', resp.json()['message'])
        resp = self.post(self.sup_a, '10', method='bank', bank=self.a_bank_usd)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.bal(self.a_bank_usd), 1000)


class ScopeAndIsolationTests(HOExpenseBase):
    def test_list_and_table_are_scoped(self):
        h = self.ho_expense('10', treasury=self.ho_cash)
        b = self.branch_expense('20')
        self.client.force_login(self.user)
        rows = self.client.get(reverse('expenses:table_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual([r['code'] for r in rows], [h.code])
        self.client.force_login(self.sup_a)
        rows = self.client.get(reverse('expenses:table_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual([r['code'] for r in rows], [b.code])

    def test_cross_scope_objects_are_not_reachable(self):
        h = self.ho_expense('10', treasury=self.ho_cash)
        b = self.branch_expense('20')
        self.client.force_login(self.sup_a)
        self.assertEqual(self.client.get(reverse('expenses:detail_api', args=[h.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('expenses:cancel', args=[h.pk])).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('expenses:detail_api', args=[b.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('expenses:cancel', args=[b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('expenses:detail_api', args=[h.pk])).status_code, 200)

    def test_list_page_offers_only_head_office_local_currency_accounts(self):
        self.client.force_login(self.user)
        ctx = self.client.get(reverse('expenses:list')).context
        self.assertTrue(ctx['ho_scope'])
        self.assertEqual({t['name'] for t in ctx['treasuries']}, {self.ho_cash.name})
        self.assertEqual({b['name'] for b in ctx['bank_accounts']}, {'بنك الإدارة'})
        self.assertTrue(ctx['can_add'] and ctx['can_change'] and ctx['can_cancel'])
        self.client.force_login(self.sup_a)
        ctx = self.client.get(reverse('expenses:list')).context
        self.assertFalse(ctx['ho_scope'])
        self.assertEqual({b['name'] for b in ctx['bank_accounts']}, {'بنك فرع أ'})   # الدولار مستبعد

    def test_permission_split(self):
        for key in ('view_head_office_expenses', 'add_head_office_expenses', 'change_head_office_expenses',
                    'delete_head_office_expenses', 'view_head_office_expenses_report'):
            self.assertTrue(self.user.has_perm_key(key), key)
            self.assertFalse(self.sup_a.has_perm_key(key) and not self.sup_a.branch_id, key)
        self.assertFalse(self.user.has_perm_key('add_expenses'))
        # مستخدم فرع بمفتاح إدارة فقط: مرفوض (لا نطاق إدارة لمن له فرع).
        group = PermissionGroup.objects.create(tenant=self.tenant, name='خطأ', is_active=True,
                                               permissions={'view_head_office_expenses': True})
        u = User.objects.create_user(username='ex-bad', password='x12345678', tenant=self.tenant, branch=self.a)
        u.permission_groups.add(group)
        self.client.force_login(u)
        self.assertEqual(self.client.get(reverse('expenses:list')).status_code, 302)

    def test_sidebar_entries(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn(f'href="{reverse("expenses:list")}"', html)
        self.assertIn(reverse('expenses:reports:details_report'), html)
        self.assertIn(reverse('expenses:reports:summary_report'), html)


class HeadOfficeExpenseReportsTests(HOExpenseBase):
    def setUp(self):
        super().setUp()
        self.ho_expense('100', treasury=self.ho_cash)                         # إدارة 100 نقداً
        self.ho_expense('250', method='bank', bank=self.ho_bank, category=self.cat2)  # إدارة 250 بنكياً
        self.branch_expense('40')                                               # فرع أ 40

    def gen(self, **kw):
        return ExpensesReportGenerator(self.tenant, date(2020, 1, 1), date(2030, 1, 1), **kw)

    def test_generator_scopes(self):
        self.assertEqual(self.gen(head_office=True).get_summary_report()['summary']['total_amount'], '350.00')
        self.assertEqual(self.gen().get_summary_report()['summary']['total_amount'], '390.00')       # إجمالي المؤسسة
        self.assertEqual(self.gen(branch=self.a).get_summary_report()['summary']['total_amount'], '40.00')
        s = self.gen(head_office=True).get_summary_report()['summary']
        self.assertEqual((s['total_cash'], s['total_bank'], s['expense_count']), ('100.00', '250.00', '2'))

    def test_by_category_by_date_and_details(self):
        cats = {r['category_name']: r['total_amount'] for r in self.gen(head_office=True).get_by_category_report()['data']}
        self.assertEqual(cats, {'كهرباء': '250.00', 'إيجار': '100.00'})
        self.assertEqual(self.gen(head_office=True).get_by_date_report()['data'][0]['total_amount'], '350.00')
        rows = self.gen(head_office=True).get_details_report()['data']
        names = {r['treasury_name'] for r in rows}
        self.assertTrue(any(n.startswith(self.ho_cash.name) for n in names))
        self.assertTrue(any(n.startswith('بنك الإدارة') for n in names))   # الحساب البنكي يظهر أيضاً ويُوسم بالإدارة المركزية

    def test_report_pages_default_option_and_head_office_option(self):
        self.client.force_login(self.user)
        for name in ('summary_report', 'details_report', 'by_category_report', 'by_date_report'):
            url = reverse(f'expenses:reports:{name}')
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, name)
            self.assertContains(resp, 'value="head_office"')
            resp = self.client.get(url, {'branch': 'head_office'})
            self.assertEqual(resp.status_code, 200, name)
            self.assertTrue(resp.context['ho_scope'])
            self.assertEqual(self.client.get(reverse(f'expenses:reports:{name}_export'), {'branch': 'head_office'}).status_code, 200)
        resp = self.client.get(reverse('expenses:reports:summary_report'), {'branch': 'head_office'})
        self.assertEqual(resp.context['report']['summary']['total_amount'], '350.00')
        resp = self.client.get(reverse('expenses:reports:summary_report'), {'branch': self.a.pk})
        self.assertFalse(resp.context['ho_scope'])
        self.assertEqual(resp.context['report']['summary']['total_amount'], '40.00')

    def test_owner_without_the_standard_report_key_sees_only_head_office_data(self):
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='إدارة مصروفات', is_active=True, scope='admin',
            permissions={'view_head_office_expenses_report': True})
        u = User.objects.create_user(username='ex-ho-rep', password='x12345678', tenant=self.tenant)
        u.permission_groups.add(group)
        self.client.force_login(u)
        resp = self.client.get(reverse('expenses:reports:details_report'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['ho_scope'])
        self.assertEqual(len(resp.context['report']['data']), 2)

    def test_branch_user_reports_are_unchanged_and_hide_head_office(self):
        self.client.force_login(self.sup_a)
        resp = self.client.get(reverse('expenses:reports:summary_report'), {'branch': 'head_office'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['ho_scope'])
        self.assertEqual(resp.context['report']['summary']['total_amount'], '40.00')
        self.assertNotContains(resp, 'value="head_office"')


class StatementsShowHeadOfficeExpensesTests(HOExpenseBase):
    def test_treasury_and_bank_statements_show_expense_and_its_reversal(self):
        e = self.ho_expense('100', treasury=self.ho_cash)
        e2 = self.ho_expense('200', method='bank', bank=self.ho_bank)
        self.client.force_login(self.user)
        self.client.post(reverse('expenses:cancel', args=[e.pk]))
        self.client.post(reverse('expenses:cancel', args=[e2.pk]))
        trows = TreasuryReportGenerator(self.tenant).get_statement_report(self.ho_cash.pk)['data']
        brows = BankAccountReportGenerator(self.tenant).get_statement_report(self.ho_bank.pk)['data']
        for rows in (trows, brows):
            self.assertEqual([r['reference_type'] for r in rows], ['إلغاء مصروف', 'مصروف'])
            self.assertEqual(rows[0]['running_balance'], '1,000.00')


class OtherPlansUnaffectedTests(TenantTestCase):
    def test_basic_plan_expenses_work_as_before(self):
        from apps.core.test_utils import make_treasury
        treasury = make_treasury(self.tenant, current_balance='500')
        cat = ExpenseCategory.objects.create(tenant=self.tenant, name='تشغيل')
        resp = self.client.get(reverse('expenses:list'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['ho_scope'])
        body = {'category_id': cat.pk, 'description': 'x', 'amount': '100', 'expense_date': timezone.localdate().isoformat(),
                'payment_method': 'cash', 'treasury_id': treasury.pk}
        resp = self.client.post(reverse('expenses:create_api'), json.dumps(body), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        treasury.refresh_from_db()
        self.assertEqual(treasury.current_balance, D('400'))
        resp = self.client.get(reverse('expenses:reports:summary_report'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'value="head_office"')
