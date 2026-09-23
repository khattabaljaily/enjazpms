from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase

from apps.accounts.models import User
from apps.bank_accounts.models import BankAccount, BankAccountMovement
from apps.bank_accounts.services import (
    post_bank_account_transfer,
    post_treasury_to_bank_transfer,
)
from apps.core.models import Branch, BusinessType, Tenant
from apps.customers.models import Customer
from apps.expenses.models import Expense, ExpenseCategory
from apps.expenses.services import cancel_expense, confirm_expense
from apps.insurance.models import InsuranceClaim, InsuranceCompany, InsuranceMember
from apps.insurance.services import (
    create_claim_from_sale,
    record_claim_response,
    settle_claim_payment,
    submit_claim,
)
from apps.items.models import Item
from apps.purchases.models import PurchaseInvoice, PurchaseInvoiceLine, PurchaseReturn, PurchaseReturnLine
from apps.purchases.services import cancel_purchase_invoice, confirm_purchase_invoice, confirm_purchase_return
from apps.sales.models import SaleInvoice, SaleInvoiceLine, SaleReturn, SaleReturnLine, StockMovement
from apps.sales.services import cancel_sale_invoice, confirm_sale_invoice, confirm_sale_return
from apps.stocks.models import Stock, StockQuantity, StockTransfer, StockTransferLine
from apps.stocks.services import cancel_stock_transfer, confirm_stock_transfer
from apps.suppliers.models import Supplier
from apps.treasury.models import Treasury, TreasuryMovement
from apps.treasury.services import (
    post_treasury_transfer,
    set_opening_balance,
)


class FullBusinessFlowTests(TestCase):
    def setUp(self):
        business_type = BusinessType.objects.create(
            name='retail', name_ar='متجر', slug='full-flow-retail'
        )
        self.tenant = Tenant.objects.create(
            name='Full Flow Test', slug='full-flow-test',
            business_type=business_type, subscription_plan='enterprise',
            version_type='multi_branch', max_stocks=20, max_branches=10,
            currency='SDG', hard_currency_mode=False,
        )
        self.user = User.objects.create_user(
            username='full-flow-admin', password='secret123',
            tenant=self.tenant, is_tenant_admin=True,
        )
        self.stock = Stock.objects.create(
            tenant=self.tenant, name='المخزن الرئيسي', code='WH-001'
        )
        self.item = Item.objects.create(
            tenant=self.tenant, name='منتج تكاملي', sku='FULL-001',
            cost_price=Decimal('40'), selling_price=Decimal('100'),
            tax_rate=Decimal('0'),
        )
        self.customer = Customer.objects.create(
            tenant=self.tenant, name='عميل تكاملي', credit_limit=Decimal('1000')
        )
        self.supplier = Supplier.objects.create(
            tenant=self.tenant, name='مورد تكاملي', currency='SDG',
            opening_balance=Decimal('0'), credit_limit=Decimal('1000'),
        )
        self.treasury = Treasury.objects.create(
            tenant=self.tenant, name='الخزينة الرئيسية', code='TR-001',
            current_balance=Decimal('1000'), is_default=True,
        )
        self.bank = BankAccount.objects.create(
            tenant=self.tenant, name='الحساب الرئيسي', bank_name='بنك الاختبار',
            current_balance=Decimal('1000'), is_default=True,
        )
        StockQuantity.objects.filter(
            tenant=self.tenant, stock=self.stock, item=self.item
        ).update(quantity=Decimal('5'), opening_quantity=Decimal('5'))

    def make_sale(self, payment_method='cash', quantity='1', **extra):
        invoice = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, customer=extra.pop('customer', None),
            invoice_date=date(2026, 9, 13), status='draft',
            payment_method=payment_method, bank_account=extra.pop('bank_account', None),
            cash_amount=extra.pop('cash_amount', Decimal('0')),
            bank_amount=extra.pop('bank_amount', Decimal('0')),
            insurance_amount=extra.pop('insurance_amount', Decimal('0')),
            insurance_member=extra.pop('insurance_member', None),
            **extra,
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

    def test_bank_sale_updates_only_bank_and_cancel_reverses_it(self):
        invoice = self.make_sale('bank', '1', bank_account=self.bank)
        confirm_sale_invoice(invoice, self.user)

        self.bank.refresh_from_db()
        self.treasury.refresh_from_db()
        self.assertEqual(self.bank.current_balance, Decimal('1100.00'))
        self.assertEqual(self.treasury.current_balance, Decimal('1000.00'))
        self.assertEqual(
            BankAccountMovement.objects.filter(
                bank_account=self.bank, reference_type='sale_payment'
            ).count(), 1
        )

        cancel_sale_invoice(invoice, self.user, 'اختبار عكس البنك')
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.current_balance, Decimal('1000.00'))

    def test_sale_rejects_empty_and_insufficient_stock_without_side_effects(self):
        empty = SaleInvoice.objects.create(
            tenant=self.tenant, stock=self.stock, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash',
        )
        with self.assertRaisesMessage(ValueError, 'فاتورة فارغة'):
            confirm_sale_invoice(empty, self.user)

        too_large = self.make_sale('cash', '6')
        with self.assertRaises(ValueError):
            confirm_sale_invoice(too_large, self.user)
        quantity = StockQuantity.objects.get(stock=self.stock, item=self.item)
        self.treasury.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('5.0000'))
        self.assertEqual(self.treasury.current_balance, Decimal('1000.00'))

    def test_sale_partial_return_restores_only_returned_quantity(self):
        invoice = self.make_sale('cash', '2')
        confirm_sale_invoice(invoice, self.user)
        line = invoice.lines.get()
        sale_return = SaleReturn.objects.create(
            tenant=self.tenant, original_invoice=invoice,
            return_date=date(2026, 9, 13), refund_method='cash',
        )
        SaleReturnLine.objects.create(
            tenant=self.tenant, sale_return=sale_return, invoice_line=line,
            item=self.item, returned_quantity=Decimal('1'), unit_price=Decimal('100'),
        )
        confirm_sale_return(sale_return, self.user)
        quantity = StockQuantity.objects.get(stock=self.stock, item=self.item)
        invoice.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('4.0000'))
        self.assertEqual(line.returned_quantity, Decimal('1.0000'))
        self.assertEqual(invoice.status, 'partially_returned')

    def test_purchase_confirm_edit_cancel_and_partial_return(self):
        invoice = PurchaseInvoice.objects.create(
            tenant=self.tenant, supplier=self.supplier, stock=self.stock,
            invoice_date=date(2026, 9, 13), status='draft', payment_method='credit',
        )
        line = PurchaseInvoiceLine(
            tenant=self.tenant, invoice=invoice, item=self.item,
            quantity=Decimal('2'), unit_cost=Decimal('40'), tax_rate=Decimal('0'),
        )
        line.calculate()
        line.save()
        invoice.recalculate_totals()
        invoice.save(update_fields=['subtotal', 'tax_amount', 'grand_total', 'updated_at'])
        confirm_purchase_invoice(invoice, self.user)
        quantity = StockQuantity.objects.get(stock=self.stock, item=self.item)
        self.assertEqual(quantity.quantity, Decimal('7.0000'))

        purchase_return = PurchaseReturn.objects.create(
            tenant=self.tenant, original_invoice=invoice,
            return_date=date(2026, 9, 13), refund_method='balance',
        )
        PurchaseReturnLine.objects.create(
            tenant=self.tenant, purchase_return=purchase_return,
            invoice_line=line, item=self.item, returned_quantity=Decimal('1'),
            unit_cost=Decimal('40'),
        )
        confirm_purchase_return(purchase_return, self.user)
        quantity.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('6.0000'))

        with self.assertRaisesMessage(ValueError, 'لا يمكن إلغاء أمر عليه مرتجعات'):
            cancel_purchase_invoice(invoice, self.user, 'اختبار إلغاء الشراء')
        quantity.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('6.0000'))

    def test_deferred_sale_reserves_then_delivers_and_cancel_releases(self):
        invoice = self.make_sale('cash', '2', delivery_type='deferred')
        confirm_sale_invoice(invoice, self.user)
        quantity = StockQuantity.objects.get(stock=self.stock, item=self.item)
        self.assertEqual(quantity.quantity, Decimal('5.0000'))
        self.assertEqual(quantity.reserved_quantity, Decimal('2.0000'))

        from apps.sales.services import deliver_sale_invoice
        deliver_sale_invoice(invoice, self.user)
        quantity.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('3.0000'))
        self.assertEqual(quantity.reserved_quantity, Decimal('0.0000'))

        pending = self.make_sale('cash', '1', delivery_type='deferred')
        confirm_sale_invoice(pending, self.user)
        cancel_sale_invoice(pending, self.user, 'إلغاء قبل التسليم')
        quantity.refresh_from_db()
        self.assertEqual(quantity.quantity, Decimal('3.0000'))
        self.assertEqual(quantity.reserved_quantity, Decimal('0.0000'))

    def test_treasury_and_bank_opening_balances_and_transfers_balance(self):
        second_treasury = Treasury.objects.create(
            tenant=self.tenant, name='خزينة ثانية', code='TR-002', current_balance=Decimal('100')
        )
        set_opening_balance(self.tenant, self.treasury, Decimal('500'), date(2026, 9, 1), self.user)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('500'))

        post_treasury_transfer(
            self.tenant, self.treasury, second_treasury,
            Decimal('200'), Decimal('200'), Decimal('1'), date(2026, 9, 13), user=self.user,
        )
        self.treasury.refresh_from_db()
        second_treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('300'))
        self.assertEqual(second_treasury.current_balance, Decimal('300'))
        self.assertEqual(TreasuryMovement.objects.filter(reference_type='transfer').count(), 2)

        post_treasury_to_bank_transfer(
            self.tenant, second_treasury, self.bank, Decimal('100'), date(2026, 9, 13), user=self.user,
        )
        second_treasury.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(second_treasury.current_balance, Decimal('200'))
        self.assertEqual(self.bank.current_balance, Decimal('1100'))

        with self.assertRaises(ValueError):
            post_bank_account_transfer(
                self.tenant, self.bank, BankAccount.objects.create(
                    tenant=self.tenant, name='حساب اختبار ثان', current_balance=Decimal('0')
                ), Decimal('2000'), Decimal('2000'), Decimal('1'),
                date(2026, 9, 13), user=self.user,
            )

    def test_expense_confirm_and_cancel_reverses_treasury(self):
        category = ExpenseCategory.objects.create(tenant=self.tenant, name='تشغيل')
        expense = Expense.objects.create(
            tenant=self.tenant, category=category, description='مصروف اختبار',
            amount=Decimal('150'), expense_date=date(2026, 9, 13),
            payment_method=Expense.PAYMENT_CASH, treasury=self.treasury,
        )
        confirm_expense(expense, self.user)
        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('850'))
        self.assertEqual(expense.status, Expense.STATUS_CONFIRMED)

        cancel_expense(expense, self.user)
        self.treasury.refresh_from_db()
        expense.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('1000'))
        self.assertEqual(expense.status, Expense.STATUS_CANCELLED)

    def test_enterprise_branches_isolate_stock_and_respect_plan(self):
        branch_one = Branch.objects.create(tenant=self.tenant, name='فرع 1', code='BR-001')
        branch_two = Branch.objects.create(tenant=self.tenant, name='فرع 2', code='BR-002')
        stock_two = Stock.objects.create(
            tenant=self.tenant, name='مخزن الفرع الثاني', code='TEST-WH-2', branch=branch_two
        )
        self.stock.branch = branch_one
        self.stock.save(update_fields=['branch', 'updated_at'])
        StockQuantity.objects.filter(stock=stock_two, item=self.item).update(quantity=Decimal('3'))
        self.assertTrue(self.tenant.plan_allows_version_type('multi_branch'))
        self.assertTrue(Branch.can_add_branch(self.tenant))
        self.assertEqual(
            StockQuantity.objects.get(stock=self.stock, item=self.item).quantity,
            Decimal('5'),
        )
        self.assertEqual(
            StockQuantity.objects.get(stock=stock_two, item=self.item).quantity,
            Decimal('3'),
        )
        self.assertNotEqual(
            StockQuantity.objects.get(stock=self.stock, item=self.item).quantity,
            StockQuantity.objects.get(stock=stock_two, item=self.item).quantity,
        )

    def test_insurance_claim_submission_approval_and_cash_settlement(self):
        company = InsuranceCompany.objects.create(
            tenant=self.tenant, name='شركة اختبار', code='INS-001',
            default_coverage_percent=Decimal('60'), settlement_period_days=30,
        )
        member = InsuranceMember.objects.create(
            tenant=self.tenant, insurance_company=company, card_number='CARD-001',
            full_name='مشترك اختبار', coverage_percent=Decimal('60'),
        )
        invoice = self.make_sale(
            'mixed', '1', customer=self.customer, insurance_member=member,
            insurance_card_number='CARD-001', cash_amount=Decimal('40'),
            insurance_amount=Decimal('60'),
        )
        confirm_sale_invoice(invoice, self.user)
        claim = create_claim_from_sale(invoice, self.tenant, Decimal('60'), self.user)
        self.assertEqual(claim.covered_amount, Decimal('60.00'))
        self.assertEqual(claim.patient_amount, Decimal('40.00'))

        submit_claim(claim, self.user)
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'submitted')
        record_claim_response(claim, Decimal('60'), 'approved', '', self.user)
        claim.refresh_from_db()
        self.assertEqual(claim.status, 'approved')
        settle_claim_payment(
            claim, Decimal('60'), date(2026, 9, 13), 'SET-001', self.user,
            treasury=self.treasury, received_method='cash',
        )
        claim.refresh_from_db()
        self.treasury.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(claim.status, 'paid')
        self.assertEqual(claim.paid_amount, Decimal('60.00'))
        # 1000 (رصيد افتتاحي) + 40 (الجزء النقدي من الفاتورة المختلطة، يذهب
        # لهذه الخزينة عبر get_or_create_default_treasury لأنها is_default=True
        # الوحيدة للـ tenant الآن بعد إلغاء الخزينة النظامية الافتراضية
        # التلقائية لنسخة المؤسسات) + 60 (تسوية مطالبة التأمين، خزينة صريحة).
        self.assertEqual(self.treasury.current_balance, Decimal('1100.00'))
        self.assertEqual(invoice.paid_amount, Decimal('100.00'))
        self.assertEqual(
            claim.settlement_entries.aggregate(total=Sum('amount'))['total'], Decimal('0.00')
        )


class ProfessionalEditionTests(TestCase):
    """Dedicated coverage for the pro plan: multiple stocks, transfers, and limits."""

    def setUp(self):
        business_type = BusinessType.objects.create(
            name='retail', name_ar='متجر', slug='pro-flow-retail'
        )
        self.tenant = Tenant.objects.create(
            name='Professional Edition Test', slug='professional-edition-test',
            business_type=business_type, subscription_plan='pro',
            version_type='multi_stock', max_stocks=5, max_branches=0,
            currency='SDG', hard_currency_mode=False,
        )
        self.user = User.objects.create_user(
            username='professional-edition-admin', password='secret123',
            tenant=self.tenant, is_tenant_admin=True,
        )
        # إنشاء الـ tenant ينشئ مخزناً افتراضياً واحداً فقط (المشترك يضيف
        # الباقي بنفسه حتى الحد المسموح به — max_stocks سقف وليس عدداً
        # يُنشأ تلقائياً). نضيف مخزناً ثانياً يدوياً لاختبارات التحويل.
        self.stock_one = Stock.objects.filter(tenant=self.tenant, is_active=True).order_by('id').first()
        self.stock_two = Stock.objects.create(
            tenant=self.tenant, name='مخزن ثانٍ', code='WH-002', is_active=True,
        )
        self.item = Item.objects.create(
            tenant=self.tenant, name='منتج الاحترافية', sku='PRO-001',
            cost_price=Decimal('40'), selling_price=Decimal('100'), tax_rate=Decimal('0'),
        )
        StockQuantity.objects.filter(
            tenant=self.tenant, stock=self.stock_one, item=self.item
        ).update(quantity=Decimal('5'))

    def test_pro_plan_allows_multiple_stocks_but_not_branches(self):
        self.assertTrue(self.tenant.plan_allows_version_type('multi_stock'))
        self.assertFalse(self.tenant.plan_allows_version_type('multi_branch'))
        # مخزنان فقط حتى الآن (الافتراضي + المُضاف يدوياً في setUp) — ما زال
        # بإمكان المشترك إضافة المزيد حتى يبلغ الحد الأقصى (max_stocks).
        self.assertEqual(
            Stock.objects.filter(tenant=self.tenant, is_active=True).count(), 2,
        )
        self.assertTrue(Stock.can_add_stock(self.tenant))
        for i in range(3, self.tenant.max_stocks + 1):
            Stock.objects.create(
                tenant=self.tenant, name=f'مخزن {i}', code=f'WH-{i:03d}',
                is_active=True,
            )
        self.assertEqual(
            Stock.objects.filter(tenant=self.tenant, is_active=True).count(),
            self.tenant.max_stocks,
        )
        self.assertFalse(Stock.can_add_stock(self.tenant))
        self.assertFalse(Branch.can_add_branch(self.tenant))

    def test_stock_transfer_confirm_and_cancel_preserve_total_quantity(self):
        transfer = StockTransfer.objects.create(
            tenant=self.tenant, from_stock=self.stock_one, to_stock=self.stock_two,
            transfer_date=date(2026, 9, 13), status='draft',
        )
        StockTransferLine.objects.create(
            tenant=self.tenant, transfer=transfer, item=self.item, quantity=Decimal('2')
        )
        confirm_stock_transfer(transfer)
        source = StockQuantity.objects.get(stock=self.stock_one, item=self.item)
        destination = StockQuantity.objects.get(stock=self.stock_two, item=self.item)
        self.assertEqual(source.quantity, Decimal('3.0000'))
        self.assertEqual(destination.quantity, Decimal('2.0000'))
        self.assertEqual(
            StockMovement.objects.filter(reference_type='stock_transfer', reference_id=transfer.id).count(),
            2,
        )

        cancel_stock_transfer(transfer)
        source.refresh_from_db()
        destination.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(source.quantity, Decimal('5.0000'))
        self.assertEqual(destination.quantity, Decimal('0.0000'))
        self.assertEqual(transfer.status, 'cancelled')

    def test_pro_transfer_rejects_more_than_available(self):
        transfer = StockTransfer.objects.create(
            tenant=self.tenant, from_stock=self.stock_one, to_stock=self.stock_two,
            transfer_date=date(2026, 9, 13), status='draft',
        )
        StockTransferLine.objects.create(
            tenant=self.tenant, transfer=transfer, item=self.item, quantity=Decimal('6')
        )
        with self.assertRaises(ValueError):
            confirm_stock_transfer(transfer)
        source = StockQuantity.objects.get(stock=self.stock_one, item=self.item)
        destination = StockQuantity.objects.get(stock=self.stock_two, item=self.item)
        self.assertEqual(source.quantity, Decimal('5.0000'))
        self.assertEqual(destination.quantity, Decimal('0.0000'))
