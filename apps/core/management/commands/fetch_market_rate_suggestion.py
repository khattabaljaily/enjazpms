"""
Management command: fetch_market_rate_suggestion
يقرأ سعر الدولار الموازي من خلاصة الأخبار ويعرضه على مشرف المنصة كاقتراح (إشعار + بطاقة في
«سعر الصرف المرجعي»). لا يرسل شيئًا للمشتركين ولا يغيّر أي سعر: المشرف يعتمد أو يعدّل.

Cron المقترح (صباحًا بتوقيت الخرطوم، قبل أمر التنبيه الذي يعمل الظهر):
    CRON_TZ=Africa/Khartoum
    0 8 * * *  cd /path/to/enjazpms && .env/bin/python manage.py fetch_market_rate_suggestion
"""
from django.core.management.base import BaseCommand

from apps.core.market_rates import fetch_market_suggestion, fmt_money, refresh_global_rates


class Command(BaseCommand):
    help = 'Read the parallel-market USD rate from news feeds and propose it to platform staff'

    def handle(self, *args, **opts):
        refreshed = refresh_global_rates()   # أسعار اليوان والجنيه المصري العالمية للحفظ السريع لاحقًا
        self.stdout.write(f'الأسعار العالمية المحدَّثة: {", ".join(refreshed) or "لا شيء"}')
        suggestion = fetch_market_suggestion()
        if suggestion is None:
            self.stdout.write('لا يوجد اقتراح جديد (لا مقال حديث صالح أو سبقت قراءته).')
            return
        self.stdout.write(self.style.SUCCESS(
            f'اقتراح جديد: بيع {fmt_money(suggestion.usd_sell)} / شراء '
            f'{fmt_money(suggestion.usd_buy) if suggestion.usd_buy else "—"} — {suggestion.source_name}'))
