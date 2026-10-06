"""
نسخة المؤسسات: مدير الفرع لا يتعامل إلا مع خزائن/حسابات فرعه في العمليات
اليومية، ولا تذهب الحركات النقدية بلا خزينة محددة إلى خزينة الإدارة المركزية
أو خزينة فرع آخر.
"""
import json
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch, BusinessType, Tenant
from apps.customers.models import Customer
from apps.treasury.models import Treasury
from apps.treasury.services import get_or_create_default_treasury, post_treasury_receipt


class _EnterpriseSetup:
    def setUp(self):
        bt = BusinessType.objects.create(name='bt-money', name_ar='نوع', slug='bt-money')
        terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.tenant = Tenant.objects.create(
            name='Ent Money', business_type=bt, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **terms,
        )
        self.tenant.save()  # يضمن خزينة الإدارة المركزية
        self.branch_a = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.branch_b = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        self.hq = Treasury.objects.get(tenant=self.tenant, is_head_office=True, is_hard_currency=False)
        self.ta = Treasury.objects.get(tenant=self.tenant, branch=self.branch_a)
        self.tb = Treasury.objects.get(tenant=self.tenant, branch=self.branch_b)
        self.mgr_a = User.objects.create_user(username='money-a', password='s3cret123', tenant=self.tenant)
        Branch.assign_manager(self.branch_a, self.mgr_a)
        self.mgr_a.refresh_from_db()


class BranchMoneyScopeTests(_EnterpriseSetup, TestCase):
    def test_default_treasury_for_branch_user_is_branch_treasury(self):
        self.assertEqual(get_or_create_default_treasury(self.tenant, user=self.mgr_a), self.ta)

    def test_cash_receipt_without_treasury_lands_in_branch_treasury(self):
        post_treasury_receipt(self.tenant, Decimal('500'), timezone.localdate(), user=self.mgr_a)
        self.ta.refresh_from_db(); self.hq.refresh_from_db(); self.tb.refresh_from_db()
        self.assertEqual(self.ta.current_balance, Decimal('500'))
        self.assertEqual(self.hq.current_balance, Decimal('0'))
        self.assertEqual(self.tb.current_balance, Decimal('0'))

    def _pay(self, treasury):
        customer = Customer.objects.create(tenant=self.tenant, name='عميل', branch=self.branch_a)
        return self.client.post('/customers/payments/create/', data=json.dumps({
            'customer_id': customer.id, 'amount': '100', 'method': 'cash', 'treasury_id': treasury.id,
        }), content_type='application/json')

    def test_branch_user_cannot_use_head_office_or_other_branch_treasury(self):
        self.client.force_login(self.mgr_a)
        for foreign in (self.hq, self.tb):
            resp = self._pay(foreign)
            self.assertIn(resp.status_code, (403, 404), foreign.name)
            foreign.refresh_from_db()
            self.assertEqual(foreign.current_balance, Decimal('0'))

    def test_branch_user_can_use_own_treasury(self):
        self.client.force_login(self.mgr_a)
        resp = self._pay(self.ta)
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        self.ta.refresh_from_db()
        self.assertEqual(self.ta.current_balance, Decimal('100'))

    def test_payment_screen_lists_only_branch_treasuries(self):
        self.client.force_login(self.mgr_a)
        resp = self.client.get('/customers/payments/')
        self.assertEqual(list(resp.context['treasuries']), [self.ta])


class InvoiceBranchDerivationTests(_EnterpriseSetup, TestCase):
    """فواتير البيع والشراء تأخذ فرعها من المخزن تلقائياً (لا سجلات بلا فرع)."""

    def test_invoices_inherit_branch_from_stock(self):
        from apps.purchases.models import PurchaseInvoice
        from apps.sales.models import SaleInvoice
        from apps.stocks.models import Stock
        from apps.suppliers.models import Supplier
        stock = Stock.objects.create(tenant=self.tenant, name='مخزن أ', branch=self.branch_a)
        sale = SaleInvoice.objects.create(tenant=self.tenant, stock=stock, invoice_date=timezone.localdate())
        self.assertEqual(sale.branch_id, self.branch_a.id)
        supplier = Supplier.objects.create(tenant=self.tenant, name='مورد', branch=self.branch_a)
        purchase = PurchaseInvoice.objects.create(tenant=self.tenant, stock=stock, supplier=supplier,
                                                  invoice_date=timezone.localdate())
        self.assertEqual(purchase.branch_id, self.branch_a.id)


class BranchTransferSourceTests(_EnterpriseSetup, TestCase):
    """مدير الفرع لا يسحب من خزينة الإدارة المركزية بتحويل إلى خزينة فرعه."""

    def test_branch_manager_cannot_pull_from_head_office(self):
        Treasury.objects.filter(pk=self.hq.pk).update(current_balance=Decimal('1000'))
        self.client.force_login(self.mgr_a)
        resp = self.client.post('/treasury/api/transfer/', data=json.dumps({
            'from_treasury': self.hq.id, 'to_treasury': self.ta.id, 'from_amount': '100', 'to_amount': '100',
            'exchange_rate': '1', 'transfer_date': str(timezone.localdate()),
        }), content_type='application/json')
        # الخزينة المركزية مخفية عن مدير الفرع أصلاً (404) أو ممنوعة (403)
        self.assertIn(resp.status_code, (403, 404))
        self.hq.refresh_from_db(); self.ta.refresh_from_db()
        self.assertEqual(self.hq.current_balance, Decimal('1000'))
        self.assertEqual(self.ta.current_balance, Decimal('0'))


class StoreOrderCustomerTests(_EnterpriseSetup, TestCase):
    """اعتماد طلب المتجر يربط الطلب بعميل من فرع المخزن، ولا يفشل عند تكرار رقم الهاتف."""

    def test_approve_order_picks_branch_customer_and_survives_duplicate_phones(self):
        from apps.store.models import OnlineOrder, StoreSettings
        from apps.store.services import approve_order
        from apps.stocks.models import Stock
        Stock.objects.create(tenant=self.tenant, name='مخزن أ', branch=self.branch_a, is_default=True)
        Customer.objects.create(tenant=self.tenant, name='عميل الفرع ب', phone='0911', branch=self.branch_b)
        own = Customer.objects.create(tenant=self.tenant, name='عميل أ', phone='0911', branch=self.branch_a)
        Customer.objects.create(tenant=self.tenant, name='عميل أ مكرر', phone='0911', branch=self.branch_a)
        store = StoreSettings.objects.create(tenant=self.tenant)
        order = OnlineOrder.objects.create(tenant=self.tenant, store=store, customer_name='زبون', customer_phone='0911',
                                           payment_method=OnlineOrder.PAYMENT_CHOICES[0][0])
        invoice = approve_order(order)
        self.assertEqual(invoice.customer, own)
        self.assertEqual(invoice.branch_id, self.branch_a.id)


class BranchImportTests(_EnterpriseSetup, TestCase):
    """العملاء المستوردون بحساب مدير الفرع يُسجَّلون على فرعه."""

    def test_branch_manager_import_assigns_branch(self):
        import io
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        from apps.data_import.schemas import get_customer_schema
        from apps.data_import.simple_importer import import_simple_entities
        schema = get_customer_schema(self.tenant)
        wb = Workbook(); ws = wb.active
        ws.append([s['header_ar'] for s in schema])
        ws.append(['عميل مستورد'] + [''] * (len(schema) - 1))
        buf = io.BytesIO(); wb.save(buf)
        result = import_simple_entities(self.tenant, SimpleUploadedFile('c.xlsx', buf.getvalue()), self.mgr_a,
                                        Customer, schema, 'عميل')
        self.assertEqual(result['created'], 1, result)
        self.assertEqual(Customer.objects.get(tenant=self.tenant, name='عميل مستورد').branch_id, self.branch_a.id)
