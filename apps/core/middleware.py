"""
Core Middleware - الوسائط الأساسية
"""
import pytz
from django.shortcuts import redirect, render
from django.urls import reverse
from django.core.cache import cache
from django.utils import timezone
from apps.core.models import Tenant


class MaintenanceModeMiddleware:
    """
    يتحقق من وضع الصيانة — يعيد توجيه غير السوبر أدمن لصفحة الصيانة
    """

    EXEMPT_PATHS = ['/admin/', '/static/', '/media/', '/accounts/login/', '/accounts/api/login/', '/accounts/logout/']

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
            if request.user.is_superuser or getattr(request.user, 'is_platform_staff', False):
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
                    '/store/',
                ]
                if not any(request.path.startswith(path) for path in safe_paths):
                    return redirect(admin_dashboard_path)
            else:
                request.tenant = request.user.tenant
                
                # Check if tenant is active and subscription is valid
                if request.tenant:
                    if not request.tenant.is_active or not request.tenant.is_subscription_valid():
                        _expired_allowed = (
                            '/subscription/',
                            '/support/',
                            '/accounts/logout/',
                            '/static/',
                            '/media/',
                        )
                        if not any(request.path.startswith(p) for p in _expired_allowed):
                            return redirect('/subscription/')
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


class ActivityLogMiddleware:
    """
    Logs write operations (POST/DELETE) on tenant API endpoints.
    Only records successful responses (2xx). Runs async-safe via try/except.
    """

    # URL fragments → (action, model_name_ar)
    _PATTERNS = [
        ('/sales/invoices/',       'create',  'SaleInvoice',    'فاتورة مبيعات'),
        ('/sales/returns/',        'create',  'SaleReturn',     'مرتجع مبيعات'),
        ('/sales/quotes/',         'create',  'SaleQuote',      'عرض سعر'),
        ('/purchases/orders/',     'create',  'PurchaseInvoice','فاتورة مشتريات'),
        ('/purchases/returns/',    'create',  'PurchaseReturn', 'مرتجع مشتريات'),
        ('/stocks/transfers/',     'create',  'StockTransfer',  'تحويل مخزون'),
        ('/stocks/stocktakes/',    'create',  'StockTake',      'جرد مخزون'),
        ('/items/',                'create',  'Item',           'صنف'),
        ('/customers/',            'create',  'Customer',       'عميل'),
        ('/suppliers/',            'create',  'Supplier',       'مورد'),
        ('/expenses/',             'create',  'Expense',        'مصروف'),
    ]

    # Paths to skip entirely
    _SKIP = ('/static/', '/media/', '/admin/', '/accounts/login', '/accounts/logout',
             '/api/table/', '/api/detail/', '/reports/', '/notifications/')

    def __init__(self, get_response):
        self.get_response = get_response

    def _get_ip(self, request):
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')

    def __call__(self, request):
        response = self.get_response(request)

        # Only log for authenticated tenant users on write requests
        if request.method not in ('POST', 'DELETE', 'PUT', 'PATCH'):
            return response
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return response
        if request.user.is_superuser:
            return response

        tenant = getattr(request, 'tenant', None)
        if not tenant:
            return response

        # Skip non-200 and certain paths
        if response.status_code not in (200, 201, 204):
            return response
        path = request.path
        if any(path.startswith(s) for s in self._SKIP):
            return response

        # Match pattern
        action, model_name, name_ar = None, '', ''
        for fragment, act, mn, nar in self._PATTERNS:
            if fragment in path:
                action = act
                model_name = mn
                name_ar = nar
                break

        if not action:
            return response

        if request.method == 'DELETE':
            action = 'delete'
        elif request.method in ('PUT', 'PATCH'):
            action = 'update'

        verb = {'create': 'إنشاء', 'update': 'تعديل', 'delete': 'حذف'}.get(action, action)

        try:
            from apps.core.models import ActivityLog
            ActivityLog.objects.create(
                tenant=tenant,
                user=request.user,
                action=action,
                model_name=model_name,
                description=f'{verb} {name_ar}',
                ip_address=self._get_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            )
        except Exception:
            pass

        return response


class TermsMiddleware:
    """
    يمنع الدخول للنظام حتى يقبل المشترك اتفاقية الاستخدام.
    يُطبَّق على tenant_admin فقط عند أول دخول أو عند تحديث الاتفاقية.
    """

    _EXEMPT = (
        '/accounts/logout/',
        '/accounts/login/',
        '/accounts/api/login/',
        '/terms/',
        '/static/',
        '/media/',
        '/admin/',
        '/subscription/',
        '/no-tenant/',
        '/no-permission/',
        '/sw.js',
        '/manifest.json',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings as dj_settings

        if any(request.path.startswith(p) for p in self._EXEMPT):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return self.get_response(request)

        # Platform staff & superusers are exempt
        if request.user.is_superuser or getattr(request.user, 'is_platform_staff', False):
            return self.get_response(request)

        tenant = getattr(request, 'tenant', None)
        if not tenant:
            return self.get_response(request)

        current_version = dj_settings.TERMS_VERSION
        if tenant.terms_version != current_version or not tenant.terms_accepted_at:
            return redirect('/terms/')

        return self.get_response(request)


class TimezoneMiddleware:
    """
    تفعيل المنطقة الزمنية للمشترك لكل طلب — يجب أن يأتي بعد TenantMiddleware
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tzname = None
        if hasattr(request, 'tenant') and request.tenant:
            tzname = request.tenant.timezone

        if tzname:
            try:
                timezone.activate(pytz.timezone(tzname))
            except Exception:
                timezone.deactivate()
        else:
            timezone.deactivate()

        response = self.get_response(request)
        timezone.deactivate()
        return response
