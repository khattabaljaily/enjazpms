"""
اختبار: كل مستخدم يُنشأ/يُعدَّل عبر UserManagementForm في نسخة المؤسسات
(multi_branch) يجب أن يتبع فرعاً محدداً — بدون هذا القيد، مستخدم عادي بلا
فرع (request.branch = None) يُنتج سجلات يتيمة بلا فرع (مصروفات، فواتير...)
تظهر خطأً لكل الفروع — نفس فئة الخلل التي أنتجت خزينة عملة صعبة يتيمة
(راجع apps/core/signals.py::_ensure_hc_treasury).

single_store/multi_stock غير متأثرتين: لا فروع أصلاً، فالحقل يبقى اختيارياً.
"""
from apps.accounts.forms import UserManagementForm
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase


class EnterpriseUserRequiresBranchTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='الفرع الرئيسي')

    def _base_data(self, **overrides):
        data = {
            'username': 'new-employee',
            'first_name': 'موظف',
            'last_name': 'تجريبي',
            'email': 'employee@example.com',
            'phone': '',
            'password': 'secret12345',
            'password_confirm': 'secret12345',
            'is_branch_supervisor': False,
            'is_active': 'on',
        }
        data.update(overrides)
        return data

    def test_regular_user_without_branch_is_rejected(self):
        form = UserManagementForm(self._base_data(branch=''), tenant=self.tenant)
        self.assertFalse(form.is_valid())
        self.assertIn('يجب اختيار الفرع', str(form.errors))

    def test_regular_user_with_branch_is_accepted(self):
        form = UserManagementForm(self._base_data(branch=self.branch.pk), tenant=self.tenant)
        self.assertTrue(form.is_valid(), form.errors)

    def test_editing_tenant_admin_does_not_require_branch(self):
        # مدير النشاط لا يُدار عبر هذا الفورم أصلاً (is_tenant_admin غير
        # مدرج في fields)، لكن نتأكد أن تمرير instance له صراحة (لو حصل عبر
        # مسار آخر) لا يفرض عليه فرعاً بالخطأ.
        form = UserManagementForm(
            self._base_data(branch='', username=self.user.username, email=self.user.email),
            instance=self.user, tenant=self.tenant,
        )
        form.is_valid()
        self.assertNotIn('يجب اختيار الفرع', str(form.errors))


class SingleStoreUserBranchOptionalTests(TenantTestCase):
    version_type = 'single_store'
    subscription_plan = 'basic'

    def test_regular_user_without_branch_is_accepted(self):
        form = UserManagementForm({
            'username': 'new-employee-ss',
            'first_name': 'موظف',
            'last_name': 'تجريبي',
            'email': 'employee-ss@example.com',
            'phone': '',
            'password': 'secret12345',
            'password_confirm': 'secret12345',
            'branch': '',
            'is_branch_supervisor': False,
            'is_active': 'on',
        }, tenant=self.tenant)
        self.assertTrue(form.is_valid(), form.errors)
