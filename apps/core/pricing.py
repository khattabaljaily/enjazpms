"""
أسعار الباقات (بالدولار) — المصدر الوحيد لصفحة الأسعار وتقديرات إيرادات المنصة.

- السنوي = 10 أشهر (السعر كما هو؛ لا تُذكر «شهران مجاناً» في صفحة الأسعار).
- الترخيص الدائم: سعر ثابت لكل باقة (PERPETUAL_PRICE). وسعر الفرع الإضافي في المؤسسات
  بقاعدة: الإيجار السنوي × 8 سنوات ثم خصم 5٪. النظام SaaS والاشتراك هو الخيار الطبيعي،
  والدائم متاح لمن يصرّ عليه فقط.
- باقة المؤسسات: رسوم أساسية للإدارة والمخزن المركزي + سعر لكل فرع.
"""
from decimal import Decimal

ANNUAL_MONTHS = 10
PERPETUAL_YEARS = 8
PERPETUAL_DISCOUNT = Decimal('0.05')

PLAN_MONTHLY = {
    'trial': Decimal('0'),
    'basic': Decimal('25'),
    'pro': Decimal('45'),
    'enterprise': Decimal('65'),
}
ENTERPRISE_PER_BRANCH_MONTHLY = Decimal('20')

# الترخيص الدائم (دفعة واحدة). المؤسسات: الرسوم الأساسية فقط، وتُضاف الفروع.
PERPETUAL_PRICE = {
    'basic': Decimal('1900'),
    'pro': Decimal('3420'),
    'enterprise': Decimal('4940'),
}


def annual(monthly):
    return monthly * ANNUAL_MONTHS


def perpetual_from_annual(annual_rent):
    """الدائم = الإيجار السنوي × 8 سنوات − 5٪."""
    return annual_rent * PERPETUAL_YEARS * (1 - PERPETUAL_DISCOUNT)


# سعر الفرع الواحد في الترخيص الدائم لباقة المؤسسات
ENTERPRISE_PER_BRANCH_PERPETUAL = perpetual_from_annual(annual(ENTERPRISE_PER_BRANCH_MONTHLY))


def enterprise_monthly(branches):
    return PLAN_MONTHLY['enterprise'] + ENTERPRISE_PER_BRANCH_MONTHLY * max(int(branches), 0)


def enterprise_perpetual(branches):
    return PERPETUAL_PRICE['enterprise'] + ENTERPRISE_PER_BRANCH_PERPETUAL * max(int(branches), 0)


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
