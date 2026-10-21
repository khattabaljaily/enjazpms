"""
أسعار الباقات (بالدولار) — المصدر الوحيد لصفحة الأسعار وتقديرات إيرادات المنصة.

- السنوي = 10 أشهر (السعر كما هو؛ لا تُذكر «شهران مجاناً» في صفحة الأسعار).
- الترخيص الدائم لا يُنشر له سعر: النظام SaaS والاشتراك هو المنشور، وأي طلب شراء/ترخيص دائم
  يُتفاوض عليه بسعر خاص (الصفحة تكتفي بعبارة «يرجى التواصل»).
- باقة المؤسسات: رسوم أساسية للإدارة والمخزن المركزي + سعر لكل فرع.
"""
from decimal import Decimal

ANNUAL_MONTHS = 10

PLAN_MONTHLY = {
    'trial': Decimal('0'),
    'basic': Decimal('25'),
    'pro': Decimal('45'),
    'enterprise': Decimal('65'),
}
ENTERPRISE_PER_BRANCH_MONTHLY = Decimal('20')


def annual(monthly):
    return monthly * ANNUAL_MONTHS


def enterprise_monthly(branches):
    return PLAN_MONTHLY['enterprise'] + ENTERPRISE_PER_BRANCH_MONTHLY * max(int(branches), 0)


def tenant_monthly_fee(tenant, branch_count=None):
    """الاشتراك الشهري المقدّر لمشترك؛ المؤسسات حسب عدد فروعه النشطة."""
    plan = tenant.subscription_plan
    if plan == 'enterprise':
        if branch_count is None:
            branch_count = tenant.branches.filter(is_active=True).count()
        return enterprise_monthly(branch_count)
    return PLAN_MONTHLY.get(plan, Decimal('0'))


def usd(amount):
    """$1,234 — بلا كسور لأن كل الأسعار أعداد صحيحة."""
    return f'${int(amount):,}'
