"""
اختبار ارتداد بسيط على تناسق Tenant.PLAN_LIMITS/PLAN_FEATURES نفسها —
تكميلي لاختبارات التدفقات الكاملة تحت كل نسخة (apps/core/tests_full.py:
enterprise/multi_branch + pro/multi_stock، apps/sales/tests.py:
basic/single_store). يحمي من تعديل مستقبلي يكسر الهرمية المتوقعة بين
الباقات (كل باقة أعلى تشمل كل ما تسمح به الأدنى، ولا تنقص عنها رقمياً)
دون الحاجة لتشغيل التدفقات الكاملة الثلاثة لاكتشاف الخلل.
"""
from django.test import SimpleTestCase

from apps.core.models import Tenant


class PlanLimitsConsistencyTests(SimpleTestCase):
    def test_higher_plans_allow_every_version_type_the_lower_plan_allows(self):
        basic = set(Tenant.PLAN_LIMITS['basic']['allowed_version_types'])
        pro = set(Tenant.PLAN_LIMITS['pro']['allowed_version_types'])
        enterprise = set(Tenant.PLAN_LIMITS['enterprise']['allowed_version_types'])
        self.assertTrue(basic.issubset(pro), 'أي نوع نسخة تتيحه الأساسية يجب أن تتيحه الاحترافية')
        self.assertTrue(pro.issubset(enterprise), 'أي نوع نسخة تتيحه الاحترافية يجب أن تتيحه نسخة المؤسسات')

    def test_numeric_limits_never_decrease_across_plans(self):
        for field in ('max_stocks', 'max_branches', 'max_users'):
            basic_v = Tenant.PLAN_LIMITS['basic'][field]
            pro_v = Tenant.PLAN_LIMITS['pro'][field]
            enterprise_v = Tenant.PLAN_LIMITS['enterprise'][field]
            self.assertLessEqual(basic_v, pro_v, f'{field}: الأساسية أكبر من الاحترافية')
            self.assertLessEqual(pro_v, enterprise_v, f'{field}: الاحترافية أكبر من نسخة المؤسسات')

    def test_single_store_is_allowed_on_every_plan(self):
        for plan in ('basic', 'pro', 'enterprise'):
            self.assertIn(
                'single_store', Tenant.PLAN_LIMITS[plan]['allowed_version_types'],
                f'الباقة {plan} لا تتيح single_store — كل باقة يجب أن تسمح بالإعداد الأبسط',
            )

    def test_plan_features_defined_for_every_subscription_plan_choice(self):
        plan_keys = {choice[0] for choice in Tenant.SUBSCRIPTION_PLANS}
        self.assertEqual(plan_keys, set(Tenant.PLAN_LIMITS.keys()))
        self.assertEqual(plan_keys, set(Tenant.PLAN_FEATURES.keys()))
