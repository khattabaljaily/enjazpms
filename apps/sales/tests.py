from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase

from apps.accounts.models import User
from apps.core.models import BusinessType, Tenant
from apps.customers.models import Customer
from apps.items.models import Item
from apps.sales.models import CustomerLedger, SaleInvoice, SaleInvoiceLine, SalePayment
from apps.sales.services import cancel_sale_invoice, confirm_sale_invoice
from apps.stocks.models import Stock, StockQuantity
from apps.treasury.models import Treasury


class BasicEditionSalesTests(TestCase):
    def setUp(self):
        business_type = BusinessType.objects.create(
            name='retail', name_ar='متجر', slug='basic-test-retail'
        )
        self.tenant = Tenant.objects.create(
            name='Basic Edition Test', slug='basic-edition-test',
            business_type=business_type, version_type='single_store',
            hard_currency_mode=False, currency='SDG',
        )
        self.user = User.objects.create_user(
            username='basic-edition-admin', password='secret123',
            tenant=self.tenant, is_tenant_admin=True,
        )
        self.stock = Stock.objects.create(
            tenant=self.tenant, name='المخزن الأساسي', code='WH-001'
        )
        self.item = Item.objects.create(
            tenant=self.tenant, name='منتج اختبار', sku='BASIC-001',
            cost_price=Decimal('40.00'), selling_price=Decimal('100.00'),
            tax_rate=Decimal('0'),
        )
        StockQuantity.objects.filter(
            tenant=self.tenant, stock=self.stock, item=self.item
        ).update(quantity=Decimal('5.0000'), opening_quantity=Decimal('5.0000'))
        self.customer = Customer.objects.create(
            tenant=self.tenant, name='عميل اختبار', credit_limit=Decimal('1000')
        )
        self.treasury = Treasury.objects.create(
            tenant=self.tenant, name='الخزينة الأساسية', code='TR-001',
            current_balance=Decimal('1000.00'), is_default=True,
        )

    def make_invoice(self, payment_method, quantity, customer=None):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, customer=customer,
            invoice_date='2026-09-13', status='draft',
            payment_method=payment_method,
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=self.item,
            quantity=Decimal(quantity), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        return invoice

    def test_cash_sale_and_cancel_restore_stock_and_treasury(self):
        invoice = self.make_invoice('cash', '2')
        confirm_sale_invoice(invoice, self.user)

        quantity = StockQuantity.objects.get(stock=self.stock, item=self.item)
        self.treasury.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('3.0000'))
        self.assertEqual(self.treasury.current_balance, Decimal('1200.00'))
        self.assertEqual(invoice.payments.filter(is_reversed=False).count(), 1)
        self.assertEqual(invoice.paid_amount, Decimal('200.00'))

        cancel_sale_invoice(invoice, self.user, 'اختبار الإلغاء')
        quantity.refresh_from_db()
        self.treasury.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('5.0000'))
        self.assertEqual(self.treasury.current_balance, Decimal('1000.00'))
        self.assertEqual(invoice.status, 'cancelled')
        self.assertEqual(invoice.paid_amount, Decimal('0'))
        self.assertEqual(invoice.payments.filter(is_reversed=False).count(), 0)

    def test_credit_sale_and_cancel_restore_customer_balance(self):
        invoice = self.make_invoice('credit', '1', self.customer)
        confirm_sale_invoice(invoice, self.user)
        balance = CustomerLedger.objects.filter(
            tenant=self.tenant, customer=self.customer
        ).aggregate(total=Sum('amount'))['total']
        self.assertEqual(balance, Decimal('100.00'))
        self.assertEqual(invoice.paid_amount, Decimal('0.00'))

        cancel_sale_invoice(invoice, self.user, 'اختبار إلغاء الآجل')
        balance = CustomerLedger.objects.filter(
            tenant=self.tenant, customer=self.customer
        ).aggregate(total=Sum('amount'))['total']
        self.assertEqual(balance, Decimal('0.00'))
        self.assertEqual(invoice.status, 'cancelled')

    def test_basic_edition_allows_one_active_stock_only(self):
        self.assertFalse(Stock.can_add_stock(self.tenant))