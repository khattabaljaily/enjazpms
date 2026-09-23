"""
اختبار ذاتي لبنية الاختبار المشتركة (TenantTestCase + دوال المصنع في
apps/core/test_utils.py). ليس اختباراً لمنطق عمل — الهدف التأكد أن الأساس
الذي ستُبنى عليه بقية الاختبارات يعمل كما هو متوقع.
"""
from decimal import Decimal

from apps.core.test_utils import (
    TenantTestCase,
    make_agent,
    make_bank_account,
    make_customer,
    make_employee,
    make_item,
    make_stock,
    make_supplier,
    make_treasury,
)


class TenantTestCaseHarnessTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def test_tenant_user_and_client_are_ready(self):
        self.assertTrue(self.tenant.pk)
        self.assertEqual(self.user.tenant, self.tenant)
        self.assertTrue(self.user.is_tenant_admin)
        response = self.client.get('/')
        self.assertNotEqual(response.status_code, 302, 'يجب أن يكون المستخدم مسجل دخول فعلاً')

    def test_no_default_stock_or_treasury_for_enterprise_tenant(self):
        """
        نسخة المؤسسات (multi_branch) لا تحصل على مخزن/خزينة افتراضيين عند
        التسجيل (راجع apps/core/signals.py::create_tenant_defaults) — خلافاً
        لباقي النسخ single_store/multi_stock (راجع TenantTestCaseDefaultsTests
        أدناه لسلوكها).
        """
        self.assertIsNone(self.default_stock)
        self.assertIsNone(self.default_treasury)

    def test_plan_and_version_type_overrides_applied(self):
        self.assertEqual(self.tenant.subscription_plan, 'enterprise')
        self.assertEqual(self.tenant.version_type, 'multi_branch')
        self.assertTrue(self.tenant.plan_allows_version_type('multi_branch'))

    def test_set_quantity_updates_the_auto_created_stock_quantity_row(self):
        # لا مخزن افتراضياً هنا (tenant من نسخة المؤسسات) — ننشئ واحداً صراحة
        # فيُنشئ signal الصنف/المخزن سجل StockQuantity المقابل تلقائياً.
        stock = make_stock(self.tenant, code='WH-HARNESS')
        item = make_item(self.tenant, name='صنف الأساس')
        sq = self.set_quantity(item, stock, '10')
        self.assertEqual(sq.quantity, Decimal('10.0000'))
        self.assertEqual(sq.opening_quantity, Decimal('10.0000'))

    def test_factories_create_valid_rows_scoped_to_the_tenant(self):
        item = make_item(self.tenant)
        stock = make_stock(self.tenant, code='WH-EXTRA')
        customer = make_customer(self.tenant, credit_limit='500')
        supplier = make_supplier(self.tenant, credit_limit='500')
        treasury = make_treasury(self.tenant, current_balance='100')
        bank = make_bank_account(self.tenant, current_balance='100')
        agent = make_agent(self.tenant, commission_rate='5')
        employee = make_employee(self.tenant, base_salary='1000')

        for obj in (item, stock, customer, supplier, treasury, bank, agent, employee):
            self.assertEqual(obj.tenant_id, self.tenant.id)

        self.assertEqual(customer.credit_limit, Decimal('500'))
        self.assertEqual(agent.commission_rate, Decimal('5'))


class TenantTestCaseDefaultsTests(TenantTestCase):
    """التحقق من أن القيم الافتراضية (single_store/basic) تعمل بدون أي تخصيص."""

    def test_defaults_are_single_store_basic(self):
        self.assertEqual(self.tenant.version_type, 'single_store')
        self.assertEqual(self.tenant.subscription_plan, 'basic')
        self.assertEqual(self.tenant.max_stocks, 1)
        self.assertEqual(self.tenant.max_branches, 0)
