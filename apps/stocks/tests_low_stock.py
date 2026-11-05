"""
اختبارات ارتداد: الصنف ذو الحد الأدنى 0 والكمية 0 يجب أن يظهر في تقرير المخزون
المنخفض وفي الإشعارات؛ والحد المعرَّف على المنتج (لا على سجل المخزن) هو المعتمد.
"""
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_item
from apps.notifications.models import Notification
from apps.notifications.services import generate_low_stock_notifications
from apps.stocks.models import StockQuantity
from apps.stocks.reports import StocksReportGenerator


class LowStockZeroThresholdTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock

    def _names(self):
        report = StocksReportGenerator(self.tenant).get_low_stock_report()
        return [row['item_name'] for row in report['data']]

    def test_zero_quantity_with_zero_threshold_is_low(self):
        item = make_item(self.tenant, name='صفر')
        self.set_quantity(item, self.stock, '0')
        self.assertIn('صفر', self._names())
        self.assertTrue(StockQuantity.objects.get(item=item).is_low_stock)
        self.assertEqual(generate_low_stock_notifications(self.tenant), 1)
        self.assertTrue(Notification.objects.filter(notification_type='low_stock').exists())

    def test_positive_quantity_with_zero_threshold_is_not_low(self):
        item = make_item(self.tenant, name='متوفر')
        self.set_quantity(item, self.stock, '5')
        self.assertNotIn('متوفر', self._names())

    def test_item_level_threshold_is_used_by_report(self):
        item = make_item(self.tenant, name='حد المنتج', min_quantity=Decimal('10'))
        self.set_quantity(item, self.stock, '4')
        self.assertIn('حد المنتج', self._names())

    def test_service_items_are_never_low(self):
        item = make_item(self.tenant, name='خدمة', item_type='service')
        StockQuantity.objects.get_or_create(tenant=self.tenant, stock=self.stock, item=item)
        self.assertNotIn('خدمة', self._names())
