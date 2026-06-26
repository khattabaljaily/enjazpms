"""
Context Processors - معالجات السياق
توفر متغيرات عامة لجميع Templates
"""
from django.utils import timezone
from apps.core.models import Settings, TenantCapabilities


def tenant_context(request):
    """
    إضافة معلومات الـ Tenant إلى كل template
    """
    context = {
        'current_tenant': None,
        'tenant_settings': None,
        'tenant_capabilities': None,
    }

    if hasattr(request, 'tenant') and request.tenant:
        tenant = request.tenant
        context['current_tenant'] = tenant

        try:
            context['tenant_settings'] = Settings.objects.get(tenant=tenant)
        except Settings.DoesNotExist:
            context['tenant_settings'] = Settings.objects.create(tenant=tenant)

        try:
            context['tenant_capabilities'] = TenantCapabilities.objects.get(tenant=tenant)
        except TenantCapabilities.DoesNotExist:
            caps = TenantCapabilities.from_business_type(tenant)
            caps.save()
            context['tenant_capabilities'] = caps

        try:
            from apps.store.models import OnlineOrder
            context['store_pending_count'] = OnlineOrder.objects.filter(
                tenant=tenant, status='pending'
            ).count() if tenant.plan_allows('store') else 0
        except Exception:
            context['store_pending_count'] = 0

        context['plan_allows_ai'] = tenant.plan_allows('ai_assistant')
        context['plan_allows_store'] = tenant.plan_allows('store')
        context['plan_allows_smart_tips'] = tenant.plan_allows('smart_tips')
        context['plan_allows_agents'] = tenant.plan_allows('agents')

    return context


def app_context(request):
    """
    معلومات عامة عن التطبيق
    """
    return {
        'app_name': 'EnjazIMS',
        'app_version': '1.0.0',
        'app_description': 'نظام إدارة المخزون ونقاط البيع',
        'current_year': timezone.localtime().year,
    }


def impersonation_context(request):
    """وضع انتحال الهوية — يُفعّل عندما يسجل المشرف دخوله كمستخدم آخر"""
    impersonator_id = request.session.get('_impersonator_id') if hasattr(request, 'session') else None
    return {'impersonating': bool(impersonator_id)}


def training_context(request):
    """حقن مسار ملف التدريب المناسب للصفحة الحالية تلقائياً"""
    if not (hasattr(request, 'tenant') and request.tenant):
        return {'training_template': None}
    if request.user.is_superuser:
        return {'training_template': None}
    try:
        match = request.resolver_match
        if not match:
            return {'training_template': None}
        namespace = match.namespace or ''
        url_name = match.url_name or ''
        if not namespace or not url_name:
            return {'training_template': None}
        candidate = f"training/{namespace}/{url_name}.html"
        from django.template.loader import get_template
        from django.template import TemplateDoesNotExist
        try:
            get_template(candidate)
            return {'training_template': candidate}
        except TemplateDoesNotExist:
            return {'training_template': None}
    except Exception:
        return {'training_template': None}


def platform_context(request):
    """إعدادات النظام — الإشعار العام ووضع الصيانة"""
    from django.core.cache import cache
    ps = cache.get('platform_settings_ctx')
    if ps is None:
        try:
            from apps.core.models import PlatformSettings
            obj = PlatformSettings.objects.filter(pk=1).values(
                'announcement_active', 'announcement_text', 'announcement_type',
            ).first()
            ps = obj or {}
            cache.set('platform_settings_ctx', ps, 120)
        except Exception:
            ps = {}
    return {'platform_cfg': ps}
