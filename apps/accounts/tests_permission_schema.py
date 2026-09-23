"""
اختبارات بنية ملف الصلاحيات (apps/accounts/permissions_schema.json) —
مكمِّلة لـ tests_permission_matrix.py (الذي يختبر التطبيق الفعلي للصلاحيات
ولا يلمس شكل الملف إطلاقاً)، هذا الملف يختبر تنظيم/ترتيب/اكتمال المصدر
نفسه:

  1. ترتيب الأقسام والمفاتيح يطابق ترتيب القائمة الجانبية بالضبط — أي
     تغيير مستقبلي في هذا الترتيب (عرضي أو مقصود) يجب أن يُحدَّث هنا صراحة.
  2. كل مفتاح صلاحية يستخدمه أي @require_permission/@require_any_permission/
     @require_all_permissions في كامل المشروع موجود فعلاً في الملف (وإلا
     فمدير النشاط لن يقدر يمنحه لأي مجموعة صلاحيات أبداً).
  3. ملفات المرجع الثلاثة (permissions_reference/*.json) ليست قديمة —
     تُولَّد دائماً من نفس المصدر عبر generate_permission_reference.
"""
from django.test import SimpleTestCase

from apps.accounts.permissions import _load_structured_schema, get_permission_keys
from apps.accounts.tests_permission_matrix import _discover_permission_protected_routes
from apps.accounts.management.commands.generate_permission_reference import (
    VERSION_TYPES, OUTPUT_DIR, render_reference_json,
)


# المصدر الوحيد لترتيب القائمة الجانبية المعتمَد — أي تعديل عليه يجب أن يقابله
# تعديل مطابق فعلاً في apps/core/templates/components/sidebar.html (وليس
# العكس: هذا الاختبار يحمي الترتيب من انزلاق غير مقصود، لا يفرضه).
EXPECTED_SECTION_ORDER = [
    'dashboard', 'ai', 'tenant_settings', 'branches', 'store',
    'permission_groups', 'users', 'insurance', 'sales', 'purchases',
    'categories', 'items', 'stocks', 'stock_destructions', 'customers',
    'suppliers', 'agents', 'employees', 'expenses', 'treasuries',
    'bank_accounts', 'employee_advances', 'employee_incentives',
    'employee_salaries', 'sales_reports', 'purchases_reports',
    'stocks_reports', 'accounts_reports', 'agents_reports', 'notifications',
]


class PermissionSchemaOrderTests(SimpleTestCase):
    def test_section_order_matches_sidebar(self):
        actual_order = [section['key'] for section in _load_structured_schema()]
        self.assertEqual(actual_order, EXPECTED_SECTION_ORDER)


class PermissionSchemaCoverageTests(SimpleTestCase):
    def test_every_decorator_key_exists_in_schema(self):
        routes = _discover_permission_protected_routes()
        self.assertGreater(len(routes), 30, 'يجب اكتشاف عدد كبير من المسارات المحمية')

        schema_keys = set(get_permission_keys())
        missing = {}
        for path, keys, _mode in routes:
            for key in keys:
                if key not in schema_keys:
                    missing.setdefault(key, []).append(path)

        self.assertFalse(
            missing,
            f'مفاتيح صلاحية مستخدمة في views لكنها غير موجودة في permissions_schema.json: {missing}'
        )


class PermissionReferenceFilesFreshnessTests(SimpleTestCase):
    def test_reference_files_are_not_stale(self):
        stale = []
        for version_type in VERSION_TYPES:
            file_path = OUTPUT_DIR / f'{version_type}.json'
            expected = render_reference_json(version_type)
            actual = file_path.read_text(encoding='utf-8') if file_path.exists() else None
            if actual != expected:
                stale.append(version_type)

        self.assertFalse(
            stale,
            'ملفات مرجع الصلاحيات التالية قديمة، شغّل: '
            'python manage.py generate_permission_reference — ' + ', '.join(stale)
        )
