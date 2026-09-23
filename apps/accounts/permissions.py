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


def _load_structured_schema():
    """
    يقرأ apps/accounts/permissions_schema.json كما هو: قائمة sections مرتبة
    تماماً بترتيب القائمة الجانبية (راجع apps/core/templates/components/
    sidebar.html)، كل قسم فيه معرّف ثابت (key) بالإنجليزية، اسم ثنائي اللغة
    (name.ar/name.en)، وسوم اختيارية تحدد من يرى هذا القسم:
      - role_scope: 'admin_only' | 'branch_ops' | غائبة (= الاثنين معاً).
      - versions: قائمة version_type المسموحة، أو غائبة (= كل النسخ).
      - requires_capability / requires_plan_feature / requires_flag: اسم
        الخاصية على TenantCapabilities / Tenant.plan_allows / Tenant نفسه.
    كل صلاحية {key, label: {ar, en}} قد تحمل نفس الوسوم الثلاثة الأخيرة
    لتضييق صلاحية واحدة داخل قسم أوسع (مثال: transfer_treasuries داخل
    الخزائن، أو view_stocks_controlled_substances_report داخل تقارير المخزن).

    هذا هو المصدر الوحيد لشجرة الصلاحيات — أي شاشة/دالة أخرى في المشروع يجب
    أن تمر عبر الدوال أدناه بدل قراءة الملف مباشرة.
    """
    schema_path = get_permission_schema_path()
    with schema_path.open('r', encoding='utf-8-sig') as handle:
        data = json.load(handle)
    return data['sections']


def load_permission_schema():
    """
    توافقاً مع الاستخدام القديم: يرجع الشكل المسطّح {اسم_القسم_عربي: {مفتاح:
    تسمية_عربية}} بدون أي فلترة حسب الـ tenant — نفس شكل ونتيجة الدالة قبل
    إعادة الهيكلة تماماً.
    """
    return _render_legacy_shape(_load_structured_schema())


def get_permission_schema(lang='ar'):
    return _render_legacy_shape(_load_structured_schema(), lang)


def _render_legacy_shape(sections, lang='ar'):
    """
    يحوّل قائمة sections المهيكلة إلى الشكل القديم {اسم_القسم: {مفتاح:
    تسمية}} بلغة lang، بنفس ترتيب القائمة الجانبية — وهذا ما يجعل عرض
    الصلاحيات في صفحة المجموعات (permission_groups.js عبر Object.entries)
    يظهر تلقائياً بترتيب السايد بار دون أي تعديل على تلك الصفحة.
    """
    return {
        section['name'][lang]: {
            perm['key']: perm['label'][lang] for perm in section['permissions']
        }
        for section in sections
    }


def get_permission_keys():
    return [perm['key'] for section in _load_structured_schema() for perm in section['permissions']]


def get_all_permissions():
    return get_permission_keys()


def get_permission_choices(lang='ar'):
    return [
        (perm['key'], perm['label'][lang])
        for section in _load_structured_schema()
        for perm in section['permissions']
    ]


# تصنيفات حصرية لمدير النشاط (مالك الاشتراك) — لا تُمنح تلقائياً لمشرف الفرع
# لأنها إما تخص المنشأة ككل (إعدادات، سعر الصرف) أو قد تُستخدم للتصعيد
# الصلاحيات (مستخدمين، مجموعات صلاحيات) أو إدارة الفروع نفسها، أو مورد
# واحد مشترك بين كل الفروع (المتجر الإلكتروني — راجع BRANCH_SCOPING.md §9).
# مُشتقّة الآن من role_scope=="admin_only" في الملف بدل قائمة يدوية منفصلة —
# يستحيل أن تختلف عن الملف بعد اليوم (لا مجال لنسيان تحديثها).
BRANCH_SUPERVISOR_EXCLUDED_CATEGORIES = {
    section['name']['ar']
    for section in _load_structured_schema()
    if section.get('role_scope') == 'admin_only'
}


def get_branch_supervisor_permission_keys():
    """
    كل مفاتيح الصلاحيات المتاحة لمشرف الفرع تلقائياً: كل الصلاحيات ما عدا
    التصنيفات الحصرية لمدير النشاط (role_scope == 'admin_only').
    """
    return [
        perm['key']
        for section in _load_structured_schema()
        if section.get('role_scope') != 'admin_only'
        for perm in section['permissions']
    ]


# تصنيفات عمليات الفرع اليومية التي تُخفى عن مدير النشاط في نسخة المؤسسات
# تحديداً (Tenant.is_enterprise()) — هو يبقى إدارياً (فروع، مخازن، مستخدمين،
# مجموعات صلاحيات، منتجات، موظفين، إعدادات، تقارير) بينما هذه العمليات
# التشغيلية اليومية تبقى حصراً لمشرف/موظفي كل فرع. باقي الباقات
# (single_store/multi_stock) غير متأثرة إطلاقاً — راجع خطة "تقييد صلاحيات
# مدير النشاط في نسخة المؤسسات". مُشتقّة من role_scope=="branch_ops".
ENTERPRISE_OWNER_EXCLUDED_CATEGORIES = {
    section['name']['ar']
    for section in _load_structured_schema()
    if section.get('role_scope') == 'branch_ops'
}


def get_enterprise_owner_permission_keys():
    """
    كل مفاتيح الصلاحيات المتاحة لمدير النشاط في نسخة المؤسسات: كل الصلاحيات
    ما عدا عمليات الفرع اليومية (role_scope == 'branch_ops').
    """
    return [
        perm['key']
        for section in _load_structured_schema()
        if section.get('role_scope') != 'branch_ops'
        for perm in section['permissions']
    ]


# مفاتيح محظورة على أي مستخدم مربوط بفرع (request.branch/user.branch) بصرف
# النظر عن دوره أو مجموعة الصلاحيات المسندة له — قيد مطلق (hard rule) وليس
# افتراضاً قابلاً للتعديل عبر PermissionGroup: المنتج/الصنف كتالوج مركزي
# واحد يشترك فيه كل الفروع (السعر والوصف والتصنيف... إلخ)، فلا يجوز لأي فرع
# إضافته أو تعديله أو حذفه — فقط عرض تفاصيله وكشف حركته (راجع
# User.has_perm_key/get_permission_keys في apps/accounts/models.py). هذا قيد
# منتج ثابت لا علاقة له بتنظيم الأقسام، فيبقى صريحاً هنا بدل ترميزه في الملف.
BRANCH_BLOCKED_KEYS = {
    'add_items',
    'change_items',
    'delete_items',
    'import_items',
}


def _apply_tenant_filter(sections, *, version_type, has_capability, plan_allows, get_flag):
    """
    دالة نقية: تفلتر شجرة الصلاحيات حسب وسوم versions/requires_capability/
    requires_plan_feature/requires_flag على مستوى القسم ثم المفتاح، عبر
    استدعاءات/قيم بسيطة بدل كائن Tenant حي — هذا يسمح لمولّد ملفات المرجع
    (management command) باستدعائها بقدرات "افتراضية قصوى" دون الحاجة لكائن
    Tenant وهمي غير محفوظ في قاعدة البيانات.
    """
    def section_visible(section):
        if version_type is not None:
            versions = section.get('versions')
            if versions and version_type not in versions:
                return False
        capability = section.get('requires_capability')
        if capability and not has_capability(capability):
            return False
        feature = section.get('requires_plan_feature')
        if feature and not plan_allows(feature):
            return False
        flag = section.get('requires_flag')
        if flag and not get_flag(flag):
            return False
        return True

    def perm_visible(perm):
        capability = perm.get('requires_capability')
        if capability and not has_capability(capability):
            return False
        feature = perm.get('requires_plan_feature')
        if feature and not plan_allows(feature):
            return False
        flag = perm.get('requires_flag')
        if flag and not get_flag(flag):
            return False
        return True

    result = []
    for section in sections:
        if not section_visible(section):
            continue
        visible_perms = [perm for perm in section['permissions'] if perm_visible(perm)]
        if not visible_perms:
            continue
        result.append({**section, 'permissions': visible_perms})
    return result


def filter_schema_for_tenant(tenant, lang='ar'):
    """
    يحذف من شجرة الصلاحيات أي تصنيف/مفتاح يخص ميزة غير متاحة لباقة أو قدرات
    هذا الـ tenant، عشان شاشة إدارة المجموعات ما تعرضش صلاحيات لمزايا هو أصلاً
    ما يقدرش يستخدمها. نفس الشروط المستخدمة لإخفاء روابط القائمة الجانبية
    (apps/core/templates/components/sidebar.html) — لازم يفضلوا متطابقين.
    """
    caps = getattr(tenant, 'capabilities', None)
    sections = _apply_tenant_filter(
        _load_structured_schema(),
        version_type=getattr(tenant, 'version_type', None),
        has_capability=lambda name: bool(getattr(caps, name, False)),
        plan_allows=tenant.plan_allows,
        get_flag=lambda name: bool(getattr(tenant, name, False)),
    )
    return _render_legacy_shape(sections, lang)


def access_allowed(user_group_id, perm):
    from .models import PermissionGroup

    try:
        group = PermissionGroup.objects.get(id=user_group_id)
        return bool(group.permissions.get(perm, False))
    except PermissionGroup.DoesNotExist:
        return False
