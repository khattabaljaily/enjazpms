"""
اختبار: تخفيض باقة مشترك موجود بالفعل (TenantForm) يُرفض لو استخدامه الحالي
(فروع/مخازن/مستخدمين) يتجاوز حدود الباقة الجديدة — بدل ما ينزل الحقول
الرقمية بصمت ويترك بيانات "مؤسسات" حية بينما النظام صار يعاملها كنسخة أبسط.
لا حذف تلقائي ولا دمج تخميني: الحسم يدوي بيد مدير المنصة (يقلّل العدد أولاً).
"""
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.forms import TenantForm
from apps.core.models import Branch, BusinessType, Tenant
from apps.stocks.models import Stock


class TenantPlanDowngradeTests(TestCase):
    def setUp(self):
        self.bt = BusinessType.objects.create(name='bt-downgrade', name_ar='نوع', slug='bt-downgrade')
        self.terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())

    def _form_data(self, tenant, **overrides):
        start = tenant.subscription_start
        start_date = start.date() if hasattr(start, 'date') else start
        data = {
            'name': tenant.name,
            'business_type': tenant.business_type_id,
            'email': tenant.email,
            'phone': tenant.phone,
            'city': tenant.city,
            'country': tenant.country,
            'address': tenant.address,
            'subscription_plan': tenant.subscription_plan,
            'subscription_start': start_date.isoformat(),
            'subscription_expires': tenant.subscription_expires.isoformat() if tenant.subscription_expires else '',
            'timezone': tenant.timezone,
            'currency': tenant.currency,
            'exchange_rate': str(tenant.exchange_rate),
            # هذه الحقول readonly لا disabled في الواجهة الحقيقية (تُرسَل مع
            # الفورم بقيمتها الحالية المعروضة) — clean() يستبدلها بقيم الباقة
            # الجديدة بعد التحقق؛ هنا فقط لتفادي خطأ "حقل مطلوب" في الاختبار.
            'max_users': tenant.max_users,
            'max_stocks': tenant.max_stocks,
            'max_branches': tenant.max_branches,
        }
        data.update(overrides)
        return data

    def test_downgrade_rejected_when_branches_exceed_new_plan_limit(self):
        tenant = Tenant.objects.create(
            name='Enterprise Co', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        Branch.objects.create(tenant=tenant, name='Branch A')
        Branch.objects.create(tenant=tenant, name='Branch B')

        form = TenantForm(self._form_data(tenant, subscription_plan='pro'), instance=tenant)
        self.assertFalse(form.is_valid())
        self.assertIn('الفروع', str(form.errors))

    def test_downgrade_rejected_when_stocks_exceed_new_plan_limit(self):
        tenant = Tenant.objects.create(
            name='Pro Co', business_type=self.bt, subscription_plan='pro',
            version_type='multi_stock', currency='SDG', max_stocks=5, **self.terms,
        )
        for i in range(3):
            Stock.objects.create(tenant=tenant, name=f'Stock {i}', code=f'ST-{i}')

        form = TenantForm(self._form_data(tenant, subscription_plan='basic'), instance=tenant)
        self.assertFalse(form.is_valid())
        self.assertIn('المخازن', str(form.errors))

    def test_downgrade_rejected_when_users_exceed_new_plan_limit(self):
        tenant = Tenant.objects.create(
            name='Pro Co Users', business_type=self.bt, subscription_plan='pro',
            version_type='single_store', currency='SDG', max_users=15, **self.terms,
        )
        User.objects.create_user(username='owner-u', password='secret123', tenant=tenant, is_tenant_admin=True)
        for i in range(5):
            User.objects.create_user(username=f'emp-{i}', password='secret123', tenant=tenant)

        form = TenantForm(self._form_data(tenant, subscription_plan='basic'), instance=tenant)
        self.assertFalse(form.is_valid())
        self.assertIn('المستخدمين', str(form.errors))

    def test_downgrade_allowed_when_usage_within_new_plan_limits(self):
        tenant = Tenant.objects.create(
            name='Enterprise Co Empty', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        # لا فروع، لا مخازن زايدة، مستخدم واحد فقط (المالك) — التخفيض يجب أن ينجح.
        form = TenantForm(self._form_data(tenant, subscription_plan='basic'), instance=tenant)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.version_type, 'single_store')
        self.assertEqual(saved.max_branches, 0)

    def test_inactive_branches_do_not_count_against_the_new_limit(self):
        tenant = Tenant.objects.create(
            name='Enterprise Co Inactive', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        Branch.objects.create(tenant=tenant, name='Closed Branch', is_active=False)

        form = TenantForm(self._form_data(tenant, subscription_plan='basic'), instance=tenant)
        self.assertTrue(form.is_valid(), form.errors)

    def test_upgrade_is_never_blocked_regardless_of_current_usage(self):
        tenant = Tenant.objects.create(
            name='Basic Co', business_type=self.bt, subscription_plan='basic',
            version_type='single_store', currency='SDG', **self.terms,
        )
        form = TenantForm(self._form_data(tenant, subscription_plan='enterprise'), instance=tenant)
        self.assertTrue(form.is_valid(), form.errors)

    def test_new_tenant_creation_is_never_blocked(self):
        unsaved = Tenant(
            name='Brand New Co', business_type_id=self.bt.id, subscription_plan='basic',
            subscription_start=timezone.localdate(), timezone='Africa/Cairo', currency='SDG',
            exchange_rate=1, max_users=5, max_stocks=1, max_branches=0,
        )
        form = TenantForm(self._form_data(unsaved, subscription_plan='basic'))
        self.assertTrue(form.is_valid(), form.errors)
