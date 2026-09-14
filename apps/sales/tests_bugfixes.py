"""
اختبارات ارتداد لإصلاحات على confirm_sale_invoice / record_customer_payment:
  - قفل صف العميل قبل فحص الحد الائتماني (بدل قراءة غير مُقفَلة).
  - قفل صف الفاتورة قبل قراءة remaining_amount عند تسجيل دفعة.

اختبار وحيد الخيط لا يستطيع إثبات أن الـ Race Condition نفسها استحالت (هذا
جزء من اختبارات التزامن في المرحلة 4)، لكنه يثبت أن السلوك الطبيعي (حالة
واحدة، بلا تزامن) بقي صحيحاً تماماً بعد إضافة القفل.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_customer, make_item
from apps.sales.models import SaleInvoice, SaleInvoiceLine
from apps.sales.services import confirm_sale_invoice, record_customer_payment


class CreditLimitLockingTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, cost_price='10', selling_price='100')
        self.set_quantity(self.item, self.stock, '100')
        self.customer = make_customer(self.tenant, credit_limit='500')

    def make_credit_sale(self, amount_qty):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, customer=self.customer,
            invoice_date=date(2026, 9, 13), status='draft', payment_method='credit',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=self.item,
            quantity=Decimal(amount_qty), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('10'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        return invoice

    def test_sale_within_credit_limit_still_confirms_normally(self):
        invoice = self.make_credit_sale('4')  # 400 <= 500
        confirm_sale_invoice(invoice, self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'confirmed')

    def test_sale_exceeding_credit_limit_is_still_rejected(self):
        invoice = self.make_credit_sale('6')  # 600 > 500
        with self.assertRaises(ValueError):
            confirm_sale_invoice(invoice, self.user)

    def test_second_sale_exceeding_remaining_credit_is_rejected(self):
        first = self.make_credit_sale('4')  # 400
        confirm_sale_invoice(first, self.user)
        second = self.make_credit_sale('2')  # 200 more → total 600 > 500
        with self.assertRaises(ValueError):
            confirm_sale_invoice(second, self.user)


class CustomerPaymentLockingTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(self.tenant, cost_price='10', selling_price='100')
        self.set_quantity(self.item, self.stock, '10')
        self.customer = make_customer(self.tenant, credit_limit='0')
        self.invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, customer=self.customer,
            invoice_date=date(2026, 9, 13), status='draft', payment_method='credit',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=self.invoice, item=self.item,
            quantity=Decimal('1'), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('10'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        self.invoice.recalculate_totals()
        self.invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        confirm_sale_invoice(self.invoice, self.user)

    def test_payment_within_remaining_amount_succeeds(self):
        record_customer_payment(self.invoice, Decimal('100'), 'cash', date(2026, 9, 13))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.paid_amount, Decimal('100.00'))
        self.assertEqual(self.invoice.remaining_amount, Decimal('0.00'))

    def test_payment_exceeding_remaining_amount_is_rejected(self):
        with self.assertRaises(ValueError):
            record_customer_payment(self.invoice, Decimal('150'), 'cash', date(2026, 9, 13))

    def test_second_payment_exceeding_new_remaining_is_rejected(self):
        record_customer_payment(self.invoice, Decimal('60'), 'cash', date(2026, 9, 13))
        with self.assertRaises(ValueError):
            record_customer_payment(self.invoice, Decimal('60'), 'cash', date(2026, 9, 13))
