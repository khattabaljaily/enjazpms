from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.items.views import _apply_hc_prices


def _item(**kw):
    base = dict(cost_price=Decimal('0'), selling_price=Decimal('0'), min_selling_price=Decimal('0'),
                cost_price_hc=None, selling_price_hc=None, min_selling_price_hc=None)
    base.update(kw)
    return SimpleNamespace(**base)


class HardCurrencyFormPricesTests(SimpleTestCase):
    tenant = SimpleNamespace(hard_currency_mode=True, exchange_rate=Decimal('7500'))

    def test_form_save_keeps_the_local_price_the_user_typed(self):
        # 4,000,000 / 7,500 لا يقبل القسمة بدقة؛ كان الحفظ يعيد حسابه من الدولار المقرّب (3,999,999.75).
        item = _item(cost_price=Decimal('4000000'), selling_price=Decimal('5000000'),
                     min_selling_price=Decimal('4800000'),
                     cost_price_hc=Decimal('533.3333'), selling_price_hc=Decimal('666.6667'),
                     min_selling_price_hc=Decimal('640.0000'))
        _apply_hc_prices(item, self.tenant, from_local=True)
        self.assertEqual(item.cost_price, Decimal('4000000'))
        self.assertEqual(item.selling_price, Decimal('5000000'))
        self.assertEqual(item.min_selling_price, Decimal('4800000'))
        self.assertEqual(item.cost_price_hc, Decimal('533.3333'))
        self.assertEqual(item.selling_price_hc, Decimal('666.6667'))

    def test_form_save_derives_hc_from_local_even_if_client_sent_nothing(self):
        item = _item(selling_price=Decimal('7500'))
        _apply_hc_prices(item, self.tenant, from_local=True)
        self.assertEqual(item.selling_price_hc, Decimal('1.0000'))

    def test_default_mode_still_derives_local_from_hc(self):
        item = _item(selling_price=Decimal('1'), selling_price_hc=Decimal('2'))
        _apply_hc_prices(item, self.tenant)
        self.assertEqual(item.selling_price, Decimal('15000.00'))
