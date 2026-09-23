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
            if request.user.is_superuser or request.user.is_tenant_admin:
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


def require_any_permission(*permission_keys):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if request.user.is_superuser or request.user.is_tenant_admin:
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
            if request.user.is_superuser or request.user.is_tenant_admin:
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
    يتحقق إن قدرة الـ tenant (TenantCapabilities.<capability_name>) مفعّلة قبل
    تنفيذ الـ view — يمنع الوصول المباشر (برابط معروف) لميزة كانت مخفية بس من
    القائمة الجانبية لعدم توفرها في باقة/قدرات هذا الـ tenant. بخلاف
    require_permission، ده تقييد على مستوى الاشتراك نفسه فلا يُستثنى منه حتى
    مدير الحساب (tenant_admin) — بس السوبريوزر (فريق الدعم/المنصة) بيتجاوزه.
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
