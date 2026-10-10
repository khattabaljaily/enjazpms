"""
أسعار الباقات: مصدر واحد (apps/core/pricing.py) لصفحة الأسعار وتقدير إيرادات المنصة؛
المؤسسات رسوم أساسية + سعر لكل فرع نشط، والسنوي 10 أشهر، والدائم 8 سنوات.
"""
from decimal import Decimal

from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Branch
from apps.core.pricing import annual, enterprise_monthly, perpetual, tenant_monthly_fee
from apps.core.test_utils import TenantTestCase


class PricingRulesTests(TenantTestCase):
    def test_annual_is_ten_months_and_perpetual_is_eight_years(self):
        self.assertEqual(annual(Decimal('25')), Decimal('250'))
        self.assertEqual(perpetual(Decimal('25')), Decimal('2400'))

    def test_enterprise_is_base_plus_per_branch(self):
        self.assertEqual(enterprise_monthly(0), Decimal('90'))
        self.assertEqual(enterprise_monthly(5), Decimal('240'))
        self.assertEqual(enterprise_monthly(10), Decimal('390'))

    def test_tenant_fee_counts_only_active_branches_for_enterprise(self):
        self.tenant.subscription_plan = 'enterprise'
        self.tenant.save(update_fields=['subscription_plan'])
        Branch.objects.create(tenant=self.tenant, name='فرع 1')
        Branch.objects.create(tenant=self.tenant, name='فرع 2')
        Branch.objects.create(tenant=self.tenant, name='فرع مغلق', is_active=False)
        self.assertEqual(tenant_monthly_fee(self.tenant), Decimal('150'))

    def test_tenant_fee_for_flat_plans(self):
        for plan, fee in (('trial', '0'), ('basic', '25'), ('pro', '45')):
            self.tenant.subscription_plan = plan
            self.assertEqual(tenant_monthly_fee(self.tenant), Decimal(fee), plan)


class PricingPageTests(TenantTestCase):
    def test_page_shows_new_prices_and_per_branch_calculator(self):
        self.client.logout()
        page = self.client.get(reverse('core:pricing'))
        self.assertEqual(page.status_code, 200)
        for text in ('$25', '$250', '$2,400', '$45', '$450', '$4,320', '$90', '$30', 'branchCount'):
            self.assertContains(page, text)
        self.assertNotContains(page, '$149')
        self.assertNotContains(page, 'حتى 10 فروع')


class RevenueReportTests(TenantTestCase):
    def test_enterprise_revenue_follows_branch_count(self):
        self.tenant.subscription_plan = 'enterprise'
        self.tenant.save(update_fields=['subscription_plan'])
        for i in range(3):
            Branch.objects.create(tenant=self.tenant, name=f'فرع {i}')
        admin = User.objects.create_superuser(username='pricing-admin', password='x12345678', email='a@example.com')
        self.client.force_login(admin)
        resp = self.client.get(reverse('core:admin_report_revenue'))
        self.assertEqual(resp.status_code, 200)
        enterprise = resp.context['plan_revenue']['enterprise']
        self.assertEqual(enterprise['count'], 1)
        self.assertEqual(enterprise['monthly'], Decimal('180'))
        row = next(r for r in resp.context['tenant_stats'] if r['slug'] == self.tenant.slug)
        self.assertEqual(row['monthly_fee'], Decimal('180'))
