"""
Management command: check_exchange_rates
ينبّه المشتركين المفعّلين للعملة الصعبة إذا اختلف سعر صرفهم عن سعر السوق المرجعي لليوم
(الذي تُدخله إدارة المنصة من «سعر الصرف المرجعي»). إشعار واحد كحد أقصى لكل مشترك في اليوم.

Cron المقترح (الظهر بتوقيت الخرطوم، بعد استقرار السوق):
    CRON_TZ=Africa/Khartoum
    0 12 * * *  cd /path/to/enjazpms && .env/bin/python manage.py check_exchange_rates

لا يغيّر أي سعر تلقائيًا، وإن لم يُدخَل سعر اليوم لا يرسل شيئًا ويكتب تحذيرًا.
"""
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.market_rates import (
    DEFAULT_THRESHOLD_PERCENT, HARD_CURRENCIES, generate_exchange_rate_notifications, get_reference_rate,
)


class Command(BaseCommand):
    help = 'Notify hard-currency tenants when their exchange rate drifts from the market reference rate'

    def add_arguments(self, parser):
        parser.add_argument('--threshold', type=Decimal, default=DEFAULT_THRESHOLD_PERCENT,
                            help='الحد الأدنى للفرق بالنسبة المئوية (الافتراضي 2)')
        parser.add_argument('--tenant', type=str, help='slug مشترك واحد فقط')
        parser.add_argument('--date', type=date.fromisoformat, help='تاريخ السعر المرجعي YYYY-MM-DD (الافتراضي اليوم)')
        parser.add_argument('--dry-run', action='store_true', help='يعرض من سيُنبَّه دون إنشاء إشعارات')

    def handle(self, *args, **opts):
        rate_date = opts['date'] or timezone.localdate()
        if not any(get_reference_rate(c, rate_date) for c in HARD_CURRENCIES):
            self.stderr.write(self.style.WARNING(
                f'لا يوجد سعر مرجعي بتاريخ {rate_date}: لم يُرسل أي تنبيه. أدخله من لوحة المنصة (سعر الصرف المرجعي).'))
            return
        notified = generate_exchange_rate_notifications(
            rate_date=rate_date, threshold=opts['threshold'],
            tenant_slug=opts['tenant'], dry_run=opts['dry_run'],
        )
        verb = 'سيُنبَّه' if opts['dry_run'] else 'نُبِّه'
        for tenant, pct in notified:
            self.stdout.write(f'{verb}: {tenant.name} ({tenant.hard_currency}) {pct:+.1f}%')
        self.stdout.write(self.style.SUCCESS(f'{len(notified)} مشترك'))
