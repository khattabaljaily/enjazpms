"""
اختبارات أمر التصنيع (BOM consumption) — استهلاك المكوّنات، إنتاج الصنف
النهائي، وتوافق التكلفة الناتجة (فحص خطر التقريب رقم 7 من مراجعة الكود:
unit_cost المُقرَّب لأقرب قرش × الكمية يجب ألا يبتعد عن order.cost الدقيقة
بأكثر من فارق تقريب معقول — هذا سلوك متوقَّع في نظام تكاليفه بالقرش، وليس
عطلاً يُصلَح، لكن يجب أن يبقى محدوداً وليس منفلتاً).
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_item
from apps.items.models import BOMLine, BOMRecipe
from apps.stocks.models import ManufacturingOrder, StockQuantity
from apps.stocks.services import cancel_manufacturing_order, confirm_manufacturing_order


class ManufacturingOrderTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.component_a = make_item(self.tenant, name='مكوّن أ', cost_price='3', selling_price='0')
        self.component_b = make_item(self.tenant, name='مكوّن ب', cost_price='7', selling_price='0')
        self.finished = make_item(self.tenant, name='منتج نهائي', cost_price='0', selling_price='50')
        self.set_quantity(self.component_a, self.stock, '100')
        self.set_quantity(self.component_b, self.stock, '100')

        self.recipe = BOMRecipe.objects.create(tenant=self.tenant, item=self.finished)
        BOMLine.objects.create(tenant=self.tenant, recipe=self.recipe, component=self.component_a, quantity=Decimal('2'))
        BOMLine.objects.create(tenant=self.tenant, recipe=self.recipe, component=self.component_b, quantity=Decimal('1'))

    def make_order(self, quantity):
        return ManufacturingOrder.objects.create(
            tenant=self.tenant, recipe=self.recipe, stock=self.stock,
            quantity=Decimal(quantity), order_date=date(2026, 9, 13), status='draft',
        )

    def test_confirm_consumes_components_and_produces_finished_good(self):
        order = self.make_order('10')
        confirm_manufacturing_order(order)

        comp_a = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.component_a)
        comp_b = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.component_b)
        finished = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.finished)

        self.assertEqual(comp_a.quantity, Decimal('80.0000'))   # 100 - 2*10
        self.assertEqual(comp_b.quantity, Decimal('90.0000'))   # 100 - 1*10
        self.assertEqual(finished.quantity, Decimal('10.0000'))

        order.refresh_from_db()
        # التكلفة الكلية = (2*3 + 1*7) * 10 = 130
        self.assertEqual(order.cost, Decimal('130.00'))
        self.assertEqual(order.status, 'confirmed')

    def test_insufficient_component_stock_is_rejected(self):
        order = self.make_order('1000')  # يحتاج 2000 من المكوّن أ، غير متوفر
        with self.assertRaises(ValueError):
            confirm_manufacturing_order(order)
        comp_a = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.component_a)
        self.assertEqual(comp_a.quantity, Decimal('100.0000'), 'يجب ألا يتغيّر أي رصيد عند الرفض')

    def test_cancel_reverses_components_and_finished_good(self):
        order = self.make_order('10')
        confirm_manufacturing_order(order)
        cancel_manufacturing_order(order)

        comp_a = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.component_a)
        comp_b = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.component_b)
        finished = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.finished)

        self.assertEqual(comp_a.quantity, Decimal('100.0000'))
        self.assertEqual(comp_b.quantity, Decimal('100.0000'))
        self.assertEqual(finished.quantity, Decimal('0.0000'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_finished_good_cost_reconciles_within_one_cent_of_total_cost(self):
        # كمية تنتج تقريباً غير نظيف عند القسمة، لاختبار فارق التقريب.
        order = self.make_order('3')
        confirm_manufacturing_order(order)
        order.refresh_from_db()

        from apps.sales.models import StockMovement
        movement = StockMovement.objects.get(
            tenant=self.tenant, reference_type='manufacturing_order',
            reference_id=order.id, direction='in',
        )
        implied_total = (movement.unit_cost * order.quantity).quantize(Decimal('0.01'))
        gap = abs(implied_total - order.cost)
        self.assertLessEqual(
            gap, Decimal('0.01') * order.quantity,
            'فارق التقريب بين (تكلفة الوحدة × الكمية) والتكلفة الكلية الدقيقة يجب أن يبقى محدوداً',
        )
