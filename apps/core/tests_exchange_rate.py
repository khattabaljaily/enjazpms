"""
اختبارات ارتداد لتحديث سعر الصرف (exchange_rate_update_api):
  - يجب أن يُسجَّل تغيير السعر مرة واحدة فقط في ExchangeRateHistory.
  - يجب أن يُعاد تسعير أي صنف له سعر بالعملة الصعبة (بيع أو تكلفة أو حد
    أدنى)، وليس فقط الأصناف التي لها سعر بيع بالعملة الصعبة.
"""
import json
from decimal import Decimal

from apps.core.models import ExchangeRateHistory
from apps.core.test_utils import TenantTestCase, make_item


class ExchangeRateUpdateTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.exchange_rate = Decimal('1')
        self.tenant.save(update_fields=['hard_currency_mode', 'hard_currency', 'exchange_rate'])

    def post_rate(self, rate):
        return self.client.post(
            '/settings/tenant/api/exchange-rate/',
            data=json.dumps({'exchange_rate': str(rate)}),
            content_type='application/json',
        )

    def test_update_creates_exactly_one_history_entry(self):
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            ExchangeRateHistory.objects.filter(tenant=self.tenant, rate=Decimal('50')).count(), 1,
        )

    def test_item_with_only_cost_price_hc_is_repriced(self):
        item = make_item(
            self.tenant, name='صنف تكلفة فقط',
            cost_price='0', selling_price='0',
            cost_price_hc=Decimal('2'), selling_price_hc=None,
        )
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.cost_price, Decimal('100.00'))

    def test_item_with_only_selling_price_hc_still_repriced(self):
        item = make_item(
            self.tenant, name='صنف بيع فقط',
            cost_price='0', selling_price='0',
            selling_price_hc=Decimal('3'), cost_price_hc=None,
        )
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('150.00'))

    def test_item_with_no_hc_prices_is_untouched(self):
        item = make_item(self.tenant, name='صنف محلي فقط', cost_price='40', selling_price='100')
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.cost_price, Decimal('40.00'))
        self.assertEqual(item.selling_price, Decimal('100.00'))
