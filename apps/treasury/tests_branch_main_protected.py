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
        self.assertIn("hasPerm('change_treasuries') ?", html)
        self.assertNotIn("hasPerm('change_treasuries') && !r.is_hard_currency", html)

    def test_edit_sets_opening_balance_but_keeps_flags(self):
        resp = self.client.post(reverse('treasury:update_api', args=[self.hc.pk]), {
            'name': self.hc.name, 'code': self.hc.code, 'opening_balance': '250',
            'opening_balance_date': '2026-01-01', 'is_default': 'on'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.hc.refresh_from_db()
        self.assertEqual(str(self.hc.current_balance), '250.00')
        self.assertFalse(self.hc.is_default)
        self.assertTrue(self.hc.is_active)
