"""
Permission decorators for protecting views based on permission keys.
استخدم decorators هذه على views لفحص الصلاحيات من مفاتيح JSON
"""
from functools import wraps
from django.shortcuts import redirect
from django.http import JsonResponse
from .models import User
from .permissions import access_allowed


def _is_ajax(request):
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.headers.get('Accept', '').startswith('application/json')
        or request.content_type == 'application/json'
    )


def _deny(request):
    if _is_ajax(request):
        return JsonResponse({'success': False, 'message': 'ليس لديك صلاحية للقيام بهذا الإجراء'}, status=403, json_dumps_params={'ensure_ascii': False})
    return redirect('core:no_permission')


def require_permission(permission_key):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            if request.user.has_perm_key(permission_key):
                return view_func(request, *args, **kwargs)
            return _deny(request)
        # ميتاداتا فقط للاستبطان (مثلاً اختبار مصفوفة الصلاحيات) — لا تؤثر
        # على السلوك، انظر apps/accounts/tests_permission_matrix.py.
        # بعض الـ views تُكدِّس أكثر من @require_permission فوق بعضها (كل
        # طبقة تفحص وترفض باستقلالية، فالنتيجة الفعلية AND بين المفاتيح) —
        # نجمعها هنا بدل الاكتفاء بأقرب طبقة للـ view المُسجَّل في urls.py.
        existing_keys = getattr(view_func, '_required_permission_keys', ())
        existing_mode = getattr(view_func, '_permission_check_mode', 'all')
        if existing_mode == 'all':
            wrapper._required_permission_keys = tuple(existing_keys) + (permission_key,)
        else:
            wrapper._required_permission_keys = (permission_key,)
        wrapper._permission_check_mode = 'all'
        return wrapper
    return decorator


def require_scoped_permission(branch_key, central_key):
    """
    صلاحية بنطاقين لشاشات المشتريات/الموردين/الشحنات في النمطين الهجين والمركزي:
      - من يملك branch_key: نطاق الفرع/المعتاد (request.central_scope = False)، إلا مستخدم الإدارة
        (بلا فرع) في النمط «مركزي» فنطاقه المخزن المركزي (central_scope = True) بصلاحياته المعتادة.
      - من لا يملكه لكن يملك central_key، ونمط المشتريات "هجين" أو "مركزي"، وهو مستخدم
        مركزي (بلا فرع): نطاق الإدارة المركزية (request.central_scope = True).
      - غير ذلك: مرفوض. (بدون هذا الفحص كان مدير النشاط، الذي يملك المفتاح المركزي
        في أي نمط، يعبر إلى نطاق "كل الفروع" المعتاد.)
    تنفّذ الـ view نفسها نطاقها بقراءة request.central_scope.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            user = request.user
            tenant = getattr(request, 'tenant', None)
            branchless = getattr(request, 'branch', None) is None
            if user.is_superuser or user.has_perm_key(branch_key):
                # النمط المركزي: مستخدم الإدارة (بلا فرع) بالصلاحية المعتادة يعمل بنطاق المخزن المركزي.
                request.central_scope = bool(
                    tenant is not None and tenant.is_central_purchasing() and branchless)
                return view_func(request, *args, **kwargs)
            if (tenant is not None and tenant.has_central_warehouse() and branchless
                    and central_key is not None and user.has_perm_key(central_key)):
                request.central_scope = True
                return view_func(request, *args, **kwargs)
            return _deny(request)
        wrapper._required_permission_keys = (branch_key,) if central_key is None else (branch_key, central_key)
        wrapper._permission_check_mode = 'any'
        return wrapper
    return decorator


def require_account_report(std_key, ho_key='view_head_office_account_reports'):
    """
    تقارير الخزائن والحسابات البنكية (كشف الحساب والحركات) بنطاقين في نسخة المؤسسات:
      - من يملك std_key: النطاق المعتاد (request.account_report_ho = False).
      - مدير النشاط/موظف الإدارة (بلا فرع) بمفتاح الإدارة فقط: خزائن الإدارة المركزية وحساباتها البنكية وحدها
        (request.account_report_ho = True).
      - غير ذلك: مرفوض.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            user = request.user
            tenant = getattr(request, 'tenant', None)
            if user.has_perm_key(std_key):
                request.account_report_ho = False
            elif (user.has_perm_key(ho_key) and tenant is not None and tenant.is_enterprise()
                  and getattr(request, 'branch', None) is None):
                request.account_report_ho = True
            else:
                return _deny(request)
            return view_func(request, *args, **kwargs)
        wrapper._required_permission_keys = (ho_key, std_key)
        wrapper._permission_check_mode = 'any'
        return wrapper
    return decorator


def require_head_office_scoped_permission(branch_key, head_office_key):
    """
    صلاحية بنطاقين لشاشة المستخدمين في نسخة المؤسسات:
      - من يملك branch_key: النطاق المعتاد (request.head_office_scope = False).
      - من لا يملكه لكن يملك head_office_key، والنسخة multi_branch، وهو مستخدم بلا فرع
        (مدير النشاط أو موظف إدارة): نطاق إدارة النشاط (request.head_office_scope = True)
        — مستخدمو الإدارة بلا فرع فقط، ومجموعات «إدارة النشاط» فقط.
      - غير ذلك: مرفوض.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            user = request.user
            if user.is_superuser or user.has_perm_key(branch_key):
                request.head_office_scope = False
                return view_func(request, *args, **kwargs)
            tenant = getattr(request, 'tenant', None)
            if (tenant is not None and tenant.is_enterprise()
                    and getattr(request, 'branch', None) is None
                    and user.has_perm_key(head_office_key)):
                request.head_office_scope = True
                return view_func(request, *args, **kwargs)
            return _deny(request)
        wrapper._required_permission_keys = (branch_key, head_office_key)
        wrapper._permission_check_mode = 'any'
        return wrapper
    return decorator


def require_any_permission(*permission_keys):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            for perm_key in permission_keys:
                if request.user.has_perm_key(perm_key):
                    return view_func(request, *args, **kwargs)
            return _deny(request)
        wrapper._required_permission_keys = permission_keys
        wrapper._permission_check_mode = 'any'
        return wrapper
    return decorator


def require_all_permissions(*permission_keys):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            for perm_key in permission_keys:
                if not request.user.has_perm_key(perm_key):
                    return _deny(request)
            return view_func(request, *args, **kwargs)
        wrapper._required_permission_keys = permission_keys
        wrapper._permission_check_mode = 'all'
        return wrapper
    return decorator


def require_capability(capability_name):
    """
    يتحقق من أن قدرة الـ tenant (TenantCapabilities.<capability_name>) مفعّلة قبل
    تنفيذ الـ view — يمنع الوصول المباشر (برابط معروف) لميزة كانت مخفية فقط من
    القائمة الجانبية لعدم توفرها في باقة/قدرات هذا الـ tenant. بخلاف
    require_permission، هذا تقييد على مستوى الاشتراك نفسه فلا يُستثنى منه حتى
    مدير الحساب (tenant_admin) — ويتجاوزه فقط السوبريوزر (فريق الدعم/المنصة).
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            tenant = getattr(request, 'tenant', None)
            if not tenant:
                return view_func(request, *args, **kwargs)
            caps = getattr(tenant, 'capabilities', None)
            if getattr(caps, capability_name, False):
                return view_func(request, *args, **kwargs)
            return _deny(request)
        return wrapper
    return decorator


def deny_branch_scoped(view_func):
    """
    يمنع أي مستخدم مرتبط بفرع (request.branch) من الوصول لهذا الـ view مهما
    كانت صلاحياته الممنوحة عبر مجموعات الصلاحيات — لصفحات على مستوى النشاط
    بالكامل (الاشتراك، سعر الصرف، إعدادات النشاط) لا ينبغي أن يصل إليها إلا
    مدير النشاط أو السوبريوزر، حتى لو أُخفيت من القائمة الجانبية/الناف بار فقط.
    نفس فلسفة BRANCH_BLOCKED_KEYS في apps/accounts/permissions.py لكن على
    مستوى الـ view بدل مفتاح صلاحية.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        if getattr(request, 'branch', None):
            return _deny(request)
        return view_func(request, *args, **kwargs)
    return wrapper


def branch_scope_exempt(reason):
    """
    علامة توثيقية صريحة (لا سلوك جديد) على view يلمس موديلاً حساساً للفرع
    (فاتورة، مخزون، خزينة...) لكنه *متعمَّد* أن يبقى بلا فلترة for_branch/
    filter_by_branch_via — مثال: شاشة تُنشئ سجلاً جديداً من الصفر ولا تقرأ
    بيانات فروع أخرى أصلاً، أو شاشة محمية أصلاً بـ deny_branch_scoped فلا
    يصلها مستخدم بفرع من الأساس.

    الهدف الوحيد: أداة التدقيق check_branch_scoping (خطة تنفيذ Enterprise،
    القسم 3.4) تتعرّف على هذا الديكوريتر كاستثناء موثَّق بدل أن تُبلِّغ عنه
    كنسيان صامت. لا تُستخدم هذه الدالة لأي غرض آخر.

    الاستخدام: @branch_scope_exempt('سبب الاستثناء بالعربي')
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            return view_func(request, *args, **kwargs)
        wrapper._branch_scope_exempt = True
        wrapper._branch_scope_exempt_reason = reason
        return wrapper
    return decorator


def require_plan_feature(feature_name):
    """مثل require_capability لكن للتحقق من ميزة باقة الاشتراك (Tenant.plan_allows)."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            tenant = getattr(request, 'tenant', None)
            if not tenant or tenant.plan_allows(feature_name):
                return view_func(request, *args, **kwargs)
            return _deny(request)
        return wrapper
    return decorator
