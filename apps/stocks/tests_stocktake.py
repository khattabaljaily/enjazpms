"""
اختبارات ارتداد لـ confirm_stocktake: يجب أن يُرفض تطبيق فرق جرد يُنتج
كمية سالبة (مثلاً لأن system_quantity المُسجَّلة عند بدء الجرد أصبحت قديمة
بسبب بيع حدث بعدها وقبل التأكيد)، بدل تطبيقه بصمت وترك الرصيد سالباً.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_item
from apps.stocks.models import Stocktake, StocktakeLine
from apps.stocks.services import confirm_stocktake
from apps.stocks.models import StockQuantity


class StocktakeNegativeGuardTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, name='صنف جرد')
        self.set_quantity(self.item, self.stock, '10')

    def make_stocktake(self, system_quantity, counted_quantity):
        stocktake = Stocktake.objects.create(
            tenant=self.tenant, stock=self.stock, stocktake_date=date(2026, 9, 13), status='draft',
        )
        StocktakeLine.objects.create(
            tenant=self.tenant, stocktake=stocktake, item=self.item,
            system_quantity=Decimal(system_quantity), counted_quantity=Decimal(counted_quantity),
        )
        return stocktake

    def test_confirm_applies_positive_and_negative_diffs_normally(self):
        stocktake = self.make_stocktake('10', '7')
        confirm_stocktake(stocktake)
        sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.item)
        self.assertEqual(sq.quantity, Decimal('7.0000'))
        stocktake.refresh_from_db()
        self.assertEqual(stocktake.status, 'confirmed')

    def test_confirm_rejects_diff_that_would_drive_quantity_negative(self):
        # الجرد سُجِّل بافتراض رصيد 10، لكن بيعاً حدث لاحقاً خفّض الرصيد الفعلي
        # إلى 2 قبل تأكيد الجرد — تطبيق فرق -7 المبني على الرصيد القديم (10)
        # سينتج -5 لو طُبِّق كما هو.
        stocktake = self.make_stocktake('10', '3')
        StockQuantity.objects.filter(tenant=self.tenant, stock=self.stock, item=self.item).update(
            quantity=Decimal('2')
        )

        with self.assertRaises(ValueError):
            confirm_stocktake(stocktake)

        sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.item)
        self.assertEqual(sq.quantity, Decimal('2.0000'), 'يجب ألا يتغيّر الرصيد عند الرفض')
        stocktake.refresh_from_db()
        self.assertEqual(stocktake.status, 'draft', 'يجب ألا يُؤكَّد الجرد عند الرفض')
