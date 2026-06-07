"""
Context Processors - معالجات السياق
توفر متغيرات عامة لجميع Templates
"""
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
            ).count()
        except Exception:
            context['store_pending_count'] = 0

    return context


def app_context(request):
    """
    معلومات عامة عن التطبيق
    """
    from datetime import datetime
    return {
        'app_name': 'EnjazIMS',
        'app_version': '1.0.0',
        'app_description': 'نظام إدارة المخزون ونقاط البيع',
        'current_year': datetime.now().year,
    }


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
