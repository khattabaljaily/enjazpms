"""
Core Middleware - الوسائط الأساسية
"""
from django.shortcuts import redirect, render
from django.urls import reverse
from django.core.cache import cache
from apps.core.models import Tenant


class MaintenanceModeMiddleware:
    """
    يتحقق من وضع الصيانة — يعيد توجيه غير السوبر أدمن لصفحة الصيانة
    """

    EXEMPT_PATHS = ['/admin/', '/static/', '/media/', '/accounts/login/', '/accounts/logout/']

    def __init__(self, get_response):
        self.get_response = get_response

    def _is_maintenance(self):
        cached = cache.get('platform_maintenance_mode')
        if cached is None:
            try:
                from apps.core.models import PlatformSettings
                ps = PlatformSettings.objects.filter(pk=1).values('maintenance_mode', 'maintenance_message').first()
                if ps:
                    cache.set('platform_maintenance_mode', ps, 60)
                    return ps
            except Exception:
                pass
            return {'maintenance_mode': False, 'maintenance_message': ''}
        return cached

    def __call__(self, request):
        if any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
            return self.get_response(request)

        if request.user.is_authenticated and request.user.is_superuser:
            return self.get_response(request)

        ps = self._is_maintenance()
        if ps.get('maintenance_mode'):
            return render(request, 'core/maintenance.html',
                          {'message': ps.get('maintenance_message', '')}, status=503)

        return self.get_response(request)


class TenantMiddleware:
    """
    Middleware للـ Multi-Tenant
    يحدد الـ Tenant الحالي ويضيفه إلى request
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
    
    def __call__(self, request):
        # Skip middleware for superuser in admin
        if request.path.startswith('/admin/'):
            request.tenant = None
            return self.get_response(request)
        
        # Get tenant from user
        if request.user.is_authenticated:
            if request.user.is_superuser:
                request.tenant = None

                admin_dashboard_path = reverse('core:admin_dashboard')
                safe_paths = [
                    '/accounts/',
                    '/admin/',
                    admin_dashboard_path,
                    '/tenants/',
                    '/subscription/',
                    '/pricing/',
                    '/reports/',
                    '/subscription-expired/',
                    '/no-tenant/',
                    '/no-permission/',
                    '/settings/',
                    '/static/',
                    '/media/',
                    '/system/',
                    '/support/',
                    '/about/',
                    '/notifications/',
                    '/ai/',
                ]
                if not any(request.path.startswith(path) for path in safe_paths):
                    return redirect(admin_dashboard_path)
            else:
                request.tenant = request.user.tenant
                
                # Check if tenant is active and subscription is valid
                if request.tenant:
                    if not request.tenant.is_active or not request.tenant.is_subscription_valid():
                        # Redirect to subscription expired page
                        if not request.path.startswith('/subscription-expired/'):
                            return redirect('/subscription-expired/')
        else:
            request.tenant = None
        
        response = self.get_response(request)
        return response


class ActiveTenantMiddleware:
    """
    Middleware يتأكد من أن المستخدم لديه tenant نشط
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_urls = [
            '/accounts/',
            '/admin/',
            '/static/',
            '/media/',
            '/subscription-expired/',
            '/no-tenant/',
            '/store/',
        ]
    
    def __call__(self, request):
        # Check if path is exempt
        path = request.path
        if any(path.startswith(url) for url in self.exempt_urls):
            return self.get_response(request)
        
        # Check if user is authenticated and has tenant
        if request.user.is_authenticated and not request.user.is_superuser:
            if not hasattr(request.user, 'tenant') or not request.user.tenant:
                # User has no tenant, redirect to error page
                return redirect('/no-tenant/')
        
        response = self.get_response(request)
        return response
