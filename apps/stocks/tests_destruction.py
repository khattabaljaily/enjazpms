"""
اختبارات إتلاف المخزون (StockDestruction) — خصم الكمية، تحديث الدفعة
المرتبطة إن وُجدت، رفض الإتلاف الأكبر من المتاح، واستحالة إلغاء سجل مؤكد
(متطلب رقابي: الإتلاف المؤكد نهائي ولا رجعة فيه).
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_item
from apps.items.models import ItemBatch
from apps.stocks.models import StockDestruction, StockDestructionLine, StockQuantity
from apps.stocks.services import cancel_stock_destruction, confirm_stock_destruction


class StockDestructionTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, name='دواء منتهي', cost_price='10', selling_price='20')
        self.set_quantity(self.item, self.stock, '10')

    def make_destruction(self, quantity, batch=None):
        destruction = StockDestruction.objects.create(
            tenant=self.tenant, stock=self.stock, destruction_date=date(2026, 9, 13),
            status='draft', reason='expired',
        )
        StockDestructionLine.objects.create(
            tenant=self.tenant, destruction=destruction, item=self.item, batch=batch,
            quantity=Decimal(quantity), unit_cost_snapshot=Decimal('10'),
        )
        return destruction

    def test_confirm_deducts_quantity_and_creates_movement(self):
        destruction = self.make_destruction('4')
        confirm_stock_destruction(destruction, self.user)

        sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.item)
        self.assertEqual(sq.quantity, Decimal('6.0000'))
        destruction.refresh_from_db()
        self.assertEqual(destruction.status, 'confirmed')
        self.assertIsNotNone(destruction.confirmed_at)

    def test_confirm_also_reduces_linked_batch_remaining(self):
        batch = ItemBatch.objects.create(
            tenant=self.tenant, item=self.item, stock=self.stock,
            batch_number='B-1', expiry_date=date(2026, 1, 1),
            quantity_received=Decimal('10'), quantity_remaining=Decimal('10'),
        )
        destruction = self.make_destruction('4', batch=batch)
        confirm_stock_destruction(destruction, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('6.0000'))

    def test_confirm_rejects_quantity_beyond_available_stock(self):
        destruction = self.make_destruction('20')
        with self.assertRaises(ValueError):
            confirm_stock_destruction(destruction, self.user)
        sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.item)
        self.assertEqual(sq.quantity, Decimal('10.0000'))

    def test_confirmed_destruction_cannot_be_cancelled(self):
        destruction = self.make_destruction('4')
        confirm_stock_destruction(destruction, self.user)
        with self.assertRaises(ValueError):
            cancel_stock_destruction(destruction)

    def test_draft_destruction_can_be_cancelled(self):
        destruction = self.make_destruction('4')
        cancel_stock_destruction(destruction)
        destruction.refresh_from_db()
        self.assertEqual(destruction.status, 'cancelled')
        sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.item)
        self.assertEqual(sq.quantity, Decimal('10.0000'), 'الإلغاء من مسودة لا يجب أن يمسّ المخزون أصلاً')
