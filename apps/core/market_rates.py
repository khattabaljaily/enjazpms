"""
سعر السوق المرجعي للعملة الصعبة + تنبيه المشتركين عند تغيّره.

لا يوجد مصدر آلي موثوق لسعر السوق الموازي في السودان، لذا تُدخل إدارة المنصة سعر
الدولار يوميًا، وتُشتق منه بقية العملات بأسعارها العالمية مقابل الدولار. ثم يقارن
`generate_exchange_rate_notifications` هذا السعر بسعر كل مشترك مفعّل للعملة الصعبة
وينبّه مدير نشاطه إذا اختلفا. لا يغيّر سعر أي مشترك تلقائيًا: تغييره يعيد تسعير
أصنافه، فالقرار له.
"""
import json
import logging
import urllib.request
from datetime import datetime, time
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .constants import CURRENCY_AR
from .models import MarketExchangeRate, Tenant

logger = logging.getLogger(__name__)

# عملات مربوطة بالدولار: نستعمل ربطها الرسمي ولا نعتمد على مصدر خارجي.
PEGGED_PER_USD = {'SAR': Decimal('3.75'), 'AED': Decimal('3.6725')}
# العملات الصعبة التي يختارها المشتركون (forms.TenantForm).
HARD_CURRENCIES = ('USD', 'SAR', 'AED', 'CNY', 'EGP')
CURRENCY_NAME_AR = {
    'USD': 'الدولار', 'SAR': 'الريال السعودي', 'AED': 'الدرهم الإماراتي',
    'CNY': 'اليوان الصيني', 'EGP': 'الجنيه المصري',
}
FX_URL = 'https://open.er-api.com/v6/latest/USD'
DEFAULT_THRESHOLD_PERCENT = Decimal('2')


def fetch_units_per_usd(codes):
    """
    {code: وحدات العملة لكل 1 دولار} للعملات غير المربوطة، من سعر السوق العالمي.
    يُرجع {} عند أي فشل: لا نخمّن سعرًا.
    """
    try:
        with urllib.request.urlopen(FX_URL, timeout=10) as resp:
            rates = json.load(resp).get('rates', {})
    except Exception as exc:  # الشبكة غير مضمونة؛ الفشل لا يوقف إدخال الدولار
        logger.warning('تعذّر جلب الأسعار العالمية: %s', exc)
        return {}
    out = {}
    for code in codes:
        try:
            value = Decimal(str(rates[code]))
        except (KeyError, InvalidOperation):
            continue
        if value > 0:
            out[code] = value
    return out


def units_per_usd(codes):
    """وحدات كل عملة لكل دولار: الربط الرسمي أولًا ثم السعر العالمي. العملة بلا سعر لا تظهر."""
    out = {'USD': Decimal(1)}
    floating = []
    for code in codes:
        if code in out:
            continue
        if code in PEGGED_PER_USD:
            out[code] = PEGGED_PER_USD[code]
        else:
            floating.append(code)
    if floating:
        out.update(fetch_units_per_usd(floating))
    return out


@transaction.atomic
def set_market_usd_rate(usd_rate, user=None, rate_date=None):
    """
    يحفظ سعر الدولار يدويًا ويشتق بقية العملات. يُرجع (الصفوف المحفوظة، العملات التي تعذّر اشتقاقها).
    """
    usd_rate = Decimal(str(usd_rate))
    if usd_rate <= 0:
        raise ValueError('سعر الدولار يجب أن يكون أكبر من صفر')
    rate_date = rate_date or timezone.localdate()

    per_usd = units_per_usd(HARD_CURRENCIES)
    saved, missing = [], []
    for code in HARD_CURRENCIES:
        if code == 'USD':
            value, source = usd_rate, 'manual'
        elif code in per_usd:
            value, source = (usd_rate / per_usd[code]), 'derived'
        else:
            missing.append(code)
            continue
        row, _ = MarketExchangeRate.objects.update_or_create(
            currency=code, rate_date=rate_date,
            defaults={'rate': value.quantize(Decimal('0.01')), 'source': source, 'entered_by': user},
        )
        saved.append(row)
    return saved, missing


def get_reference_rate(currency, rate_date=None):
    """مرجع السوق لعملة في تاريخ معيّن (اليوم افتراضيًا) أو None."""
    return MarketExchangeRate.objects.filter(
        currency=currency, rate_date=rate_date or timezone.localdate(),
    ).first()


def _eligible_tenants():
    qs = Tenant.objects.filter(is_active=True, hard_currency_mode=True)
    return [t for t in qs if t.is_subscription_valid() and t.hard_currency]


def deviation_percent(current, reference):
    """نسبة فرق مرجع السوق عن سعر المشترك (موجبة = السوق أعلى). None إن لم يكن له سعر صالح."""
    current = Decimal(str(current or 0))
    if current <= 0:
        return None
    return (Decimal(str(reference)) - current) / current * 100


def find_deviating_tenants(rate_date=None, threshold=DEFAULT_THRESHOLD_PERCENT, tenant_slug=None):
    """[(tenant, reference_row, pct)] للمشتركين الذين اختلف سعرهم عن السوق بأكثر من العتبة."""
    result = []
    for tenant in _eligible_tenants():
        if tenant_slug and tenant.slug != tenant_slug:
            continue
        ref = get_reference_rate(tenant.hard_currency, rate_date)
        if ref is None:
            continue
        pct = deviation_percent(tenant.exchange_rate, ref.rate)
        if pct is not None and abs(pct) >= Decimal(str(threshold)):
            result.append((tenant, ref, pct))
    return result


def _fmt(value):
    return f'{Decimal(value):,.2f}'.rstrip('0').rstrip('.')


def generate_exchange_rate_notifications(rate_date=None, threshold=DEFAULT_THRESHOLD_PERCENT,
                                         tenant_slug=None, dry_run=False):
    """
    ينشئ إشعارًا واحدًا في اليوم لكل مشترك اختلف سعره عن السوق. يُرجع قائمة (tenant, pct)
    للمشتركين الذين أُنشئ لهم إشعار (أو سيُنشأ في dry_run).
    """
    from apps.notifications.models import Notification

    # نطاق زمني لا __date: على MySQL بلا جداول المناطق الزمنية يرجع __date فارغًا.
    day_start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    notified = []
    for tenant, ref, pct in find_deviating_tenants(rate_date, threshold, tenant_slug):
        already = Notification.objects.filter(
            tenant=tenant, notification_type='exchange_rate', created_at__gte=day_start,
        ).exists()
        if already:
            continue
        notified.append((tenant, pct))
        if dry_run:
            continue
        name = CURRENCY_NAME_AR.get(ref.currency, CURRENCY_AR.get(ref.currency, ref.currency))
        direction = 'ارتفع' if pct > 0 else 'انخفض'
        Notification.objects.create(
            tenant=tenant,
            notification_type='exchange_rate',
            priority='high' if abs(pct) >= 5 else 'medium',
            title=f'تغيّر سعر الصرف: {name} {direction} في السوق',
            message=(
                f'سعر السوق اليوم لكل 1 {name}: {_fmt(ref.rate)} ج.س، بينما سعر الصرف المعتمد في '
                f'نظامك {_fmt(tenant.exchange_rate)} ج.س (فرق {pct:+.1f}%). '
                f'حدّث السعر ليُعاد تسعير أصنافك وتبقى هوامش ربحك دقيقة.'
            ),
            link='/settings/exchange-rate/',
        )
    return notified
