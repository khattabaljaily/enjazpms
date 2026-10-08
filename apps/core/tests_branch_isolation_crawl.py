"""
اختبار عزل شامل لنسخة المؤسسات (زحف على كل المسارات).

يبني فرعين (A و B) بسجلات في كل وحدة، كل منها يحمل علامة نصية فريدة
(ZZA_... / ZZB_...)، ومشتركاً ثانياً (ZZT_...) ثم:

  1. يسجّل دخول مشرف الفرع A ويفتح كل مسار GET بلا معاملات (قوائم، واجهات
     DataTables، تقارير، إكمال تلقائي...) ويتأكد أن الرد لا يحتوي أي علامة
     من الفرع B ولا من المشترك الآخر.
  2. لكل مسار بمعامل رقمي واحد أو أكثر، يجرّب معرّفات سجلات الفرع B (ومعرّفات
     المشترك الآخر) كلها ويتأكد أن الرد لا يكشف علاماتها.
  3. مستخدم الفرع B يُجرى عليه العكس (لا يرى علامات A).
  4. مدير النشاط (بلا فرع) لا يرى علامات مشترك آخر أبداً.
"""
import re
from decimal import Decimal
from itertools import product

from django.conf import settings
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import BusinessType, Branch, Tenant, TenantCapabilities
from apps.core.test_utils import (
    TenantTestCase, make_agent, make_bank_account, make_customer, make_employee,
    make_item, make_stock, make_supplier, make_treasury,
)

_TOKEN_RE = re.compile(r'<(?:(?P<type>[^:>]+):)?(?P<name>[^>]+)>')
_SKIP_PREFIXES = ('admin/', 'static/', 'media/', '__debug__/')
_SKIP_WORDS = ('logout', 'delete', 'remove', 'cancel', 'backup', 'download', 'export', 'restore', 'reset')
_PAGINATION = {'draw': 1, 'start': 0, 'length': 100, 'q': 'ZZ', 'term': 'ZZ', 'search': 'ZZ'}


def _routes():
    """[(template, [(name, type), ...])] لكل مسار GET قابل للزحف."""
    out = []

    def walk(resolver, prefix):
        for entry in resolver.url_patterns:
            full = prefix + str(entry.pattern)
            if isinstance(entry, URLResolver):
                walk(entry, full)
            elif isinstance(entry, URLPattern):
                if full.startswith(_SKIP_PREFIXES):
                    continue
                if any(w in full.lower() for w in _SKIP_WORDS):
                    continue
                if '(?P' in full or '^' in full or '$' in full:
                    continue
                out.append((full, [(m.group('name'), m.group('type') or 'str')
                                   for m in _TOKEN_RE.finditer(full)]))

    walk(get_resolver(), '')
    return out


def _body(resp):
    if getattr(resp, 'streaming', False):
        return b''.join(resp.streaming_content).decode('utf-8', 'ignore')
    return resp.content.decode('utf-8', 'ignore')


class BranchIsolationCrawlTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    @classmethod
    def _seed(cls, tenant, tag, branch):
        """يملأ كل وحدة بسجلات تحمل العلامة tag ويرجع {موديل: [pks]}."""
        today = timezone.localdate()
        ids = {}

        def reg(obj):
            ids.setdefault(type(obj).__name__, []).append(obj.pk)
            return obj

        stock = reg(make_stock(tenant, name=f'{tag}مخزن', branch=branch))
        item = reg(make_item(tenant, name=f'صنف-{tag.lower()}'))  # الأصناف كتالوج مركزي مشترك بالتصميم
        customer = reg(make_customer(tenant, name=f'{tag}عميل', branch=branch))
        supplier = reg(make_supplier(tenant, name=f'{tag}مورد', branch=branch))
        reg(make_agent(tenant, name=f'{tag}مندوب', branch=branch))
        reg(make_employee(tenant, name=f'{tag}موظف', branch=branch))
        reg(make_treasury(tenant, name=f'{tag}خزينة', branch=branch))
        reg(make_bank_account(tenant, name=f'{tag}بنك', branch=branch))

        from apps.expenses.models import Expense, ExpenseCategory
        cat = reg(ExpenseCategory.objects.create(tenant=tenant, name=f'تصنيف-{tag.lower()}'))
        treasury = make_treasury(tenant, name=f'{tag}خزينة2', current_balance='1000', branch=branch)
        try:
            reg(Expense.unscoped.create(
                tenant=tenant, branch=branch, category=cat, description=f'{tag}مصروف',
                amount=Decimal('5'), expense_date=today, treasury=treasury))
        except Exception:  # شكل الحقول يختلف بين النسخ
            pass

        from apps.sales.models import CustomerLedger, SaleInvoice, SaleInvoiceLine
        inv = reg(SaleInvoice.objects.create(
            tenant=tenant, stock=stock, customer=customer, invoice_date=today,
            status='confirmed', payment_method='credit', notes=f'{tag}ملاحظة'))
        line = SaleInvoiceLine(tenant=tenant, invoice=inv, item=item, quantity=Decimal('1'),
                               unit_price=Decimal('50'), cost_price_snapshot=Decimal('10'),
                               tax_rate=Decimal('0'))
        line.calculate()
        line.save()
        inv.recalculate_totals()
        inv.save()
        reg(CustomerLedger.objects.create(
            tenant=tenant, customer=customer, entry_type='opening', amount=Decimal('50'),
            entry_date=today, notes=f'{tag}قيد'))

        from apps.purchases.models import PurchaseInvoice
        reg(PurchaseInvoice.objects.create(
            tenant=tenant, supplier=supplier, stock=stock, branch=branch, invoice_date=today,
            status='confirmed', notes=f'{tag}شراء'))

        from apps.notifications.models import Notification
        reg(Notification.objects.create(
            tenant=tenant, branch=branch, title=f'{tag}إشعار', message=f'{tag}رسالة'))
        return ids

    def setUp(self):
        super().setUp()
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant,
            defaults={f.name: True for f in TenantCapabilities._meta.get_fields()
                      if getattr(f, 'name', '').startswith('has_')})
        self.a = Branch.objects.create(tenant=self.tenant, name='ZZA_فرع')
        self.b = Branch.objects.create(tenant=self.tenant, name='ZZB_فرع')
        self.ids_a = self._seed(self.tenant, 'ZZA_', self.a)
        self.ids_b = self._seed(self.tenant, 'ZZB_', self.b)

        bt = BusinessType.objects.create(name='other-bt', name_ar='آخر', slug='other-bt')
        limits = Tenant.PLAN_LIMITS['enterprise']
        self.other = Tenant.objects.create(
            name='ZZT_مشترك', business_type=bt, subscription_plan='enterprise',
            version_type='multi_branch', max_stocks=limits['max_stocks'],
            max_branches=limits['max_branches'], currency='SDG', hard_currency_mode=False,
            terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        other_branch = Branch.objects.create(tenant=self.other, name='ZZT_فرع')
        self.ids_t = self._seed(self.other, 'ZZT_', other_branch)

        self.user_a = User.objects.create_user(
            username='zz-sup-a', password='x12345678', tenant=self.tenant,
            branch=self.a, is_branch_supervisor=True)
        self.user_b = User.objects.create_user(
            username='zz-sup-b', password='x12345678', tenant=self.tenant,
            branch=self.b, is_branch_supervisor=True)
        self.routes = _routes()

    # ------------------------------------------------------------------
    def _get(self, path):
        try:
            return self.client.get(path, _PAGINATION, follow=True)
        except Exception as exc:  # خطأ داخلي = مشكلة تستحق التقرير لا إخفاءها
            self.fail(f'GET {path} رفع استثناء: {type(exc).__name__}: {exc}')

    def _leaks(self, path, markers):
        resp = self._get(path)
        if resp.status_code >= 500:
            self.fail(f'GET {path} → {resp.status_code}')
        body = _body(resp)
        out = []
        for m in markers:
            if m in body:
                i = body.index(m)
                out.append(f'{m} [{resp.status_code} {resp.request["PATH_INFO"]}] …{body[max(0, i - 120):i + 60]!r}')
        return out

    def _crawl(self, user, forbidden, foreign_ids):
        self.client.force_login(user)
        no_arg = [t for t, params in self.routes if not params]
        with_args = [(t, params) for t, params in self.routes if params]
        pk_pool = sorted({pk for lst in foreign_ids for pks in lst.values() for pk in pks})
        leaks = []

        for tpl in no_arg:
            path = '/' + tpl
            for m in self._leaks(path, forbidden):
                leaks.append((path, m))

        for tpl, params in with_args:
            int_params = [p for p in params if p[1] == 'int']
            if len(int_params) != len(params) or len(params) > 2:
                continue
            combos = product(pk_pool, repeat=len(params)) if len(params) == 1 else \
                [(p, p) for p in pk_pool]
            for combo in combos:
                path = '/' + _TOKEN_RE.sub(lambda m, it=iter(combo): str(next(it)), tpl)
                for m in self._leaks(path, forbidden):
                    leaks.append((path, m))
        return leaks

    def test_branch_a_user_never_sees_branch_b_or_other_tenant(self):
        leaks = self._crawl(self.user_a, ['ZZB_', 'ZZT_'], [self.ids_b, self.ids_t])
        self.assertEqual(leaks, [], f'تسريب بيانات للفرع A:\n' + '\n'.join(map(str, leaks[:40])))

    def test_branch_b_user_never_sees_branch_a_or_other_tenant(self):
        leaks = self._crawl(self.user_b, ['ZZA_', 'ZZT_'], [self.ids_a, self.ids_t])
        self.assertEqual(leaks, [], f'تسريب بيانات للفرع B:\n' + '\n'.join(map(str, leaks[:40])))

    def test_tenant_admin_never_sees_other_tenant(self):
        leaks = self._crawl(self.user, ['ZZT_'], [self.ids_t])
        self.assertEqual(leaks, [], f'تسريب بين المشتركين:\n' + '\n'.join(map(str, leaks[:40])))

    def test_crawl_is_meaningful(self):
        """يتأكد أن الزحف لا يمرّ على شاشات فارغة: مشرف الفرع يرى علامة فرعه."""
        self.client.force_login(self.user_a)
        seen = False
        for tpl, params in self.routes:
            if params:
                continue
            resp = self._get('/' + tpl)
            if 'ZZA_' in _body(resp):
                seen = True
                break
        self.assertTrue(seen, 'لم تظهر أي علامة للفرع A في أي شاشة — بيانات الاختبار لا تصل للشاشات')


class BranchWriteIsolationTests(BranchIsolationCrawlTests):
    """مستخدم الفرع B يرسل POST (حذف/تعديل/إلغاء/تأكيد...) على معرّفات سجلات الفرع A
    والمشترك الآخر: لا يجب أن يتغير أي سجل منها."""

    _WRITE_SKIP = ('logout', 'backup', 'restore', 'reset')

    # لا نعيد تشغيل اختبارات الزحف الموروثة هنا.
    test_branch_a_user_never_sees_branch_b_or_other_tenant = None
    test_branch_b_user_never_sees_branch_a_or_other_tenant = None
    test_tenant_admin_never_sees_other_tenant = None
    test_crawl_is_meaningful = None

    def _snapshot(self):
        from apps.agents.models import Agent
        from apps.bank_accounts.models import BankAccount
        from apps.customers.models import Customer
        from apps.employees.models import Employee
        from apps.expenses.models import Expense
        from apps.notifications.models import Notification
        from apps.purchases.models import PurchaseInvoice
        from apps.sales.models import SaleInvoice
        from apps.stocks.models import Stock
        from apps.suppliers.models import Supplier
        from apps.treasury.models import Treasury
        snap = {}
        for model in (Customer, Supplier, Agent, Employee, Stock, Treasury, BankAccount,
                      Expense, SaleInvoice, PurchaseInvoice, Notification):
            mgr = getattr(model, 'unscoped', model.objects)
            snap[model.__name__] = sorted(
                (r.pk, str(r), getattr(r, 'status', None), getattr(r, 'is_active', None),
                 getattr(r, 'is_read', None))
                for r in mgr.filter(tenant__in=[self.tenant, self.other]).exclude(
                    pk__in=self.ids_b.get(model.__name__, [])))
        snap['Branch'] = sorted(
            (b.pk, b.name, b.is_active) for b in Branch.objects.filter(tenant__in=[self.tenant, self.other])
            .exclude(pk=self.b.pk))
        return snap

    def test_branch_b_user_cannot_modify_branch_a_or_other_tenant_records(self):
        self.client.force_login(self.user_b)
        # أخطاء 500 المستقلة (مثل حذف عميل له فواتير) ليست موضوع هذا الاختبار؛
        # نتحقق فقط أن شيئاً خارج الفرع B لم يتغير.
        self.client.raise_request_exception = False
        before = self._snapshot()
        pool = sorted({pk for ids in (self.ids_a, self.ids_t) for pks in ids.values() for pk in pks}
                      | {self.a.pk})
        sent = 0
        for tpl, params in self.routes_all:
            if any(w in tpl.lower() for w in self._WRITE_SKIP):
                continue
            if not params or any(t != 'int' for _, t in params) or len(params) > 1:
                continue
            for pk in pool:
                path = "/" + _TOKEN_RE.sub(lambda m, pk=pk: str(pk), tpl)
                self.client.post(path, {}, follow=False)
                sent += 1
        self.assertGreater(sent, 100)
        after = self._snapshot()
        for key in before:
            self.assertEqual(before[key], after[key], f'تغيّرت سجلات {key} بعد POST من فرع آخر')

    def setUp(self):
        super().setUp()
        self.routes_all = []

        def walk(resolver, prefix):
            for entry in resolver.url_patterns:
                full = prefix + str(entry.pattern)
                if isinstance(entry, URLResolver):
                    walk(entry, full)
                elif isinstance(entry, URLPattern) and not full.startswith(_SKIP_PREFIXES) \
                        and not any(c in full for c in '(^$'):
                    self.routes_all.append((full, [(m.group('name'), m.group('type') or 'str')
                                                   for m in _TOKEN_RE.finditer(full)]))
        walk(get_resolver(), '')

    def test_deleting_own_records_with_history_returns_400_not_500(self):
        from apps.customers.models import Customer
        from apps.stocks.models import Stock
        self.client.force_login(self.user_b)
        customer = Customer.unscoped.get(tenant=self.tenant, name='ZZB_عميل')
        resp = self.client.post(f'/customers/api/{customer.pk}/delete/')
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(resp.json()['success'])
        self.assertTrue(Customer.unscoped.filter(pk=customer.pk).exists())
        stock = Stock.objects.get(tenant=self.tenant, name='ZZB_مخزن')
        resp = self.client.post(f'/stocks/api/{stock.pk}/delete/')
        self.assertIn(resp.status_code, (400, 403, 404), resp.content)
        self.assertTrue(Stock.objects.filter(pk=stock.pk).exists())
