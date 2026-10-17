"""الخزينة المحلية الرئيسية للفرع (تُنشأ مع الفرع) محمية من الحذف، وخزائن الفرع الأخرى تُحذف عادة."""
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase
from apps.treasury.models import Treasury


class BranchMainTreasuryProtectionTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.main = Treasury.objects.get(tenant=self.tenant, branch=self.branch, is_hard_currency=False)
        self.extra = Treasury.objects.create(tenant=self.tenant, branch=self.branch, name='خزينة إضافية',
                                             currency=self.tenant.currency)
        self.sup = User.objects.create_user(username='tm-sup', password='x12345678', tenant=self.tenant,
                                            branch=self.branch, is_branch_supervisor=True)
        self.client.force_login(self.sup)

    def _delete(self, treasury):
        return self.client.post(reverse('treasury:delete_api', args=[treasury.pk]))

    def test_main_is_flagged_and_cannot_be_deleted(self):
        self.assertTrue(self.main.is_branch_main)
        self.assertFalse(self.extra.is_branch_main)
        resp = self._delete(self.main)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('الرئيسية', resp.json()['message'])
        self.assertTrue(Treasury.objects.filter(pk=self.main.pk).exists())

    def test_other_branch_treasury_can_still_be_deleted(self):
        self.assertEqual(self._delete(self.extra).status_code, 200)
        self.assertFalse(Treasury.objects.filter(pk=self.extra.pk).exists())

    def test_table_api_exposes_flag(self):
        rows = self.client.get(reverse('treasury:table_api'), {'length': 50}).json()['data']
        flags = {r['name']: r['is_branch_main'] for r in rows}
        self.assertTrue(flags[self.main.name])
        self.assertFalse(flags['خزينة إضافية'])


class HardCurrencyTreasuryEditTests(TenantTestCase):
    """زر التعديل يظهر لخزينة العملة الصعبة أيضًا (للرصيد الافتتاحي) دون أن تصير افتراضية أو تُعطَّل."""

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.save()
        self.hc = Treasury.objects.filter(tenant=self.tenant, is_hard_currency=True).first() or Treasury.objects.create(
            tenant=self.tenant, name='خزينة الدولار', is_hard_currency=True, currency='USD')

    def test_edit_button_is_not_hidden_for_hard_currency_rows(self):
        html = self.client.get(reverse('treasury:list')).content.decode()
        # عرضا الجدول (سطح المكتب) والبطاقات (الجوال) كلاهما: لا شرط يخفي زر التعديل عن خزائن العملة الصعبة
        self.assertEqual(html.count('window.openEdit(${row.id})'), 1)
        self.assertIn("const canEdit = row.is_head_office ? hasPerm('change_head_office_treasury') : hasPerm('change_treasuries');", html)
        self.assertIn("${canEdit ? `<button class=\"cx-btn-icon\" onclick=\"window.openEdit", html)
        self.assertNotIn("&& !row.is_hard_currency ?", html)
        self.assertNotIn("&& !r.is_hard_currency ?", html)

    def test_edit_sets_opening_balance_but_keeps_flags(self):
        resp = self.client.post(reverse('treasury:update_api', args=[self.hc.pk]), {
            'name': self.hc.name, 'code': self.hc.code, 'opening_balance': '250',
            'opening_balance_date': '2026-01-01', 'is_default': 'on'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.hc.refresh_from_db()
        self.assertEqual(str(self.hc.current_balance), '250.00')
        self.assertFalse(self.hc.is_default)
        self.assertTrue(self.hc.is_active)


class HeadOfficeTreasuryEditTests(TenantTestCase):
    """مدير النشاط (نسخة المؤسسات) يعدّل الرصيد الافتتاحي لخزائن الإدارة المركزية ولا يحذفها."""
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.save()
        from apps.core.signals import _ensure_head_office_treasuries
        _ensure_head_office_treasuries(self.tenant)
        self.local = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=False)
        self.hc = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=True)

    def _edit(self, treasury, amount):
        return self.client.post(reverse('treasury:update_api', args=[treasury.pk]), {
            'name': treasury.name, 'code': treasury.code, 'opening_balance': amount,
            'opening_balance_date': '2026-01-01', 'is_default': 'on'})

    def test_owner_sets_opening_balance_of_both_head_office_treasuries(self):
        self.assertTrue(self.user.has_perm_key('change_head_office_treasury'))
        for treasury, amount in ((self.local, '1000'), (self.hc, '50')):
            resp = self._edit(treasury, amount)
            self.assertEqual(resp.status_code, 200, resp.content)
            treasury.refresh_from_db()
            self.assertEqual(str(treasury.current_balance), f'{amount}.00')
            self.assertFalse(treasury.is_default)      # حالتها لا تتغير من هنا
            self.assertTrue(treasury.is_active)

    def test_owner_cannot_delete_head_office_treasuries(self):
        for treasury in (self.local, self.hc):
            self.assertNotEqual(self.client.post(reverse('treasury:delete_api', args=[treasury.pk])).status_code, 200)
            self.assertTrue(Treasury.objects.filter(pk=treasury.pk).exists())

    def test_table_exposes_head_office_flag(self):
        rows = self.client.get(reverse('treasury:table_api'), {'length': 50}).json()['data']
        self.assertTrue(rows and all(r['is_head_office'] for r in rows))

    def test_branch_user_cannot_edit_head_office_treasury(self):
        from apps.core.models import Branch
        branch = Branch.objects.create(tenant=self.tenant, name='ف')
        sup = User.objects.create_user(username='ho-sup', password='x12345678', tenant=self.tenant,
                                       branch=branch, is_branch_supervisor=True)
        self.client.force_login(sup)
        self.assertNotEqual(self._edit(self.local, '1').status_code, 200)
        self.local.refresh_from_db()
        self.assertEqual(str(self.local.current_balance), '0.00')


class BranchManagerEditTests(TenantTestCase):
    """مدير الفرع يعدّل الرصيد الافتتاحي لخزينتي فرعه (المحلية والعملة الصعبة) ولا يحذفهما."""
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.save()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.local = Treasury.objects.get(tenant=self.tenant, branch=self.branch, is_hard_currency=False)
        self.hc = Treasury.objects.get(tenant=self.tenant, branch=self.branch, is_hard_currency=True)
        self.mgr = User.objects.create_user(username='bm-mgr', password='x12345678', tenant=self.tenant,
                                            branch=self.branch, is_branch_supervisor=True)
        self.client.force_login(self.mgr)

    def test_manager_edits_opening_balance_but_cannot_delete(self):
        for treasury in (self.local, self.hc):
            resp = self.client.post(reverse('treasury:update_api', args=[treasury.pk]), {
                'name': treasury.name, 'code': treasury.code, 'opening_balance': '75',
                'opening_balance_date': '2026-01-01'})
            self.assertEqual(resp.status_code, 200, resp.content)
            treasury.refresh_from_db()
            self.assertEqual(str(treasury.current_balance), '75.00')
            self.assertEqual(self.client.post(reverse('treasury:delete_api', args=[treasury.pk])).status_code, 400)
            self.assertTrue(Treasury.objects.filter(pk=treasury.pk).exists())


class EditButtonPermissionKeysTests(TenantTestCase):
    """مفاتيح الصلاحيات المحقونة في الصفحة (window.hasPerm) تتيح زر التعديل للمالك في كل نسخة."""

    def test_basic_owner_gets_change_treasuries(self):
        html = self.client.get(reverse('treasury:list')).content.decode()
        self.assertIn('"change_treasuries"', html)


class EnterpriseOwnerEditKeyTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def test_enterprise_owner_gets_head_office_edit_key(self):
        html = self.client.get(reverse('treasury:list')).content.decode()
        self.assertIn('"change_head_office_treasury"', html)
