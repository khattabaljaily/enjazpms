"""جدول أرصدة المخزون: الترتيب بالضغط على رأس العمود يجب أن يُطبَّق في الخادم."""
from django.urls import reverse

from apps.core.test_utils import TenantTestCase, make_item


class QuantitiesTableOrderingTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        for name, qty in (('ب', '5'), ('أ', '9'), ('ج', '1')):
            self.set_quantity(make_item(self.tenant, name=name), self.stock, qty)

    def _names(self, **params):
        resp = self.client.get(reverse('stocks:quantities_table_api'), params)
        return [r['item_name'] for r in resp.json()['data']]

    def _order(self, col, direction, stock=True):
        p = {'order[0][column]': col, 'order[0][dir]': direction}
        if stock:
            p['stock_id'] = self.stock.id
        return p

    def test_order_by_name_single_stock(self):
        self.assertEqual(self._names(**self._order(0, 'asc')), ['أ', 'ب', 'ج'])
        self.assertEqual(self._names(**self._order(0, 'desc')), ['ج', 'ب', 'أ'])

    def test_order_by_quantity_single_stock(self):
        self.assertEqual(self._names(**self._order(5, 'asc')), ['ج', 'ب', 'أ'])
        self.assertEqual(self._names(**self._order(5, 'desc')), ['أ', 'ب', 'ج'])

    def test_order_by_quantity_all_stocks(self):
        self.assertEqual(self._names(**self._order(5, 'asc', stock=False)), ['ج', 'ب', 'أ'])
        self.assertEqual(self._names(**self._order(7, 'desc', stock=False)), ['أ', 'ب', 'ج'])

    def test_invalid_order_column_is_ignored(self):
        self.assertEqual(len(self._names(**self._order(99, 'asc'))), 3)
