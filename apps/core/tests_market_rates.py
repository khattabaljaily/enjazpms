"""سعر السوق المرجعي وتنبيه المشتركين المفعّلين للعملة الصعبة."""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core import market_rates
from apps.core.models import MarketExchangeRate
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
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value=CNY):
            saved, missing = market_rates.set_market_usd_rate(9000)
        by = {r.currency: r for r in saved}
        self.assertEqual(by['USD'].rate, Decimal('9000.00'))
        self.assertEqual(by['USD'].source, 'manual')
        self.assertEqual(by['SAR'].rate, Decimal('2400.00'))          # 9000 / 3.75
        self.assertEqual(by['AED'].rate, Decimal('2450.65'))          # 9000 / 3.6725
        self.assertEqual(by['CNY'].rate, Decimal('1250.00'))          # 9000 / 7.2
        self.assertEqual(by['SAR'].source, 'derived')
        self.assertEqual(missing, ['EGP'])                            # لم يُجلب سعره العالمي

    def test_global_fetch_failure_keeps_usd_and_pegged_currencies_only(self):
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={}):
            saved, missing = market_rates.set_market_usd_rate(9000)
        self.assertEqual({r.currency for r in saved}, {'USD', 'SAR', 'AED'})
        self.assertEqual(set(missing), {'CNY', 'EGP'})

    def test_saving_twice_same_day_updates_instead_of_duplicating(self):
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={}):
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
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={}):
            market_rates.set_market_usd_rate(9000)

    def _alerts(self):
        return Notification.objects.filter(tenant=self.tenant, notification_type='exchange_rate')

    def test_deviating_tenant_gets_one_alert_per_day(self):
        notified = market_rates.generate_exchange_rate_notifications()
        self.assertEqual([t for t, _ in notified], [self.tenant])
        n = self._alerts().get()
        self.assertEqual(n.priority, 'high')                          # +7.1% ≥ 5%
        self.assertIn('9,000', n.message)
        self.assertIn('8,400', n.message)
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
        with mock.patch.object(market_rates, 'fetch_units_per_usd', return_value={}):
            market_rates.set_market_usd_rate(9000)
        r = self.client.get(reverse('core:exchange_rate_page'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['market'].rate, Decimal('9000.00'))
        self.assertContains(r, 'سعر السوق اليوم')
