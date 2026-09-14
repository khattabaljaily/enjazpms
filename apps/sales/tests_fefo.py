"""
اختبارات FEFO (الأقرب انتهاءً أولاً) — apps/sales/batch_allocation.py.

تغطي: ترتيب الاستهلاك حسب تاريخ الانتهاء، الدفعات بلا تاريخ انتهاء تُستهلك
أخيراً، الكمية غير المغطاة بدفعات مسجَّلة (رصيد افتتاحي) تُقبل دون رفض
العملية، عدم التأثر إطلاقاً على الأصناف غير المتتبَّعة، وعكس/إعادة الاستهلاك
الصحيح عند الإلغاء والمرتجعات (كاملة وجزئية).
"""
from datetime import date, timedelta
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_item
from apps.items.models import ItemBatch
from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine
from apps.purchases.services import confirm_purchase_invoice
from apps.sales.models import SaleInvoice, SaleInvoiceLine, SaleInvoiceLineBatchAllocation, SaleReturn, SaleReturnLine
from apps.sales.services import (
    cancel_sale_invoice,
    cancel_sale_return,
    confirm_sale_invoice,
    confirm_sale_return,
    deliver_sale_invoice,
    edit_confirmed_invoice,
)


class FefoBatchAllocationTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.stock = self.default_stock
        self.item = make_item(
            self.tenant, name='دواء متتبَّع', cost_price='10', selling_price='20',
            track_batch=True, track_expiry=True,
        )
        self.untracked_item = make_item(
            self.tenant, name='صنف عادي', cost_price='10', selling_price='20',
        )

    def make_batch(self, quantity, expiry_date=None, batch_number='', item=None, stock=None):
        item = item or self.item
        stock = stock or self.stock
        qty = Decimal(quantity)
        batch = ItemBatch.objects.create(
            tenant=self.tenant, item=item, stock=stock,
            batch_number=batch_number, expiry_date=expiry_date,
            quantity_received=qty, quantity_remaining=qty,
            purchase_date=date(2026, 1, 1),
        )
        self.set_quantity(item, stock, self.get_total_qty(item, stock) + qty)
        return batch

    def get_total_qty(self, item, stock):
        from apps.stocks.models import StockQuantity
        sq = StockQuantity.objects.filter(tenant=self.tenant, item=item, stock=stock).first()
        return sq.quantity if sq else Decimal('0')

    def make_sale(self, item, quantity, delivery_type='immediate'):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash', delivery_type=delivery_type,
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=item,
            quantity=Decimal(quantity), unit_price=Decimal('20'),
            cost_price_snapshot=Decimal('10'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        return invoice

    # ── الترتيب FEFO ──────────────────────────────────────

    def test_consumes_earliest_expiry_batch_first(self):
        far = self.make_batch('5', date(2027, 1, 1), 'BATCH-FAR')
        near = self.make_batch('5', date(2026, 10, 1), 'BATCH-NEAR')

        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)

        near.refresh_from_db()
        far.refresh_from_db()
        self.assertEqual(near.quantity_remaining, Decimal('2.0000'))
        self.assertEqual(far.quantity_remaining, Decimal('5.0000'))

        line = invoice.lines.get()
        allocations = list(line.batch_allocations.all())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].batch_id, near.id)
        self.assertEqual(allocations[0].quantity, Decimal('3.0000'))

    def test_spans_multiple_batches_in_expiry_order(self):
        near = self.make_batch('2', date(2026, 10, 1), 'BATCH-NEAR')
        mid = self.make_batch('2', date(2026, 12, 1), 'BATCH-MID')
        far = self.make_batch('10', date(2027, 6, 1), 'BATCH-FAR')

        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)

        near.refresh_from_db()
        mid.refresh_from_db()
        far.refresh_from_db()
        self.assertEqual(near.quantity_remaining, Decimal('0.0000'))
        self.assertEqual(mid.quantity_remaining, Decimal('1.0000'))
        self.assertEqual(far.quantity_remaining, Decimal('10.0000'))

        line = invoice.lines.get()
        allocations = {a.batch_id: a.quantity for a in line.batch_allocations.all()}
        self.assertEqual(allocations[near.id], Decimal('2.0000'))
        self.assertEqual(allocations[mid.id], Decimal('1.0000'))
        self.assertNotIn(far.id, allocations)

    def test_batches_without_expiry_are_consumed_last(self):
        no_expiry = self.make_batch('5', None, 'BATCH-NOEXP')
        dated = self.make_batch('5', date(2026, 10, 1), 'BATCH-DATED')

        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)

        no_expiry.refresh_from_db()
        dated.refresh_from_db()
        self.assertEqual(dated.quantity_remaining, Decimal('2.0000'))
        self.assertEqual(no_expiry.quantity_remaining, Decimal('5.0000'))

    # ── الحالات الحدّية ───────────────────────────────────

    def test_untracked_item_creates_no_allocations(self):
        self.set_quantity(self.untracked_item, self.stock, '10')
        invoice = self.make_sale(self.untracked_item, '3')
        confirm_sale_invoice(invoice, self.user)
        line = invoice.lines.get()
        self.assertEqual(line.batch_allocations.count(), 0)

    def test_quantity_beyond_registered_batches_falls_back_to_unlinked_allocation(self):
        # الكمية الإجمالية للمخزن أكبر من مجموع الدفعات المسجّلة — يمثّل رصيداً
        # افتتاحياً أو إدخالاً يدوياً قبل تفعيل تتبع الدفعات لهذا الصنف.
        batch = self.make_batch('2', date(2026, 10, 1), 'BATCH-A')
        self.set_quantity(self.item, self.stock, self.get_total_qty(self.item, self.stock) + Decimal('10'))

        invoice = self.make_sale(self.item, '5')
        confirm_sale_invoice(invoice, self.user)

        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('0.0000'))

        line = invoice.lines.get()
        unlinked = line.batch_allocations.filter(batch__isnull=True).first()
        self.assertIsNotNone(unlinked)
        self.assertEqual(unlinked.quantity, Decimal('3.0000'))

    # ── الإلغاء ────────────────────────────────────────────

    def test_cancel_invoice_restores_batches_fully(self):
        batch = self.make_batch('5', date(2026, 10, 1), 'BATCH-A')
        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('2.0000'))

        cancel_sale_invoice(invoice, self.user, 'اختبار إلغاء')
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('5.0000'))
        line = invoice.lines.get()
        self.assertEqual(line.batch_allocations.count(), 0)

    def test_deferred_sale_only_consumes_batches_at_delivery(self):
        batch = self.make_batch('5', date(2026, 10, 1), 'BATCH-A')
        invoice = self.make_sale(self.item, '3', delivery_type='deferred')
        confirm_sale_invoice(invoice, self.user)

        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('5.0000'), 'الحجز لا يستهلك الدفعات')

        deliver_sale_invoice(invoice, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('2.0000'))

    # ── المرتجعات ────────────────────────────────────────

    def test_partial_return_restores_exact_batch_consumed(self):
        near = self.make_batch('2', date(2026, 10, 1), 'BATCH-NEAR')
        far = self.make_batch('5', date(2027, 1, 1), 'BATCH-FAR')

        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)
        near.refresh_from_db()
        far.refresh_from_db()
        self.assertEqual(near.quantity_remaining, Decimal('0.0000'))
        self.assertEqual(far.quantity_remaining, Decimal('4.0000'))

        line = invoice.lines.get()
        sale_return = SaleReturn.objects.create(
            tenant=self.tenant, original_invoice=invoice,
            return_date=date(2026, 9, 13), refund_method='cash',
        )
        return_line = SaleReturnLine.objects.create(
            tenant=self.tenant, sale_return=sale_return, invoice_line=line,
            item=self.item, returned_quantity=Decimal('1'), unit_price=Decimal('20'),
        )
        confirm_sale_return(sale_return, self.user)

        near.refresh_from_db()
        far.refresh_from_db()
        # أول وحدة استُهلكت من BATCH-NEAR (الأقدم انتهاءً) فيجب أن تعود إليها أولاً
        self.assertEqual(near.quantity_remaining, Decimal('1.0000'))
        self.assertEqual(far.quantity_remaining, Decimal('4.0000'))

        cancel_sale_return(sale_return, self.user)
        near.refresh_from_db()
        far.refresh_from_db()
        self.assertEqual(near.quantity_remaining, Decimal('0.0000'))
        self.assertEqual(far.quantity_remaining, Decimal('4.0000'))
        self.assertEqual(return_line.batch_restorations.count(), 0)

    def test_full_return_then_cancel_return_round_trips_cleanly(self):
        batch = self.make_batch('5', date(2026, 10, 1), 'BATCH-A')
        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)

        line = invoice.lines.get()
        sale_return = SaleReturn.objects.create(
            tenant=self.tenant, original_invoice=invoice,
            return_date=date(2026, 9, 13), refund_method='cash',
        )
        return_line = SaleReturnLine.objects.create(
            tenant=self.tenant, sale_return=sale_return, invoice_line=line,
            item=self.item, returned_quantity=Decimal('3'), unit_price=Decimal('20'),
        )
        confirm_sale_return(sale_return, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('5.0000'))
        self.assertEqual(line.batch_allocations.count(), 0)

        cancel_sale_return(sale_return, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('2.0000'))
        allocations = list(line.batch_allocations.all())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].batch_id, batch.id)
        self.assertEqual(allocations[0].quantity, Decimal('3.0000'))

        # ولإكمال دورة الحياة: إلغاء الفاتورة نفسها بعد إلغاء المرتجع يجب أن
        # يعيد كل شيء للدفعة الأصلية.
        cancel_sale_invoice(invoice, self.user, 'تنظيف بعد الاختبار')
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('5.0000'))

    # ── التعديل ────────────────────────────────────────────

    def test_edit_confirmed_invoice_reallocates_batches_for_new_quantity(self):
        batch = self.make_batch('10', date(2026, 10, 1), 'BATCH-A')
        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('7.0000'))

        line = invoice.lines.get()
        edit_confirmed_invoice(
            invoice,
            header_data={},
            lines_data=[{
                'item_id': self.item.id, 'unit_id': None, 'quantity': Decimal('5'),
                'unit_price': Decimal('20'), 'discount_percent': Decimal('0'),
                'tax_rate': Decimal('0'), 'cost_price_snapshot': Decimal('10'),
                'batch_number': '', 'serial_number': '', 'expiry_date': None,
            }],
            user=self.user,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('5.0000'))
        new_line = invoice.lines.get()
        allocations = list(new_line.batch_allocations.all())
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].quantity, Decimal('5.0000'))

    # ── تكامل حقيقي مع الشراء ────────────────────────────

    def test_batch_created_by_real_purchase_is_consumed_fefo_on_sale(self):
        from apps.treasury.models import Treasury
        Treasury.objects.filter(tenant=self.tenant, is_default=True).update(current_balance=Decimal('1000'))

        purchase = PurchaseInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, invoice_date=date(2026, 9, 1),
            status='draft', payment_method='cash',
        )
        pline = PurchaseInvoiceLine(
            tenant=self.tenant, invoice=purchase, item=self.item,
            quantity=Decimal('8'), unit_cost=Decimal('10'), tax_rate=Decimal('0'),
            batch_number='PO-BATCH-1', expiry_date=date(2026, 11, 1),
        )
        pline.calculate()
        pline.save()
        purchase.recalculate_totals()
        purchase.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        confirm_purchase_invoice(purchase, self.user)

        batch = ItemBatch.objects.get(tenant=self.tenant, item=self.item, batch_number='PO-BATCH-1')
        self.assertEqual(batch.quantity_remaining, Decimal('8.0000'))

        invoice = self.make_sale(self.item, '3')
        confirm_sale_invoice(invoice, self.user)
        batch.refresh_from_db()
        self.assertEqual(batch.quantity_remaining, Decimal('5.0000'))
