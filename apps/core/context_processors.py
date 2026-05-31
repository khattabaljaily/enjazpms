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
