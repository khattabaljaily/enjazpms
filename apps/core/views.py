"""
Core Views - Dashboard وصفحات النظام الأساسية
"""
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from datetime import datetime, timedelta


@login_required
def dashboard(request):
    """الصفحة الرئيسية - Dashboard"""
    
    tenant = request.tenant
    
    # Basic stats
    stats = {
        'total_users': 0,
        'total_products': 0,
        'total_customers': 0,
        'total_suppliers': 0,
        'today_sales': 0,
        'this_month_sales': 0,
        'low_stock_items': 0,
        'expired_items': 0,
    }
    
    # Get users count for this tenant
    from apps.accounts.models import User
    if tenant:
        stats['total_users'] = User.objects.filter(tenant=tenant).count()
    
    # TODO: Add more stats when other apps are created
    # stats['total_products'] = Item.objects.filter(tenant=tenant).count()
    # stats['total_customers'] = Customer.objects.filter(tenant=tenant).count()
    # etc...
    
    context = {
        'stats': stats,
        'tenant': tenant,
    }
    
    return render(request, 'core/dashboard.html', context)


def subscription_expired(request):
    """صفحة انتهاء الاشتراك"""
    return render(request, 'core/subscription_expired.html')


def no_tenant(request):
    """صفحة عدم وجود tenant"""
    return render(request, 'core/no_tenant.html')


@login_required
def tenant_settings(request):
    """إعدادات النشاط التجاري"""
    return render(request, 'core/tenant_settings.html')


@login_required
def subscription_info(request):
    """معلومات الاشتراك"""
    tenant = request.tenant
    
    context = {
        'tenant': tenant,
        'days_remaining': tenant.days_until_expiry() if tenant else None,
    }
    
    return render(request, 'core/subscription.html', context)
