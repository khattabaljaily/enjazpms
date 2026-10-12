"""
سعر السوق المرجعي للعملة الصعبة + تنبيه المشتركين عند تغيّره.

لا يوجد مصدر آلي موثوق لسعر السوق الموازي في السودان، لذا تُدخل إدارة المنصة سعر
الدولار يوميًا، وتُشتق منه بقية العملات بأسعارها العالمية مقابل الدولار. ثم يقارن
`generate_exchange_rate_notifications` هذا السعر بسعر كل مشترك مفعّل للعملة الصعبة
وينبّه مدير نشاطه إذا اختلفا. لا يغيّر سعر أي مشترك تلقائيًا: تغييره يعيد تسعير
أصنافه، فالقرار له.
"""
import hashlib
import html
import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, time, timedelta
from email.utils import parsedate_to_datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .constants import CURRENCY_AR
from .models import AdminNotification, MarketExchangeRate, MarketRateSuggestion, Tenant

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


def fmt_money(value):
    """صيغة الأرقام في النظام: 9,000.00"""
    return f'{Decimal(str(value)):,.2f}'


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
                f'سعر السوق اليوم لكل 1 {name}: {fmt_money(ref.rate)} ج.س، بينما سعر الصرف المعتمد في '
                f'نظامك {fmt_money(tenant.exchange_rate)} ج.س (فرق {pct:+.1f}%). '
                f'حدّث السعر ليُعاد تسعير أصنافك وتبقى هوامش ربحك دقيقة.'
            ),
            link='/settings/exchange-rate/',
        )
    return notified


# ══════════════════════════════════════════════════════════════
# اقتراح آلي لسعر السوق (للمشرف فقط — لا يصل المشتركين منه شيء)
# ══════════════════════════════════════════════════════════════
# خلاصات RSS تحمل نص المقال كاملًا. المقال اليومي يذكر «متوسط سعر بيع الدولار X ... Y للشراء».
SUGGESTION_FEEDS = [('تاق برس', 'https://www.tagpress.net/feed')]
SUGGESTION_MAX_AGE = timedelta(hours=72)
SUGGESTION_MAX_JUMP = Decimal('0.30')   # أي قراءة تبعد أكثر من 30% عن آخر سعر معتمد تُهمَل كخطأ قراءة
_NUM = r'([\d][\d,]*(?:\.\d+)?)'
_SELL_RE = re.compile(r'متوسط\s+سعر\s+بيع\s+الدولار[^\d]{0,20}' + _NUM)
_BUY_RE = re.compile(_NUM + r'\s*جنيه[ًا]?\s+للشراء')


def _plain(text):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(text or ''))).strip()


def _num(text):
    return Decimal(text.replace(',', ''))


def parse_feed_items(xml_bytes):
    """[(title, link, published_at, plain_body)] من خلاصة RSS."""
    ns = {'c': 'http://purl.org/rss/1.0/modules/content/'}
    out = []
    for item in ET.fromstring(xml_bytes).findall('.//item'):
        try:
            published = parsedate_to_datetime(item.findtext('pubDate'))
        except (TypeError, ValueError):
            continue
        body = item.findtext('c:encoded', namespaces=ns) or item.findtext('description') or ''
        out.append((_plain(item.findtext('title')), (item.findtext('link') or '').strip(), published, _plain(body)))
    return out


def extract_usd_quote(title, body):
    """(بيع، شراء) من مقال عن السوق الموازي للدولار، أو None إن لم يكن مقالًا صالحًا."""
    text = f'{title} {body}'
    if 'الدولار' not in text or not ('السوق الموازي' in text or 'السوق السوداء' in text):
        return None
    if 'الجمركي' in title:                      # الدولار الجمركي سعر آخر
        return None
    sell = _SELL_RE.search(body)
    if not sell:
        return None
    buy = _BUY_RE.search(body)
    return _num(sell.group(1)), (_num(buy.group(1)) if buy else None)


def last_confirmed_usd():
    row = MarketExchangeRate.objects.filter(currency='USD').order_by('-rate_date').first()
    return row.rate if row else None


def fetch_market_suggestion(now=None, feeds=None):
    """
    يقرأ الخلاصات وينشئ اقتراحًا جديدًا (ويُشعر المشرف) إن وُجد مقال حديث صالح لم يُقرأ من قبل.
    يُرجع الاقتراح الجديد أو None. لا يخمّن: قراءة مشكوك فيها تُهمَل.
    """
    now = now or timezone.now()
    reference = last_confirmed_usd()
    best = None
    for name, url in (feeds or SUGGESTION_FEEDS):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                items = parse_feed_items(resp.read())
        except Exception as exc:
            logger.warning('تعذّر قراءة الخلاصة %s: %s', url, exc)
            continue
        for title, link, published, body in items:
            if not link or now - published > SUGGESTION_MAX_AGE:
                continue
            quote = extract_usd_quote(title, body)
            if quote is None:
                continue
            sell, buy = quote
            if sell <= 0 or (buy is not None and not (sell * Decimal('0.8') <= buy <= sell * Decimal('1.2'))):
                continue
            if reference and abs(sell - reference) / reference > SUGGESTION_MAX_JUMP:
                logger.warning('قراءة مهمَلة (بعيدة جدًا عن آخر سعر معتمد): %s', sell)
                continue
            if best is None or published > best[0]:
                best = (published, name, link, title, sell, buy)
    if best is None:
        return None
    published, name, link, title, sell, buy = best
    key = hashlib.sha1(link.encode('utf-8')).hexdigest()
    if MarketRateSuggestion.objects.filter(source_key=key).exists():
        return None
    with transaction.atomic():
        MarketRateSuggestion.objects.filter(status='pending').update(status='dismissed')
        suggestion = MarketRateSuggestion.objects.create(
            usd_sell=sell, usd_buy=buy, source_name=name, source_url=link, source_key=key,
            headline=title[:300], published_at=published,
        )
        change = ''
        if reference:
            change = f' ({(sell - reference) / reference * 100:+.1f}% عن آخر سعر معتمد {fmt_money(reference)})'
        AdminNotification.push(
            notification_type='exchange_rate',
            title=f'اقتراح سعر الدولار اليوم: {fmt_money(sell)} ج.س',
            message=f'قرأ النظام من {name}: متوسط بيع الدولار {fmt_money(sell)}{change}. راجعه واعتمده أو عدّله، '
                    f'ومن سعرك أنت يُنبَّه المشتركون.',
            link='/system/exchange-rates/', priority='high',
            ref_key=f'xr_suggestion_{suggestion.pk}',
        )
    return suggestion


def pending_suggestion(now=None):
    """أحدث اقتراح بانتظار المراجعة وغير قديم، أو None."""
    now = now or timezone.now()
    s = MarketRateSuggestion.objects.filter(status='pending').order_by('-published_at').first()
    return s if s and now - s.published_at <= SUGGESTION_MAX_AGE else None
