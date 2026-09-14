"""
اختبارات ارتداد لتحديث سعر الصرف (exchange_rate_update_api):
  - يجب أن يُسجَّل تغيير السعر مرة واحدة فقط في ExchangeRateHistory.
  - يجب أن يُعاد تسعير أي صنف له سعر بالعملة الصعبة (بيع أو تكلفة أو حد
    أدنى)، وليس فقط الأصناف التي لها سعر بيع بالعملة الصعبة.
  - فاتورة بيع مسودة بأسعار سابقة لتغيّر سعر الصرف يجب أن يُرفض تأكيدها
    (بدل تمريرها بصمت بسعر قديم) — انظر test_stale_draft_sale_invoice_*.
"""
import json
from datetime import date
from decimal import Decimal

from apps.core.models import ExchangeRateHistory
from apps.core.test_utils import TenantTestCase, make_item
from apps.sales.models import SaleInvoice, SaleInvoiceLine
from apps.sales.services import confirm_sale_invoice


class ExchangeRateUpdateTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.exchange_rate = Decimal('1')
        self.tenant.save(update_fields=['hard_currency_mode', 'hard_currency', 'exchange_rate'])

    def post_rate(self, rate):
        return self.client.post(
            '/settings/tenant/api/exchange-rate/',
            data=json.dumps({'exchange_rate': str(rate)}),
            content_type='application/json',
        )

    def test_update_creates_exactly_one_history_entry(self):
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            ExchangeRateHistory.objects.filter(tenant=self.tenant, rate=Decimal('50')).count(), 1,
        )

    def test_item_with_only_cost_price_hc_is_repriced(self):
        item = make_item(
            self.tenant, name='صنف تكلفة فقط',
            cost_price='0', selling_price='0',
            cost_price_hc=Decimal('2'), selling_price_hc=None,
        )
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.cost_price, Decimal('100.00'))

    def test_item_with_only_selling_price_hc_still_repriced(self):
        item = make_item(
            self.tenant, name='صنف بيع فقط',
            cost_price='0', selling_price='0',
            selling_price_hc=Decimal('3'), cost_price_hc=None,
        )
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('150.00'))

    def test_item_with_no_hc_prices_is_untouched(self):
        item = make_item(self.tenant, name='صنف محلي فقط', cost_price='40', selling_price='100')
        response = self.post_rate('50')
        self.assertEqual(response.status_code, 200, response.content)
        item.refresh_from_db()
        self.assertEqual(item.cost_price, Decimal('40.00'))
        self.assertEqual(item.selling_price, Decimal('100.00'))

    def test_stale_draft_sale_invoice_is_rejected_after_rate_change(self):
        item = make_item(self.tenant, cost_price='40', selling_price='100')
        self.set_quantity(item, self.default_stock, '10')

        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.default_stock, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=item,
            quantity=Decimal('1'), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])

        self.post_rate('60')  # يتغيّر سعر الصرف بعد إنشاء المسودة
        # إعادة الجلب من القاعدة (لا اعتماد على tenant المخزَّن في الذاكرة) —
        # يحاكي طلب HTTP منفصل حقيقي كما في الإنتاج.
        invoice = SaleInvoice.objects.get(pk=invoice.pk)

        with self.assertRaisesMessage(ValueError, 'تغيّر سعر الصرف'):
            confirm_sale_invoice(invoice, self.user)

    def test_draft_sale_invoice_confirms_normally_without_rate_change(self):
        item = make_item(self.tenant, cost_price='40', selling_price='100')
        self.set_quantity(item, self.default_stock, '10')

        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.default_stock, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=item,
            quantity=Decimal('1'), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])

        confirm_sale_invoice(invoice, self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'confirmed')

    def test_re_saving_invoice_after_rate_change_clears_the_staleness_guard(self):
        item = make_item(self.tenant, cost_price='40', selling_price='100')
        self.set_quantity(item, self.default_stock, '10')

        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.default_stock, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash',
        )
        line = SaleInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=item,
            quantity=Decimal('1'), unit_price=Decimal('100'),
            cost_price_snapshot=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])

        self.post_rate('60')
        invoice = SaleInvoice.objects.get(pk=invoice.pk)

        # الموظف يراجع الفاتورة ويعيد حفظها (بأسعار محدَّثة من الواجهة) — يُفترض
        # أن ذلك يُحدِّث updated_at ويُزيل حالة التقادم.
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])

        confirm_sale_invoice(invoice, self.user)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'confirmed')
