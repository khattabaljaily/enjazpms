"""
اختبار ارتداد: قفل صف المورد قبل فحص الحد الائتماني في confirm_purchase_invoice
(بدل قراءة غير مُقفَلة) — يثبت أن السلوك الطبيعي بقي صحيحاً بعد إضافة القفل.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_item, make_supplier
from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine
from apps.purchases.services import confirm_purchase_invoice


class SupplierCreditLimitLockingTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, cost_price='10', selling_price='20')
        self.supplier = make_supplier(self.tenant, credit_limit='500')

    def make_credit_purchase(self, qty):
        invoice = PurchaseInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, supplier=self.supplier,
            invoice_date=date(2026, 9, 13), status='draft', payment_method='credit',
        )
        line = PurchaseInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=self.item,
            quantity=Decimal(qty), unit_cost=Decimal('10'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        return invoice

    def test_purchase_within_credit_limit_still_confirms_normally(self):
        invoice = self.make_credit_purchase('40')  # 400 <= 500
        confirm_purchase_invoice(invoice, self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'confirmed')

    def test_second_purchase_exceeding_remaining_credit_is_rejected(self):
        first = self.make_credit_purchase('40')  # 400
        confirm_purchase_invoice(first, self.user)
        second = self.make_credit_purchase('20')  # 200 more → total 600 > 500
        with self.assertRaises(ValueError):
            confirm_purchase_invoice(second, self.user)
