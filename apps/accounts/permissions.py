import json
from pathlib import Path

from django.conf import settings


APP_SCHEMA_PATH = Path(__file__).resolve().parent / 'permissions_schema.json'
ROOT_SCHEMA_PATH = Path(settings.BASE_DIR) / 'perms.json'


def get_permission_schema_path():
    if APP_SCHEMA_PATH.exists():
        return APP_SCHEMA_PATH
    if ROOT_SCHEMA_PATH.exists():
        return ROOT_SCHEMA_PATH
    raise FileNotFoundError(
        'لم يتم العثور على ملف صلاحيات. يُرجى إنشاء ملف permissions_schema.json داخل app accounts أو perms.json في جذر المشروع.'
    )


def load_permission_schema():
    schema_path = get_permission_schema_path()
    with schema_path.open('r', encoding='utf-8-sig') as handle:
        return json.load(handle)


def get_permission_schema():
    return load_permission_schema()


def get_permission_keys():
    schema = load_permission_schema()
    keys = []
    for section in schema.values():
        keys.extend(section.keys())
    return keys


def get_all_permissions():
    return get_permission_keys()


# تصنيفات حصرية لمدير النشاط (مالك الاشتراك) — لا تُمنح تلقائياً لمشرف الفرع
# لأنها إما تخص المنشأة ككل (إعدادات، سعر الصرف) أو قد تُستخدم للتصعيد
# الصلاحيات (مستخدمين، مجموعات صلاحيات) أو إدارة الفروع نفسها، أو مورد
# واحد مشترك بين كل الفروع (المتجر الإلكتروني — راجع BRANCH_SCOPING.md §9).
BRANCH_SUPERVISOR_EXCLUDED_CATEGORIES = {
    'إعدادات النشاط التجاري',
    'المستخدمين',
    'المجموعات والصلاحيات',
    'الفروع',
    'المتجر الإلكتروني',
}


def get_branch_supervisor_permission_keys():
    """
    كل مفاتيح الصلاحيات المتاحة لمشرف الفرع تلقائياً: كل الصلاحيات ما عدا
    التصنيفات الحصرية لمدير النشاط (BRANCH_SUPERVISOR_EXCLUDED_CATEGORIES).
    """
    schema = load_permission_schema()
    keys = []
    for section, perms in schema.items():
        if section in BRANCH_SUPERVISOR_EXCLUDED_CATEGORIES:
            continue
        keys.extend(perms.keys())
    return keys


# تصنيفات عمليات الفرع اليومية التي تُخفى عن مدير النشاط في نسخة المؤسسات
# تحديداً (Tenant.is_enterprise()) — هو يبقى إدارياً (فروع، مخازن، مستخدمين،
# مجموعات صلاحيات، منتجات، موظفين، إعدادات، تقارير) بينما هذه العمليات
# التشغيلية اليومية تبقى حصراً لمشرف/موظفي كل فرع. باقي الباقات
# (single_store/multi_stock) غير متأثرة إطلاقاً — راجع خطة "تقييد صلاحيات
# مدير النشاط في نسخة المؤسسات".
ENTERPRISE_OWNER_EXCLUDED_CATEGORIES = {
    'العملاء',
    'الموردين',
    'إتلاف المخزون',
    'المبيعات',
    'المشتريات',
    'المصروفات',
    'الخزائن',
    'الحسابات البنكية',
    'المناديب',
    'التأمين',
    'رواتب الموظفين',
    'سلف الموظفين',
    'حوافز الموظفين',
}


def get_enterprise_owner_permission_keys():
    """
    كل مفاتيح الصلاحيات المتاحة لمدير النشاط في نسخة المؤسسات: كل الصلاحيات
    ما عدا عمليات الفرع اليومية (ENTERPRISE_OWNER_EXCLUDED_CATEGORIES).
    """
    schema = load_permission_schema()
    keys = []
    for section, perms in schema.items():
        if section in ENTERPRISE_OWNER_EXCLUDED_CATEGORIES:
            continue
        keys.extend(perms.keys())
    return keys


# مفاتيح محظورة على أي مستخدم مربوط بفرع (request.branch/user.branch) بصرف
# النظر عن دوره أو مجموعة الصلاحيات المسندة له — قيد مطلق (hard rule) وليس
# افتراضاً قابلاً للتعديل عبر PermissionGroup: المنتج/الصنف كتالوج مركزي
# واحد يشترك فيه كل الفروع (السعر والوصف والتصنيف... إلخ)، فلا يجوز لأي فرع
# إضافته أو تعديله أو حذفه — فقط عرض تفاصيله وكشف حركته (راجع
# User.has_perm_key/get_permission_keys في apps/accounts/models.py).
BRANCH_BLOCKED_KEYS = {
    'add_items',
    'change_items',
    'delete_items',
    'import_items',
}


def get_permission_choices():
    schema = load_permission_schema()
    flatten = []
    for section, permissions in schema.items():
        for key, label in permissions.items():
            flatten.append((key, label))
    return flatten


def filter_schema_for_tenant(schema, tenant):
    """
    يحذف من شجرة الصلاحيات أي تصنيف/مفتاح يخص ميزة غير متاحة لباقة أو قدرات
    هذا الـ tenant، عشان شاشة إدارة المجموعات ما تعرضش صلاحيات لمزايا هو أصلاً
    ما يقدرش يستخدمها. نفس الشروط المستخدمة لإخفاء روابط القائمة الجانبية
    (apps/core/templates/components/sidebar.html) — لازم يفضلوا متطابقين.
    """
    caps = getattr(tenant, 'capabilities', None)

    def has_cap(name):
        return bool(getattr(caps, name, False))

    # تصنيفات كاملة تُحذف لو الميزة غير متاحة
    hidden_categories = set()
    if getattr(tenant, 'version_type', None) != 'multi_branch':
        hidden_categories.add('الفروع')
    if not (has_cap('has_agents_module') and tenant.plan_allows('agents')):
        hidden_categories.add('المناديب')
        hidden_categories.add('تقارير المناديب')
    if not has_cap('has_insurance_billing'):
        hidden_categories.add('التأمين')
    if not tenant.plan_allows('ai_assistant'):
        hidden_categories.add('الذكاء الاصطناعي')
    if not tenant.plan_allows('store'):
        hidden_categories.add('المتجر الإلكتروني')
    if not has_cap('has_expiry_alerts'):
        hidden_categories.add('إتلاف المخزون')

    # مفاتيح مفردة تُحذف داخل تصنيف يفضل ظاهر
    hidden_keys = set()
    if not getattr(tenant, 'hard_currency_mode', False):
        hidden_keys.add('transfer_treasuries')
    if not has_cap('has_drug_classification'):
        hidden_keys.add('view_stocks_controlled_substances_report')

    return {
        section: {k: v for k, v in perms.items() if k not in hidden_keys}
        for section, perms in schema.items()
        if section not in hidden_categories
    }


def access_allowed(user_group_id, perm):
    from .models import PermissionGroup

    try:
        group = PermissionGroup.objects.get(id=user_group_id)
        return bool(group.permissions.get(perm, False))
    except PermissionGroup.DoesNotExist:
        return False
