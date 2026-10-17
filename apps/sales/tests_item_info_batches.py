"""شاشة البيع: معلومات المنتج تعيد دفعاته المتاحة (برقمها وتاريخ صلاحيتها) ليظهر التاريخ عند اختيار الدفعة."""
from datetime import date

from django.urls import reverse

from apps.core.test_utils import TenantTestCase, make_item
from apps.items.models import ItemBatch


class ItemInfoBatchesTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, name='دواء بدفعات', track_batch=True, track_expiry=True)

    def _info(self, item=None):
        return self.client.get(reverse('sales:item_info_api'),
                               {'item_id': (item or self.item).pk, 'stock_id': self.stock.pk}).json()['item']

    def _batch(self, number, expiry, remaining='5'):
        return ItemBatch.objects.create(tenant=self.tenant, item=self.item, stock=self.stock, batch_number=number,
                                        expiry_date=expiry, quantity_received=remaining, quantity_remaining=remaining)

    def test_batches_listed_nearest_expiry_first_with_dates(self):
        self._batch('B-LATE', date(2027, 12, 1))
        self._batch('B-SOON', date(2027, 3, 1))
        self._batch('B-EMPTY', date(2027, 1, 1), remaining='0')
        batches = self._info()['batches']
        self.assertEqual([(b['batch_number'], b['expiry_date']) for b in batches],
                         [('B-SOON', '2027-03-01'), ('B-LATE', '2027-12-01')])

    def test_untracked_item_has_no_batches(self):
        plain = make_item(self.tenant, name='عادي')
        self.assertEqual(self._info(plain)['batches'], [])

    def test_form_wires_batch_selection_to_expiry(self):
        html = self.client.get(reverse('sales:invoice_create')).content.decode()
        self.assertIn('onBatchInput', html)
        self.assertIn('datalist', html)
