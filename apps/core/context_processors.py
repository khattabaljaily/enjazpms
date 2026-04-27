"""
Context Processors - معالجات السياق
توفر متغيرات عامة لجميع Templates
"""
from apps.core.models import Settings


def tenant_context(request):
    """
    إضافة معلومات الـ Tenant إلى كل template
    """
    context = {
        'current_tenant': None,
        'tenant_settings': None,
    }
    
    if hasattr(request, 'tenant') and request.tenant:
        context['current_tenant'] = request.tenant
        
        # Get or create tenant settings
        try:
            settings = Settings.objects.get(tenant=request.tenant)
        except Settings.DoesNotExist:
            settings = Settings.objects.create(tenant=request.tenant)
        
        context['tenant_settings'] = settings
    
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
