"""
مساعدات اختبار مشتركة لكل التطبيقات.

يوفر هذا الملف:
  - TenantTestCase: كلاس أساسي ينشئ BusinessType + Tenant + مستخدم مدير
    مسجّل دخول في self.client، بدل تكرار نفس الـ 15 سطر في كل ملف اختبار.
  - دوال مصنع خفيفة (بدون مكتبات خارجية) للنماذج الأساسية المستخدمة في أغلب
    الاختبارات: صنف، مخزن، عميل، مورد، خزينة، حساب بنكي، مندوب، موظف.

ملاحظة مهمة: عند إنشاء Tenant من نسخة single_store/multi_stock (غير
Enterprise) تُنشأ تلقائياً عبر signals (انظر apps/core/signals.py):
  - مخزن افتراضي واحد (is_system_default=True)
  - خزينة افتراضية واحدة (is_system_default=True)
  - سجلات StockQuantity (كمية=0) لكل صنف × مخزن قائم بالفعل
لذلك دوال المصنع هنا لا تُنشئ مخزناً/خزينة افتراضيين من جديد إلا إذا طُلب
صراحة (multiple=True) لتفادي انتهاك unique_together على (tenant, code).

نسخة المؤسسات (version_type='multi_branch') استثناء: لا مخزن ولا خزينة
افتراضيين تلقائياً — self.default_stock/self.default_treasury تُرجع None،
واختبارات هذه النسخة يجب أن تُنشئ مخزنها/خزينتها صراحة (make_stock/
make_treasury) قبل استخدام set_quantity أو أي عملية تحتاج مخزناً.
"""
from decimal import Decimal

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import BusinessType, Tenant


class TenantTestCase(TestCase):
    """
    كلاس أساسي: self.tenant / self.user (مدير Tenant) / self.client مسجّل دخول
    جاهزون بعد استدعاء super().setUp().

    Attributes قابلة للتخصيص في الكلاس الفرعي:
      version_type       'single_store' (افتراضي) | 'multi_stock' | 'multi_branch'
      subscription_plan  'basic' (افتراضي) | 'pro' | 'enterprise'
      max_stocks / max_branches  تُشتق تلقائياً من حدود الباقة إن لم تُحدَّد
    """

    version_type = 'single_store'
    subscription_plan = 'basic'
    max_stocks = None
    max_branches = None

    def setUp(self):
        super().setUp()
        slug_base = self.__class__.__name__.lower()
        business_type = BusinessType.objects.create(
            name=f'test-{slug_base}', name_ar='نشاط اختبار', slug=f'bt-{slug_base}',
        )
        limits = Tenant.PLAN_LIMITS[self.subscription_plan]
        self.tenant = Tenant.objects.create(
            name=f'Tenant {self.__class__.__name__}',
            business_type=business_type,
            subscription_plan=self.subscription_plan,
            version_type=self.version_type,
            max_stocks=self.max_stocks if self.max_stocks is not None else limits['max_stocks'],
            max_branches=self.max_branches if self.max_branches is not None else limits['max_branches'],
            currency='SDG', hard_currency_mode=False,
            # يتفادى إعادة التوجيه إلى /terms/ عبر TermsMiddleware عند اختبار
            # views/URLs حقيقية عبر self.client.
            terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now(),
        )
        self.user = User.objects.create_user(
            username=f'admin-{slug_base}', password='secret123',
            tenant=self.tenant, is_tenant_admin=True,
        )
        self.client.force_login(self.user)

    @property
    def default_stock(self):
        from apps.stocks.models import Stock
        return Stock.objects.filter(tenant=self.tenant, is_system_default=True).first()

    @property
    def default_treasury(self):
        from apps.treasury.models import Treasury
        return Treasury.objects.filter(tenant=self.tenant, is_system_default=True).first()

    def set_quantity(self, item, stock, quantity, reserved=Decimal('0')):
        """يضبط StockQuantity الموجود مسبقاً (أُنشئ تلقائياً عبر signal) بدل إنشاء سجل جديد."""
        from apps.stocks.models import StockQuantity
        StockQuantity.objects.filter(tenant=self.tenant, stock=stock, item=item).update(
            quantity=Decimal(quantity), opening_quantity=Decimal(quantity), reserved_quantity=Decimal(reserved),
        )
        return StockQuantity.objects.get(tenant=self.tenant, stock=stock, item=item)


# ============================================
# دوال مصنع خفيفة — بدون factory_boy (غير مثبتة في المشروع عمداً)
# ============================================

def make_item(tenant, name='صنف اختبار', cost_price='40', selling_price='100', tax_rate='0', **extra):
    from apps.items.models import Item
    return Item.objects.create(
        tenant=tenant, name=name,
        cost_price=Decimal(cost_price), selling_price=Decimal(selling_price),
        tax_rate=Decimal(tax_rate), **extra,
    )


def make_stock(tenant, name='مخزن إضافي', **extra):
    from apps.stocks.models import Stock
    return Stock.objects.create(tenant=tenant, name=name, **extra)


def make_customer(tenant, name='عميل اختبار', credit_limit='0', **extra):
    from apps.customers.models import Customer
    return Customer.objects.create(
        tenant=tenant, name=name, credit_limit=Decimal(credit_limit), **extra,
    )


def make_supplier(tenant, name='مورد اختبار', credit_limit='0', **extra):
    from apps.suppliers.models import Supplier
    return Supplier.objects.create(
        tenant=tenant, name=name, credit_limit=Decimal(credit_limit), **extra,
    )


def make_treasury(tenant, name='خزينة إضافية', current_balance='0', **extra):
    from apps.treasury.models import Treasury
    return Treasury.objects.create(
        tenant=tenant, name=name, current_balance=Decimal(current_balance), **extra,
    )


def make_bank_account(tenant, name='حساب بنكي اختبار', current_balance='0', **extra):
    from apps.bank_accounts.models import BankAccount
    return BankAccount.objects.create(
        tenant=tenant, name=name, current_balance=Decimal(current_balance), **extra,
    )


def make_agent(tenant, name='مندوب اختبار', commission_basis='invoice', commission_type='percentage',
               commission_rate='0', **extra):
    from apps.agents.models import Agent
    return Agent.objects.create(
        tenant=tenant, name=name, commission_basis=commission_basis,
        commission_type=commission_type, commission_rate=Decimal(commission_rate), **extra,
    )


def make_employee(tenant, name='موظف اختبار', base_salary='0', **extra):
    from apps.employees.models import Employee
    return Employee.objects.create(
        tenant=tenant, name=name, base_salary=Decimal(base_salary), **extra,
    )
