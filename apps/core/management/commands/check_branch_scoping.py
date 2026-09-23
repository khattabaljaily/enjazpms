"""
check_branch_scoping — أداة تدقيق CI (خطة تنفيذ Enterprise، القسم 3.4).

النية: تحويل "نسيان تطبيق فلترة الفرع في view جديد" من خطأ صامت (يتكرر
تاريخياً حسب سجل git — راجع commits d06f295 وc792f9e) إلى فشل CI ظاهر.

الآلية: مسح كل apps/*/views.py بحثاً عن دوال تلمس موديلاً من قائمة
BRANCH_SENSITIVE_MODELS مباشرة (نمط `Model.objects`)، والتحقق أن كل دالة
كهذه إما:
  (أ) تستدعي for_branch(/filter_by_branch_via(/resolve_report_scope(/
      enforce_branch_ownership( في جسمها (فلترة قوائم أو تحقق ملكية سجل
      واحد بمعرّفه)، أو
  (ب) مُزيَّنة بـ deny_branch_scoped (لا يصلها مستخدم بفرع من الأساس)، أو
  (ج) مُزيَّنة صراحة بـ branch_scope_exempt(reason) (استثناء موثَّق).

هذه أداة heuristic (فحص نصي على مستوى جسم الدالة) لا تحليلاً دلالياً
كاملاً — الهدف "خط دفاع ثانٍ" يُقلِّل احتمال النسيان، لا إثبات رياضي.
أي نتيجة إيجابية كاذبة تُعالَج بتوثيق استثناء عبر branch_scope_exempt، لا
بتعطيل الأداة.

الاستخدام:
    python manage.py check_branch_scoping            # يطبع فقط
    python manage.py check_branch_scoping --strict    # exit code 1 عند وجود مخالفات (للـ CI)
"""
import ast
import re
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management.base import BaseCommand


# موديلات حساسة للفرع — كل من يملك حقل branch مباشر أو غير مباشر (عبر stock/
# customer/agent/original_invoice...) ويحمل بيانات تشغيلية/مالية لكل فرع.
BRANCH_SENSITIVE_MODELS = [
    'SaleInvoice', 'SaleReturn', 'SaleQuote', 'SaleInvoiceLine', 'SalePayment', 'CustomerLedger',
    'PurchaseInvoice', 'PurchaseReturn', 'PurchaseRFQ', 'PurchasePayment', 'SupplierLedger',
    'Stock', 'StockQuantity', 'StockTransfer', 'Stocktake', 'StockDestruction', 'ManufacturingOrder',
    'Customer', 'Supplier', 'Agent',
    'Treasury', 'TreasuryMovement', 'TreasuryTransfer',
    'BankAccount', 'BankAccountMovement', 'BankAccountTransfer', 'TreasuryBankTransfer',
    'Employee', 'EmployeeAdvance', 'EmployeeSalaryPayment',
    'Expense',
]

BRANCH_FILTER_MARKERS = ('for_branch(', 'filter_by_branch_via(', 'resolve_report_scope(', 'enforce_branch_ownership(', 'enforce_transfer_branch_ownership(')
EXEMPT_DECORATOR_NAMES = ('deny_branch_scoped', 'branch_scope_exempt')

# ملفات/أنماط تُستثنى من المسح كلياً (لا تتعامل مع طلبات HTTP مباشرة)
SKIP_DIR_NAMES = {'migrations', 'tests', '__pycache__'}


def _decorator_name(dec_node):
    """يُرجع اسم الديكوريتر من عقدة AST، سواء كان @name أو @name(...)."""
    target = dec_node.func if isinstance(dec_node, ast.Call) else dec_node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _iter_view_files():
    base = Path(settings.BASE_DIR) / 'apps'
    for views_file in sorted(base.glob('*/views.py')):
        if any(part in SKIP_DIR_NAMES for part in views_file.parts):
            continue
        yield views_file


def _scan_file(path):
    """يُرجع قائمة مخالفات: (funcname, lineno, [models_matched])."""
    source = path.read_text(encoding='utf-8')
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    model_patterns = {
        m: re.compile(rf'\b{re.escape(m)}\.objects\b|get_object_or_404\(\s*{re.escape(m)}\b')
        for m in BRANCH_SENSITIVE_MODELS
    }
    violations = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue

        # دالة داخلية بلا request (مثال: _delete_tenant_data(tenant) تُستدعى من
        # view آخر) لا يمكنها التحقق من فرع الطلب أصلاً — ليست مسؤولة عن
        # الإنفاذ بنفسها؛ الفلترة يجب أن تكون قد حدثت في الـ view المستدعي.
        arg_names = {a.arg for a in node.args.args}
        if 'request' not in arg_names:
            continue

        decorator_names = {n for n in (_decorator_name(d) for d in node.decorator_list) if n}
        if decorator_names & set(EXEMPT_DECORATOR_NAMES):
            continue

        func_source = ast.get_source_segment(source, node) or ''
        matched_models = [m for m, pat in model_patterns.items() if pat.search(func_source)]
        if not matched_models:
            continue

        if any(marker in func_source for marker in BRANCH_FILTER_MARKERS):
            continue

        violations.append((node.name, node.lineno, matched_models))

    return violations


class Command(BaseCommand):
    help = 'يفحص apps/*/views.py بحثاً عن دوال تلمس موديلات حساسة للفرع بلا فلترة/استثناء موثَّق (خطة Enterprise، القسم 3.4).'

    def add_arguments(self, parser):
        parser.add_argument('--strict', action='store_true', help='exit code 1 عند وجود أي مخالفة (للاستخدام في CI).')

    def handle(self, *args, **options):
        total_violations = 0
        for views_file in _iter_view_files():
            violations = _scan_file(views_file)
            if not violations:
                continue
            rel = views_file.relative_to(settings.BASE_DIR)
            for funcname, lineno, models in violations:
                total_violations += 1
                self.stdout.write(self.style.WARNING(
                    f'{rel}:{lineno}: {funcname}() يلمس {", ".join(models)} بلا for_branch/'
                    f'filter_by_branch_via/resolve_report_scope ولا استثناء موثَّق (branch_scope_exempt).'
                ))

        if total_violations == 0:
            self.stdout.write(self.style.SUCCESS('لا مخالفات — كل الدوال التي تلمس موديلات حساسة للفرع مُفلترة أو مُستثناة صراحة.'))
        else:
            self.stdout.write(self.style.WARNING(f'\nإجمالي المخالفات: {total_violations}'))

        if options['strict'] and total_violations:
            raise SystemExit(1)
