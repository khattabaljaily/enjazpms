"""
اختبار خزينة/حساب الإدارة المركزية لنسخة المؤسسات (multi_branch):
  1. الإنشاء التلقائي (خزينة محلية دائماً + عملة صعبة إن كانت مفعّلة)، عند
     إنشاء الـ tenant وعند تفعيل وضع العملة الصعبة لاحقاً.
  2. قواعد التحويل: فرع↔إدارة مسموح (بشرط تطابق العملة، بلا سعر صرف)، فرع↔فرع
     مباشرة ممنوع، تحويل الإدارة الداخلي (محلي↔عملة صعبة) يبقى بسعر صرف مثل
     أي فرع، ومدير النشاط لا يبدأ تحويلاً إلا من خزينته هو.
  3. الإلغاء/العكس: يعكس الأرصدة على الطرفين، لا يحذف الحركات الأصلية، يُرفض
     لو التحويل ملغى بالفعل أو لو تعذّر عكس الطرف الآخر لعدم كفاية الرصيد.
"""
import json
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch, BusinessType, Tenant
from apps.core.signals import _ensure_head_office_treasuries
from apps.core.test_utils import TenantTestCase
from apps.treasury.models import Treasury, TreasuryTransfer
from apps.treasury.services import cancel_treasury_transfer, post_treasury_transfer


class HeadOfficeTreasuryAutoCreationTests(TestCase):
    def setUp(self):
        self.bt = BusinessType.objects.create(name='bt-hq-tr', name_ar='نوع', slug='bt-hq-tr')
        self.terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())

    def test_enterprise_tenant_creation_creates_local_and_hc_head_office_treasuries(self):
        tenant = Tenant.objects.create(
            name='HQ Co', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG',
            hard_currency_mode=True, hard_currency='USD', **self.terms,
        )
        hq = list(Treasury.objects.filter(tenant=tenant, is_head_office=True))
        self.assertEqual(len(hq), 2)
        self.assertTrue(any(not t.is_hard_currency and t.currency == 'SDG' for t in hq))
        self.assertTrue(any(t.is_hard_currency and t.currency == 'USD' for t in hq))
        self.assertTrue(all(t.branch_id is None for t in hq))

    def test_enterprise_tenant_without_hc_creates_only_local_head_office_treasury(self):
        tenant = Tenant.objects.create(
            name='HQ Co No HC', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', hard_currency_mode=False, **self.terms,
        )
        hq = list(Treasury.objects.filter(tenant=tenant, is_head_office=True))
        self.assertEqual(len(hq), 1)
        self.assertFalse(hq[0].is_hard_currency)

    def test_toggling_hc_mode_on_later_creates_head_office_hc_treasury(self):
        tenant = Tenant.objects.create(
            name='HQ Co Toggle', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', hard_currency_mode=False, **self.terms,
        )
        self.assertEqual(Treasury.objects.filter(tenant=tenant, is_head_office=True, is_hard_currency=True).count(), 0)

        tenant.hard_currency_mode = True
        tenant.hard_currency = 'USD'
        tenant.save()

        self.assertEqual(Treasury.objects.filter(tenant=tenant, is_head_office=True, is_hard_currency=True).count(), 1)

    def test_upgrading_existing_tenant_to_enterprise_creates_head_office_treasury(self):
        """
        ترقية باقة مشترك موجود إلى 'مؤسسات' (subscription_plan + version_type)
        بدون لمس hard_currency_mode في نفس الحفظة — يجب أن تُنشئ خزينة الإدارة
        المركزية فوراً (لا تنتظر تفعيل HC mode أو تشغيل أمر backfill يدوياً).
        """
        tenant = Tenant.objects.create(
            name='Upgrading Co', business_type=self.bt, subscription_plan='basic',
            version_type='single_store', currency='SDG', **self.terms,
        )
        self.assertEqual(Treasury.objects.filter(tenant=tenant, is_head_office=True).count(), 0)

        tenant.subscription_plan = 'enterprise'
        tenant.version_type = 'multi_branch'
        tenant.save()

        self.assertEqual(Treasury.objects.filter(tenant=tenant, is_head_office=True).count(), 1)

    def test_single_store_tenant_gets_no_head_office_treasury(self):
        tenant = Tenant.objects.create(
            name='Single Co', business_type=self.bt, subscription_plan='basic',
            version_type='single_store', currency='SDG', **self.terms,
        )
        self.assertEqual(Treasury.objects.filter(tenant=tenant, is_head_office=True).count(), 0)

    def test_backfill_function_is_idempotent(self):
        tenant = Tenant.objects.create(
            name='HQ Co Backfill', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', hard_currency_mode=True, hard_currency='USD', **self.terms,
        )
        _ensure_head_office_treasuries(tenant)
        _ensure_head_office_treasuries(tenant)
        self.assertEqual(Treasury.objects.filter(tenant=tenant, is_head_office=True).count(), 2)


class HeadOfficeTransferTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.save()

        self.branch_a = Branch.objects.create(tenant=self.tenant, name='Branch A')
        self.branch_b = Branch.objects.create(tenant=self.tenant, name='Branch B')

        self.user_a = User.objects.create_user(
            username='sup-a', password='secret123', tenant=self.tenant,
            branch=self.branch_a, is_branch_supervisor=True,
        )
        self.user_b = User.objects.create_user(
            username='sup-b', password='secret123', tenant=self.tenant,
            branch=self.branch_b, is_branch_supervisor=True,
        )

        self.hq_local = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=False)
        self.hq_usd = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=True)
        self.branch_a_local = Treasury.objects.get(tenant=self.tenant, branch=self.branch_a, is_hard_currency=False)
        self.branch_a_usd = Treasury.objects.get(tenant=self.tenant, branch=self.branch_a, is_hard_currency=True)
        self.branch_b_local = Treasury.objects.get(tenant=self.tenant, branch=self.branch_b, is_hard_currency=False)

        for t in (self.hq_local, self.hq_usd, self.branch_a_local, self.branch_a_usd, self.branch_b_local):
            t.current_balance = Decimal('1000')
            t.save(update_fields=['current_balance'])

    def _transfer(self, from_id, to_id, from_amount, to_amount=None, exchange_rate='1', notes=''):
        return self.client.post(
            '/treasury/api/transfer/',
            data=json.dumps({
                'from_treasury': from_id, 'to_treasury': to_id,
                'from_amount': str(from_amount), 'to_amount': str(to_amount or from_amount),
                'exchange_rate': exchange_rate, 'transfer_date': timezone.localdate().isoformat(),
                'notes': notes,
            }),
            content_type='application/json',
        )

    def test_branch_user_can_transfer_to_head_office(self):
        self.client.force_login(self.user_a)
        resp = self._transfer(self.branch_a_local.id, self.hq_local.id, '100')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.branch_a_local.refresh_from_db()
        self.hq_local.refresh_from_db()
        self.assertEqual(self.branch_a_local.current_balance, Decimal('900'))
        self.assertEqual(self.hq_local.current_balance, Decimal('1100'))

    def test_branch_user_cannot_transfer_directly_to_another_branch(self):
        self.client.force_login(self.user_a)
        resp = self._transfer(self.branch_a_local.id, self.branch_b_local.id, '100')
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])
        self.assertIn('مباشر', resp.json()['message'])

    def test_owner_can_transfer_from_head_office_to_branch(self):
        self.client.force_login(self.user)  # self.user = tenant admin/owner, from TenantTestCase
        resp = self._transfer(self.hq_local.id, self.branch_a_local.id, '200')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.hq_local.refresh_from_db()
        self.branch_a_local.refresh_from_db()
        self.assertEqual(self.hq_local.current_balance, Decimal('800'))
        self.assertEqual(self.branch_a_local.current_balance, Decimal('1200'))

    def test_owner_cannot_source_transfer_from_a_branch_treasury(self):
        self.client.force_login(self.user)
        resp = self._transfer(self.branch_a_local.id, self.hq_local.id, '50')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(resp.json()['success'])

    def test_currency_mismatch_between_branch_and_head_office_is_rejected(self):
        self.client.force_login(self.user_a)
        # فرع أ (خزينة محلية SDG) -> إدارة (خزينة عملة صعبة USD): عملتان مختلفتان
        resp = self._transfer(self.branch_a_local.id, self.hq_usd.id, '100')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('عملة', resp.json()['message'])

    def test_matching_currency_hc_transfer_to_head_office_succeeds(self):
        self.client.force_login(self.user_a)
        resp = self._transfer(self.branch_a_usd.id, self.hq_usd.id, '50')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

    def test_owner_internal_local_to_hc_conversion_still_uses_exchange_rate(self):
        """تحويل داخلي لمدير النشاط بين خزينتيه (محلية وعملة صعبة) — يبقى بسعر صرف، لا قيد تطابق العملة."""
        self.client.force_login(self.user)
        resp = self._transfer(self.hq_local.id, self.hq_usd.id, '500', to_amount='0.1', exchange_rate='5000')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])
        self.hq_local.refresh_from_db()
        self.hq_usd.refresh_from_db()
        self.assertEqual(self.hq_local.current_balance, Decimal('1000') - Decimal('500'))
        self.assertEqual(self.hq_usd.current_balance, Decimal('1000') + Decimal('0.1'))

    def test_same_branch_hc_conversion_unaffected(self):
        """تحويل محلي↔عملة صعبة داخل نفس الفرع (الميزة القديمة) يبقى يعمل كما هو."""
        self.client.force_login(self.user_a)
        resp = self._transfer(self.branch_a_local.id, self.branch_a_usd.id, '500', to_amount='0.1', exchange_rate='5000')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'])


class TransferCancelTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch_a = Branch.objects.create(tenant=self.tenant, name='Branch A')
        self.hq_local = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=False)
        self.branch_a_local = Treasury.objects.get(tenant=self.tenant, branch=self.branch_a, is_hard_currency=False)
        self.hq_local.current_balance = Decimal('1000')
        self.hq_local.save(update_fields=['current_balance'])
        self.branch_a_local.current_balance = Decimal('500')
        self.branch_a_local.save(update_fields=['current_balance'])

    def test_cancel_reverses_balances_on_both_sides(self):
        transfer = post_treasury_transfer(
            tenant=self.tenant, from_treasury=self.branch_a_local, to_treasury=self.hq_local,
            from_amount=Decimal('100'), to_amount=Decimal('100'), exchange_rate=Decimal('1'),
            transfer_date=timezone.localdate(), user=self.user,
        )
        self.branch_a_local.refresh_from_db()
        self.hq_local.refresh_from_db()
        self.assertEqual(self.branch_a_local.current_balance, Decimal('400'))
        self.assertEqual(self.hq_local.current_balance, Decimal('1100'))

        cancel_treasury_transfer(transfer, user=self.user)
        self.branch_a_local.refresh_from_db()
        self.hq_local.refresh_from_db()
        self.assertEqual(self.branch_a_local.current_balance, Decimal('500'))
        self.assertEqual(self.hq_local.current_balance, Decimal('1000'))

        transfer.refresh_from_db()
        self.assertTrue(transfer.is_cancelled)
        self.assertIsNotNone(transfer.cancelled_at)
        # الحركتان الأصليتان تبقيان كما هما — لا حذف ولا تعديل، أُضيفت حركتان جديدتان فقط
        self.assertEqual(transfer.from_movement.amount, Decimal('100'))
        self.assertEqual(transfer.to_movement.amount, Decimal('100'))

    def test_double_cancel_is_rejected(self):
        transfer = post_treasury_transfer(
            tenant=self.tenant, from_treasury=self.branch_a_local, to_treasury=self.hq_local,
            from_amount=Decimal('100'), to_amount=Decimal('100'), exchange_rate=Decimal('1'),
            transfer_date=timezone.localdate(), user=self.user,
        )
        cancel_treasury_transfer(transfer, user=self.user)
        with self.assertRaises(ValueError):
            cancel_treasury_transfer(transfer, user=self.user)

    def test_cancel_fails_gracefully_if_destination_already_spent(self):
        transfer = post_treasury_transfer(
            tenant=self.tenant, from_treasury=self.branch_a_local, to_treasury=self.hq_local,
            from_amount=Decimal('100'), to_amount=Decimal('100'), exchange_rate=Decimal('1'),
            transfer_date=timezone.localdate(), user=self.user,
        )
        # يُصرف كل رصيد الإدارة المركزية (1100) في مكان آخر قبل محاولة الإلغاء
        self.hq_local.current_balance = Decimal('0')
        self.hq_local.save(update_fields=['current_balance'])

        with self.assertRaises(ValueError):
            cancel_treasury_transfer(transfer, user=self.user)
        transfer.refresh_from_db()
        self.assertFalse(transfer.is_cancelled)

    def test_cancel_api_respects_branch_ownership(self):
        other_branch = Branch.objects.create(tenant=self.tenant, name='Branch C')
        outsider = User.objects.create_user(
            username='outsider', password='secret123', tenant=self.tenant,
            branch=other_branch, is_branch_supervisor=True,
        )
        transfer = post_treasury_transfer(
            tenant=self.tenant, from_treasury=self.branch_a_local, to_treasury=self.hq_local,
            from_amount=Decimal('100'), to_amount=Decimal('100'), exchange_rate=Decimal('1'),
            transfer_date=timezone.localdate(), user=self.user,
        )
        self.client.force_login(outsider)
        resp = self.client.post(f'/treasury/api/transfer/{transfer.id}/cancel/')
        self.assertEqual(resp.status_code, 404)

        self.client.force_login(self.user)  # owner: request.branch=None, enforce_branch_ownership no-ops
        resp = self.client.post(f'/treasury/api/transfer/{transfer.id}/cancel/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
