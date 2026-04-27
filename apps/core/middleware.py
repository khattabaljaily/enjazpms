"""
Core Middleware - الوسائط الأساسية
"""
from django.shortcuts import redirect
from django.urls import reverse
from apps.core.models import Tenant


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
