"""سعر السوق المرجعي وتنبيه المشتركين المفعّلين للعملة الصعبة."""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core import market_rates
from apps.core.models import GlobalFxRate, MarketExchangeRate
from apps.core.test_utils import TenantTestCase
from apps.notifications.models import Notification

CNY = {'CNY': Decimal('7.2')}


def _hard(tenant, rate, currency='USD', mode=True):
    tenant.hard_currency_mode = mode
    tenant.hard_currency = currency
    tenant.exchange_rate = Decimal(rate)
    tenant.save()


class MarketRateDerivationTests(TenantTestCase):
    def test_usd_manual_and_others_derived_from_it(self):
        GlobalFxRate.objects.create(currency='CNY', per_usd=Decimal('7.2'))
        saved, missing = market_rates.set_market_usd_rate(9000)
        by = {r.currency: r for r in saved}
        self.assertEqual(by['USD'].rate, Decimal('9000.00'))
        self.assertEqual(by['USD'].source, 'manual')
        self.assertEqual(by['SAR'].rate, Decimal('2400.00'))          # 9000 / 3.75
        self.assertEqual(by['AED'].rate, Decimal('2450.65'))          # 9000 / 3.6725
        self.assertEqual(by['CNY'].rate, Decimal('1250.00'))          # 9000 / 7.2
        self.assertEqual(by['SAR'].source, 'derived')
        self.assertEqual(missing, ['EGP'])                            # لا سعر عالمي مخزّن له بعد

    def test_saving_never_touches_the_network(self):
        """الحفظ كان يؤخر نصف دقيقة بانتظار مصدر خارجي: ممنوع أي استدعاء شبكة فيه."""
        with mock.patch.object(market_rates.urllib.request, 'urlopen',
                               side_effect=AssertionError('network call during save')):
            saved, missing = market_rates.set_market_usd_rate(9000)
        self.assertEqual({r.currency for r in saved}, {'USD', 'SAR', 'AED'})
        self.assertEqual(set(missing), {'CNY', 'EGP'})

    def test_refresh_stores_global_rates_for_later_saves(self):
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={**CNY, 'EGP': Decimal('50')}):
            market_rates.refresh_global_rates()
        saved, missing = market_rates.set_market_usd_rate(9000)
        self.assertEqual(missing, [])
        self.assertEqual({r.currency: r.rate for r in saved}['EGP'], Decimal('180.00'))

    def test_failed_refresh_keeps_previous_global_rates(self):
        GlobalFxRate.objects.create(currency='CNY', per_usd=Decimal('7.2'))
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={}):
            market_rates.refresh_global_rates()
        self.assertEqual(GlobalFxRate.objects.get(currency='CNY').per_usd, Decimal('7.200000'))

    def test_saving_twice_same_day_updates_instead_of_duplicating(self):
        market_rates.set_market_usd_rate(9000)
        market_rates.set_market_usd_rate(9100)
        self.assertEqual(MarketExchangeRate.objects.filter(currency='USD').count(), 1)
        self.assertEqual(MarketExchangeRate.objects.get(currency='USD').rate, Decimal('9100.00'))

    def test_non_positive_rate_rejected(self):
        with self.assertRaises(ValueError):
            market_rates.set_market_usd_rate(0)


class ExchangeRateAlertTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        _hard(self.tenant, '8400')
        market_rates.set_market_usd_rate(9000)

    def _alerts(self):
        return Notification.objects.filter(tenant=self.tenant, notification_type='exchange_rate')

    def test_deviating_tenant_gets_one_alert_per_day(self):
        notified = market_rates.generate_exchange_rate_notifications()
        self.assertEqual([t for t, _ in notified], [self.tenant])
        n = self._alerts().get()
        self.assertEqual(n.priority, 'high')                          # +7.1% ≥ 5%
        self.assertIn('9,000.00', n.message)                           # صيغة النظام: 9,000.00
        self.assertIn('8,400.00', n.message)
        self.assertEqual(n.link, '/settings/exchange-rate/')
        self.assertEqual(market_rates.generate_exchange_rate_notifications(), [])
        self.assertEqual(self._alerts().count(), 1)

    def test_tenant_within_threshold_is_not_alerted(self):
        _hard(self.tenant, '8900')                                    # +1.1%
        self.assertEqual(market_rates.generate_exchange_rate_notifications(), [])
        self.assertFalse(self._alerts().exists())

    def test_hard_currency_off_or_expired_is_not_alerted(self):
        _hard(self.tenant, '8400', mode=False)
        self.assertEqual(market_rates.generate_exchange_rate_notifications(), [])
        _hard(self.tenant, '8400')
        self.tenant.subscription_expires = timezone.localdate() - timezone.timedelta(days=1)
        self.tenant.save()
        self.assertEqual(market_rates.generate_exchange_rate_notifications(), [])

    def test_uses_the_tenants_own_currency(self):
        _hard(self.tenant, '2400', currency='SAR')                    # السوق 2400 → لا فرق
        self.assertEqual(market_rates.generate_exchange_rate_notifications(), [])
        _hard(self.tenant, '2000', currency='SAR')                    # +20%
        self.assertEqual(len(market_rates.generate_exchange_rate_notifications()), 1)

    def test_no_reference_for_today_means_no_alert(self):
        MarketExchangeRate.objects.all().delete()
        self.assertEqual(market_rates.generate_exchange_rate_notifications(), [])

    def test_dry_run_creates_nothing(self):
        self.assertEqual(len(market_rates.generate_exchange_rate_notifications(dry_run=True)), 1)
        self.assertFalse(self._alerts().exists())

    def test_command_alerts_and_warns_when_rate_missing(self):
        out = StringIO()
        call_command('check_exchange_rates', stdout=out)
        self.assertEqual(self._alerts().count(), 1)
        MarketExchangeRate.objects.all().delete()
        Notification.objects.all().delete()
        err = StringIO()
        call_command('check_exchange_rates', stderr=err)
        self.assertIn('لا يوجد سعر مرجعي', err.getvalue())
        self.assertFalse(self._alerts().exists())


class AdminExchangeRatePageTests(TenantTestCase):
    def test_tenant_admin_cannot_open_platform_page(self):
        r = self.client.get(reverse('core:admin_exchange_rates'))
        self.assertEqual(r.status_code, 302)

    def test_superuser_saves_rate_and_sends_alerts(self):
        _hard(self.tenant, '8400')
        su = User.objects.create_superuser(username='root-xr', password='secret123')
        self.client.force_login(su)
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={}):
            r = self.client.post(reverse('core:admin_exchange_rates'), {'action': 'save', 'usd_rate': '9000'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(MarketExchangeRate.objects.get(currency='USD').rate, Decimal('9000.00'))
        page = self.client.get(reverse('core:admin_exchange_rates'))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context['deviating']), 1)
        self.client.post(reverse('core:admin_exchange_rates'), {'action': 'send'})
        self.assertEqual(Notification.objects.filter(notification_type='exchange_rate').count(), 1)

    def test_invalid_rate_is_rejected(self):
        su = User.objects.create_superuser(username='root-xr2', password='secret123')
        self.client.force_login(su)
        self.client.post(reverse('core:admin_exchange_rates'), {'action': 'save', 'usd_rate': 'abc'})
        self.assertFalse(MarketExchangeRate.objects.exists())

    def test_tenant_page_offers_todays_market_rate(self):
        _hard(self.tenant, '8400')
        market_rates.set_market_usd_rate(9000)
        r = self.client.get(reverse('core:exchange_rate_page'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['market'].rate, Decimal('9000.00'))
        self.assertContains(r, 'سعر السوق اليوم')
        self.assertContains(r, '9,000.00')


# ── الاقتراح الآلي من خلاصة الأخبار ─────────────────────────────────────
BODY = ('سجل الدولار الأميركي ارتفاعاً جديداً في السوق الموازي بالسودان، متجاوزاً حاجز 9000 جنيه. '
        'وبلغ متوسط سعر بيع الدولار 9004.27 جنيهات، مقابل 8900 جنيه للشراء، وسط تفاوت بين التجار.')


def _feed(items):
    from email.utils import format_datetime
    rows = ''.join(
        f'<item><title>{t}</title><link>{link}</link><pubDate>{format_datetime(p)}</pubDate>'
        f'<content:encoded><![CDATA[<p>{b}</p>]]></content:encoded></item>' for t, link, p, b in items)
    return ('<?xml version="1.0"?><rss xmlns:content="http://purl.org/rss/1.0/modules/content/">'
            f'<channel>{rows}</channel></rss>').encode()


def _serve(xml):
    resp = mock.MagicMock()
    resp.__enter__.return_value.read.return_value = xml
    return mock.patch.object(market_rates.urllib.request, 'urlopen', return_value=resp)


class MarketSuggestionTests(TenantTestCase):
    def _item(self, title='الدولار يتجاوز 9000 جنيه في السوق الموازي', link='https://x.test/1',
              age_hours=10, body=BODY):
        return (title, link, timezone.now() - timezone.timedelta(hours=age_hours), body)

    def test_reads_sell_and_buy_and_notifies_platform_staff(self):
        from apps.core.models import AdminNotification, MarketRateSuggestion
        with _serve(_feed([self._item()])):
            s = market_rates.fetch_market_suggestion()
        self.assertEqual((s.usd_sell, s.usd_buy), (Decimal('9004.27'), Decimal('8900')))
        self.assertEqual(MarketRateSuggestion.objects.count(), 1)
        n = AdminNotification.objects.get(notification_type='exchange_rate')
        self.assertIn('9,004.27', n.title)
        self.assertIn('9,004.27', n.message)
        self.assertEqual(n.link, '/system/exchange-rates/')
        # لا يصل المشتركين شيء، ولا يتغير أي سعر معتمد
        self.assertFalse(Notification.objects.filter(notification_type='exchange_rate').exists())
        self.assertFalse(MarketExchangeRate.objects.exists())

    def test_same_article_is_not_proposed_twice(self):
        with _serve(_feed([self._item()])):
            self.assertIsNotNone(market_rates.fetch_market_suggestion())
            self.assertIsNone(market_rates.fetch_market_suggestion())

    def test_ignores_customs_dollar_old_and_unrelated_articles(self):
        items = [
            self._item(title='ارتفاع الدولار الجمركي في السوق الموازي', link='https://x.test/c'),
            self._item(link='https://x.test/old', age_hours=200),
            self._item(title='خبر رياضي', link='https://x.test/s', body='مباراة'),
            self._item(link='https://x.test/nonum', body='ارتفع الدولار في السوق الموازي بلا أرقام'),
        ]
        with _serve(_feed(items)):
            self.assertIsNone(market_rates.fetch_market_suggestion())

    def test_reading_far_from_last_confirmed_rate_is_discarded(self):
        MarketExchangeRate.objects.create(currency='USD', rate=Decimal('3000'), rate_date=timezone.localdate())
        with _serve(_feed([self._item()])):                            # 9004 مقابل 3000: خطأ قراءة محتمل
            self.assertIsNone(market_rates.fetch_market_suggestion())

    def test_inconsistent_buy_sell_is_discarded(self):
        body = BODY.replace('8900 جنيه للشراء', '100 جنيه للشراء')
        with _serve(_feed([self._item(body=body)])):
            self.assertIsNone(market_rates.fetch_market_suggestion())

    def test_feed_failure_returns_none(self):
        with mock.patch.object(market_rates.urllib.request, 'urlopen', side_effect=OSError('down')):
            self.assertIsNone(market_rates.fetch_market_suggestion())

    def test_newer_suggestion_supersedes_pending_one(self):
        from apps.core.models import MarketRateSuggestion
        with _serve(_feed([self._item(link='https://x.test/a', age_hours=30)])):
            market_rates.fetch_market_suggestion()
        with _serve(_feed([self._item(link='https://x.test/b', age_hours=2, body=BODY.replace('9004.27', '9100'))])):
            market_rates.fetch_market_suggestion()
        self.assertEqual(MarketRateSuggestion.objects.get(status='pending').source_url, 'https://x.test/b')
        self.assertEqual(MarketRateSuggestion.objects.filter(status='dismissed').count(), 1)


class SuggestionReviewViewTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        _hard(self.tenant, '8400')
        self.su = User.objects.create_superuser(username='root-sg', password='secret123')
        self.client.force_login(self.su)
        with _serve(_feed([(
                'x', 'https://x.test/s', timezone.now() - timezone.timedelta(hours=3), BODY)])):
            self.s = market_rates.fetch_market_suggestion()

    def _post(self, **data):
        return self.client.post(reverse('core:admin_exchange_rates'), data)

    def test_page_shows_pending_suggestion(self):
        page = self.client.get(reverse('core:admin_exchange_rates'))
        self.assertEqual(page.context['suggestion'].pk, self.s.pk)
        self.assertContains(page, 'اعتمد هذا السعر')
        self.assertContains(page, '9,004.27')                          # لا 9004.27 ولا 9,004

    def test_accepting_sets_the_reference_but_does_not_alert_tenants_yet(self):
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={}):
            self._post(action='accept_suggestion', suggestion_id=self.s.pk)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, 'accepted')
        self.assertEqual(MarketExchangeRate.objects.get(currency='USD').rate, Decimal('9004.27'))
        self.assertFalse(Notification.objects.filter(notification_type='exchange_rate').exists())
        # الإرسال خطوة منفصلة بيد المشرف (أو أمر الظهر)
        self._post(action='send')
        self.assertEqual(Notification.objects.filter(notification_type='exchange_rate').count(), 1)

    def test_dismissing_changes_nothing_else(self):
        self._post(action='dismiss_suggestion', suggestion_id=self.s.pk)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, 'dismissed')
        self.assertFalse(MarketExchangeRate.objects.exists())

    def test_manual_fetch_button(self):
        with _serve(_feed([])):
            r = self._post(action='fetch')
        self.assertEqual(r.status_code, 302)

    def test_non_platform_user_cannot_accept(self):
        self.client.force_login(self.user)                              # مدير مشترك
        self._post(action='accept_suggestion', suggestion_id=self.s.pk)
        self.s.refresh_from_db()
        self.assertEqual(self.s.status, 'pending')
