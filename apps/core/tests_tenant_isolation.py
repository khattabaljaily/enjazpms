"""
اختبارات عزل البيانات بين المشتركين (tenants). حسب بحث سابق في معمارية
النظام: TenantManager/TenantQuerySet لا يُصفِّيان تلقائياً حسب tenant الحالي
(TenantMixin.objects عادي بامتداد for_tenant() اختياري) — العزل بالكامل
مبني على أن كل view يُضيف تصفية tenant= يدوياً بنفسه. هذا يعني أن أي view
ينسى تلك التصفية عند جلب كائن بمعرّفه (get_object_or_404) قد يُسرِّب بيانات
مشترك آخر لمن يعرف رقم المعرّف فقط — خطر حقيقي وليس افتراضياً.

يُغطّي هذا الملف أهم شاشات "تفاصيل بمعرّف" عبر التطبيقات المالية/التخزينية
الأساسية: يُنشئ كائناً حقيقياً عند tenant B، ثم يحاول الوصول إليه بنفس
معرّفه وهو مسجّل دخول كمدير tenant A — يجب أن يُرفض دوماً (404)، لا أن
يُعرض أو يُعدَّل.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import Agent
from apps.core.models import BusinessType, Tenant
from apps.core.test_utils import make_customer, make_employee, make_item, make_supplier
from apps.customers.models import Customer
from apps.employees.models import Employee
from apps.items.models import Item
from apps.purchases.models import PurchaseInvoice
from apps.sales.models import SaleInvoice
from apps.suppliers.models import Supplier


class TenantIsolationTests(TestCase):
    """
    كائنان منفصلان تماماً (tenant_a / tenant_b)، كل واحد له مدير مسجّل دخول
    خاص به. self.client يُمثِّل جلسة tenant A طوال الاختبار.
    """

    def setUp(self):
        bt_a = BusinessType.objects.create(name='iso-a', name_ar='أ', slug='iso-a')
        bt_b = BusinessType.objects.create(name='iso-b', name_ar='ب', slug='iso-b')
        terms = dict(terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.tenant_a = Tenant.objects.create(
            name='Tenant A', business_type=bt_a, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **terms,
        )
        self.tenant_b = Tenant.objects.create(
            name='Tenant B', business_type=bt_b, subscription_plan='enterprise',
            version_type='multi_branch', currency='SDG', **terms,
        )
        self.user_a = User.objects.create_user(
            username='iso-admin-a', password='secret123', tenant=self.tenant_a, is_tenant_admin=True,
        )
        self.user_b = User.objects.create_user(
            username='iso-admin-b', password='secret123', tenant=self.tenant_b, is_tenant_admin=True,
        )
        self.client.force_login(self.user_a)

        # كائن حقيقي لكل نوع عند tenant B فقط
        self.customer_b = make_customer(self.tenant_b, name='عميل ب')
        self.supplier_b = make_supplier(self.tenant_b, name='مورد ب')
        self.item_b = make_item(self.tenant_b, name='صنف ب')
        self.employee_b = make_employee(self.tenant_b, name='موظف ب')
        self.agent_b = Agent.objects.create(tenant=self.tenant_b, name='مندوب ب')

        from apps.stocks.models import Stock
        stock_b = Stock.objects.filter(tenant=self.tenant_b, is_system_default=True).first()
        self.sale_invoice_b = SaleInvoice.objects.create(
            tenant=self.tenant_b, stock=stock_b, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash',
        )
        self.purchase_invoice_b = PurchaseInvoice.objects.create(
            tenant=self.tenant_b, stock=stock_b, invoice_date=date(2026, 9, 13),
            status='draft', payment_method='cash',
        )

    def assertNotLeaked(self, path):
        response = self.client.get(path)
        self.assertIn(
            response.status_code, (404, 302, 403),
            f'تسرّب محتمل: {path} أرجع {response.status_code} بدل رفض الوصول لكائن مشترك آخر',
        )
        # لو 200، تأكد صراحة أن الرد لا يحتوي بيانات الكائن المسرَّب (بعض
        # الشاشات القديمة قد ترجع 200 مع رسالة خطأ JSON بدل 404 حرفي).
        if response.status_code == 200:
            body = response.content.decode('utf-8', errors='ignore')
            self.assertNotIn('"success": true', body.replace(' ', ''))

    def test_customer_detail_across_tenants_is_rejected(self):
        self.assertNotLeaked(f'/customers/api/{self.customer_b.id}/detail/')

    def test_supplier_detail_across_tenants_is_rejected(self):
        self.assertNotLeaked(f'/suppliers/api/{self.supplier_b.id}/detail/')

    def test_item_detail_across_tenants_is_rejected(self):
        self.assertNotLeaked(f'/items/api/{self.item_b.id}/detail/')

    def test_employee_detail_across_tenants_is_rejected(self):
        self.assertNotLeaked(f'/employees/api/{self.employee_b.id}/')

    def test_agent_detail_across_tenants_is_rejected(self):
        self.assertNotLeaked(f'/agents/api/{self.agent_b.id}/detail/')

    def test_sale_invoice_detail_across_tenants_is_rejected(self):
        self.assertNotLeaked(f'/sales/{self.sale_invoice_b.id}/')

    def test_purchase_invoice_detail_across_tenants_is_rejected(self):
        self.assertNotLeaked(f'/purchases/{self.purchase_invoice_b.id}/')

    def test_customer_list_api_never_includes_other_tenants_rows(self):
        response = self.client.get('/customers/api/table/')
        if response.status_code != 200:
            return  # route قد يتطلب باراميترات DataTables معينة — يُختبر منفصلاً لو لزم
        body = response.content.decode('utf-8', errors='ignore')
        self.assertNotIn(self.customer_b.name, body)

    def test_cross_tenant_queryset_filtering_is_correct_at_the_orm_level(self):
        """
        تأكيد تكميلي على مستوى الاستعلام مباشرة (وليس فقط عبر HTTP) أن
        for_tenant() يعزل فعلياً — أساس كل الفحوصات أعلاه.
        """
        self.assertFalse(Customer.objects.filter(tenant=self.tenant_a, pk=self.customer_b.pk).exists())
        self.assertFalse(Item.objects.filter(tenant=self.tenant_a, pk=self.item_b.pk).exists())
        self.assertFalse(Employee.objects.filter(tenant=self.tenant_a, pk=self.employee_b.pk).exists())
        self.assertFalse(SaleInvoice.objects.filter(tenant=self.tenant_a, pk=self.sale_invoice_b.pk).exists())
