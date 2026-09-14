"""
اختبارات تزامن حقيقية (threads + اتصالات قاعدة بيانات منفصلة فعلياً) تُثبت
أن أقفال select_for_update المُضافة في المرحلة 1 (بق #4/#5) تصمد فعلاً تحت
تنافس حقيقي — وليس فقط منطقاً صحيحاً في اختبار وحيد الخيط.

يجب استخدام TransactionTestCase (لا TestCase العادي) هنا: TestCase يغلّف
كل اختبار في معاملة واحدة تُلغى في النهاية، فكل الـ threads تشارك نفس
المعاملة بلا عزل حقيقي بينها. TransactionTestCase يسمح لكل thread باتصال
ومعاملة منفصلة فعلياً — يحاكي طلبين HTTP متزامنين حقيقيين.
"""
import threading
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import BusinessType, Tenant
from apps.core.test_utils import make_customer, make_item
from apps.sales.models import CustomerLedger, SaleInvoice, SaleInvoiceLine
from apps.sales.services import confirm_sale_invoice, record_customer_payment
from apps.stocks.models import Stock, StockQuantity


class _ConcurrencyTestBase(TransactionTestCase):
    """إعداد يدوي مطابق لـ TenantTestCase لكن فوق TransactionTestCase مباشرة."""

    def setUp(self):
        from django.conf import settings
        business_type = BusinessType.objects.create(
            name=f'test-{self.__class__.__name__.lower()}', name_ar='نشاط اختبار',
            slug=f'bt-{self.__class__.__name__.lower()}',
        )
        self.tenant = Tenant.objects.create(
            name=f'Tenant {self.__class__.__name__}', business_type=business_type,
            subscription_plan='basic', version_type='single_store',
            currency='SDG', hard_currency_mode=False,
            terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now(),
        )
        self.user = User.objects.create_user(
            username=f'admin-{self.__class__.__name__.lower()}', password='secret123',
            tenant=self.tenant, is_tenant_admin=True,
        )
        self.stock = Stock.objects.filter(tenant=self.tenant, is_system_default=True).first()

    def run_concurrently(self, *workers):
        """يُشغّل كل worker في thread منفصل، يبدأون معاً (Barrier)، ثم ينتظر الكل."""
        barrier = threading.Barrier(len(workers))
        errors_by_index = {}

        def wrap(index, fn):
            def target():
                try:
                    barrier.wait(timeout=10)
                    fn()
                except Exception as e:  # noqa: BLE001 — نجمع أي استثناء لتفحصه لاحقاً
                    errors_by_index[index] = e
                finally:
                    connection.close()
            return target

        threads = [threading.Thread(target=wrap(i, w)) for i, w in enumerate(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        return errors_by_index


class CreditLimitConcurrencyTests(_ConcurrencyTestBase):
    def setUp(self):
        super().setUp()
        self.item = make_item(self.tenant, cost_price='40', selling_price='100')
        StockQuantity.objects.filter(tenant=self.tenant, stock=self.stock, item=self.item).update(
            quantity=Decimal('100'), opening_quantity=Decimal('100'),
        )
        self.customer = make_customer(self.tenant, credit_limit='1000')

    def make_draft_invoice(self, quantity):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, customer=self.customer,
            invoice_date=date(2026, 9, 13), status='draft', payment_method='credit',
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
        return invoice.id

    def test_two_concurrent_credit_sales_never_jointly_exceed_the_limit(self):
        # كل فاتورة 600 (6 × 100)؛ الحد 1000 — لا يمكن أن تنجح الاثنتان معاً
        # (600+600=1200 > 1000)، رغم أن كل واحدة منفردة كانت ستُقبل.
        invoice_id_1 = self.make_draft_invoice('6')
        invoice_id_2 = self.make_draft_invoice('6')

        def confirm(invoice_id):
            invoice = SaleInvoice.objects.get(pk=invoice_id)
            confirm_sale_invoice(invoice, self.user)

        errors = self.run_concurrently(
            lambda: confirm(invoice_id_1),
            lambda: confirm(invoice_id_2),
        )

        succeeded = SaleInvoice.objects.filter(
            pk__in=[invoice_id_1, invoice_id_2], status='confirmed'
        ).count()
        self.assertEqual(succeeded, 1, 'يجب أن تُؤكَّد فاتورة واحدة بالضبط')
        self.assertEqual(len(errors), 1, 'يجب أن تُرفض المحاولة الأخرى بـ ValueError')
        for exc in errors.values():
            self.assertIsInstance(exc, ValueError)

        from django.db.models import Sum
        total_posted = CustomerLedger.objects.filter(
            tenant=self.tenant, customer=self.customer
        ).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        self.assertLessEqual(total_posted, self.customer.credit_limit)


class PaymentOverpayConcurrencyTests(_ConcurrencyTestBase):
    def setUp(self):
        super().setUp()
        self.item = make_item(self.tenant, cost_price='40', selling_price='100')
        StockQuantity.objects.filter(tenant=self.tenant, stock=self.stock, item=self.item).update(
            quantity=Decimal('100'), opening_quantity=Decimal('100'),
        )
        self.customer = make_customer(self.tenant, credit_limit='0')
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, customer=self.customer,
            invoice_date=date(2026, 9, 13), status='draft', payment_method='credit',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=self.item,
            quantity=Decimal('1'), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        confirm_sale_invoice(invoice, self.user)  # grand_total = 100
        self.invoice_id = invoice.id

    def test_two_concurrent_payments_never_jointly_overpay_the_invoice(self):
        # المتبقي 100 بالضبط؛ محاولتان بـ 60 كل واحدة (120 > 100) لا يجب أن
        # تنجحا معاً.
        def pay():
            invoice = SaleInvoice.objects.get(pk=self.invoice_id)
            record_customer_payment(invoice, Decimal('60'), 'cash', date(2026, 9, 13))

        errors = self.run_concurrently(pay, pay)

        invoice = SaleInvoice.objects.get(pk=self.invoice_id)
        self.assertLessEqual(invoice.paid_amount, invoice.grand_total)
        self.assertEqual(len(errors), 1, 'يجب أن تُرفض إحدى الدفعتين لتجاوز المتبقي')


class ConcurrentStockDeductionTests(_ConcurrencyTestBase):
    def setUp(self):
        super().setUp()
        self.item = make_item(self.tenant, cost_price='40', selling_price='100')
        StockQuantity.objects.filter(tenant=self.tenant, stock=self.stock, item=self.item).update(
            quantity=Decimal('10'), opening_quantity=Decimal('10'),
        )

    def make_draft_invoice(self, quantity):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash',
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
        return invoice.id

    def test_two_concurrent_sales_never_oversell_available_stock(self):
        # الرصيد 10 فقط؛ فاتورتان بـ 6 وحدات كل واحدة (12 > 10) — لا يجب أن
        # تنجحا معاً، ولا يجب أن ينزل الرصيد تحت الصفر إطلاقاً.
        invoice_id_1 = self.make_draft_invoice('6')
        invoice_id_2 = self.make_draft_invoice('6')

        def confirm(invoice_id):
            invoice = SaleInvoice.objects.get(pk=invoice_id)
            confirm_sale_invoice(invoice, self.user)

        errors = self.run_concurrently(
            lambda: confirm(invoice_id_1),
            lambda: confirm(invoice_id_2),
        )

        succeeded = SaleInvoice.objects.filter(
            pk__in=[invoice_id_1, invoice_id_2], status='confirmed'
        ).count()
        self.assertEqual(succeeded, 1)
        self.assertEqual(len(errors), 1)

        sq = StockQuantity.objects.get(tenant=self.tenant, stock=self.stock, item=self.item)
        self.assertGreaterEqual(sq.quantity, Decimal('0'))
        self.assertEqual(sq.quantity, Decimal('4.0000'))  # 10 - 6 (فاتورة واحدة نجحت فقط)
