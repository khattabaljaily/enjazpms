"""
اختبار Phase 1 من خطة تنفيذ نسخة المؤسسات (ENTERPRISE_IMPLEMENTATION_PLAN.md،
القسم 6.2 و9): إنشاء فرع جديد في tenant من نوع multi_branch يجب أن يُنشئ
تلقائياً خزينتين (محلية + عملة صعبة)، وهذا السلوك يجب أن يكون معزولاً تماماً
عبر tenant.is_enterprise() ولا يؤثر إطلاقاً على single_store/multi_stock.
"""
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.core.models import BusinessType, Branch, Tenant
from apps.treasury.models import Treasury


class BranchTreasuryAutoCreationTests(TestCase):
    def setUp(self):
        self.bt = BusinessType.objects.create(name='bt-branch-tr', name_ar='نوع', slug='bt-branch-tr')
        self.terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())

    def test_enterprise_branch_creates_two_treasuries(self):
        tenant = Tenant.objects.create(
            name='Enterprise Co', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG',
            hard_currency_mode=True, hard_currency='USD', **self.terms,
        )
        branch = Branch.objects.create(tenant=tenant, name='Branch A')

        treasuries = list(Treasury.objects.filter(tenant=tenant, branch=branch))
        self.assertEqual(len(treasuries), 2)
        self.assertTrue(any(not t.is_hard_currency for t in treasuries))
        hc = [t for t in treasuries if t.is_hard_currency]
        self.assertEqual(len(hc), 1)
        self.assertEqual(hc[0].currency, 'USD')

        # الخزينة الافتراضية النظامية الأصلية للـ tenant (create_tenant_defaults)
        # تبقى كما هي تماماً (branch=None) — لا تعديل عليها.
        tenant_default = Treasury.objects.get(tenant=tenant, is_system_default=True)
        self.assertIsNone(tenant_default.branch)

    def test_enterprise_branch_without_hc_mode_creates_only_local_treasury(self):
        tenant = Tenant.objects.create(
            name='Enterprise Co No HC', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG',
            hard_currency_mode=False, **self.terms,
        )
        branch = Branch.objects.create(tenant=tenant, name='Branch A')
        treasuries = list(Treasury.objects.filter(tenant=tenant, branch=branch))
        self.assertEqual(len(treasuries), 1)
        self.assertFalse(treasuries[0].is_hard_currency)

    def test_single_store_branch_creates_no_branch_scoped_treasury(self):
        """
        النسخة الأولى/الثانية لا تملك فروعاً حقيقية أصلاً عبر الواجهة (مقفولة
        بـ Branch.can_add_branch)، لكن هذا الاختبار يتحقق من خط الدفاع نفسه:
        حتى لو أُنشئ سجل Branch مباشرة (تحايل على الواجهة)، isolation
        الإنشاء التلقائي مضمون بـ tenant.is_enterprise() لا بحاجز الواجهة فقط.
        """
        tenant = Tenant.objects.create(
            name='Single Store Co', business_type=self.bt, subscription_plan='basic',
            version_type='single_store', currency='SDG', **self.terms,
        )
        branch = Branch.objects.create(tenant=tenant, name='Irrelevant Branch')
        leaked = Treasury.objects.filter(tenant=tenant, branch=branch).count()
        self.assertEqual(leaked, 0)

        # وتظل خزينة الـ tenant الافتراضية الوحيدة كما كانت قبل هذا التعديل
        treasuries = list(Treasury.objects.filter(tenant=tenant))
        self.assertEqual(len(treasuries), 1)
        self.assertIsNone(treasuries[0].branch)
        self.assertTrue(treasuries[0].is_system_default)

    def test_branch_manager_field_defaults_to_none(self):
        tenant = Tenant.objects.create(
            name='Enterprise Co Mgr', business_type=self.bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **self.terms,
        )
        branch = Branch.objects.create(tenant=tenant, name='Branch A')
        self.assertIsNone(branch.manager)
