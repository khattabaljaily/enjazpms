from decimal import Decimal

from django.test import TestCase

from apps.accounts.models import User
from apps.core.models import BusinessType, Tenant
from apps.items.models import Item
from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine
from apps.purchases.services import confirm_purchase_invoice, edit_confirmed_purchase_invoice
from apps.stocks.models import Stock, StockQuantity
from apps.suppliers.models import Supplier


class PurchaseInvoiceEditTests(TestCase):
    def setUp(self):
        self.business_type = BusinessType.objects.create(
            name='retail',
            name_ar='متجر',
            slug='retail',
        )
        self.tenant = Tenant.objects.create(
            name='Purchase Test Tenant',
            slug='purchase-test-tenant',
            business_type=self.business_type,
            hard_currency_mode=False,
            currency='SDG',
        )
        self.user = User.objects.create_user(
            username='purchase-admin',
            password='secret123',
            tenant=self.tenant,
            is_tenant_admin=True,
        )
        self.supplier = Supplier.objects.create(
            tenant=self.tenant,
            name='Supplier X',
            currency='SDG',
            opening_balance=Decimal('0.00'),
        )
        self.stock = Stock.objects.create(
            tenant=self.tenant,
            name='Main Stock',
            code='WH-001',
        )
        self.item = Item.objects.create(
            tenant=self.tenant,
            name='Test Item',
            sku='SKU-1',
            cost_price=Decimal('10.00'),
            selling_price=Decimal('10.00'),
            tax_rate=Decimal('0'),
        )

    def test_edit_confirmed_purchase_invoice_reduces_stock_by_difference(self):
        invoice = PurchaseInvoice.objects.create(
            tenant=self.tenant,
            supplier=self.supplier,
            stock=self.stock,
            invoice_date='2026-06-27',
            status='draft',
            payment_method='credit',
        )
        line = PurchaseInvoiceLine(
            tenant=self.tenant,
            invoice=invoice,
            item=self.item,
            quantity=Decimal('5'),
            unit_cost=Decimal('10.00'),
            tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])

        confirm_purchase_invoice(invoice, self.user)

        stock_qty = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.item)
        self.assertEqual(stock_qty.quantity, Decimal('5.0000'))

        edit_confirmed_purchase_invoice(
            invoice,
            {},
            [{
                'item_id': self.item.id,
                'quantity': '3',
                'unit_cost': '10.00',
                'tax_rate': '0',
                'unit_id': None,
                'unit_factor': '1',
            }],
            self.user,
        )

        stock_qty.refresh_from_db()
        self.assertEqual(stock_qty.quantity, Decimal('3.0000'))

        movement = self.item.stock_movements.filter(
            tenant=self.tenant,
            reference_type='purchase_invoice_edit',
            reference_id=invoice.id,
        ).latest('id')
        self.assertIn('تعديل أمر شراء بعد التحرير', movement.notes)
        self.assertIn('تقليل', movement.notes)
        self.assertNotIn('2.0000', movement.notes)
        self.assertNotIn('1.0000', movement.notes)
