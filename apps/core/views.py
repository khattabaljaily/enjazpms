"""
Core Views - Dashboard وصفحات النظام الأساسية
"""
import json
from decimal import Decimal, InvalidOperation

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Sum, Count, Q
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta

from .models import Settings


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
    
    # Customers stats
    from apps.customers.models import Customer
    if tenant:
        stats['total_customers'] = Customer.objects.filter(tenant=tenant).count()

    # TODO: Add more stats when other apps are created
    # stats['total_products'] = Item.objects.filter(tenant=tenant).count()
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
@require_POST
def tenant_settings_update_api(request):
    """API: تحديث إعدادات النشاط التجاري عبر AJAX."""
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط مرتبط بالمستخدم'}, status=400)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    section = str(payload.get('section', '')).strip()
    data = payload.get('data') or {}
    if section not in {'business', 'system'}:
        return JsonResponse({'success': False, 'message': 'نوع التحديث غير مدعوم'}, status=400)

    if section == 'business':
        tenant.name = str(data.get('name', tenant.name)).strip() or tenant.name
        tenant.email = str(data.get('email', tenant.email)).strip()
        tenant.phone = str(data.get('phone', tenant.phone)).strip()
        tenant.city = str(data.get('city', tenant.city)).strip()
        tenant.country = str(data.get('country', tenant.country)).strip() or tenant.country
        tenant.address = str(data.get('address', tenant.address)).strip()
        tenant.timezone = str(data.get('timezone', tenant.timezone)).strip() or tenant.timezone
        tenant.currency = str(data.get('currency', tenant.currency)).strip() or tenant.currency
        tenant.save(update_fields=['name', 'email', 'phone', 'city', 'country', 'address', 'timezone', 'currency', 'updated_at'])
        return JsonResponse({'success': True, 'message': 'تم تحديث بيانات المتجر بنجاح'})

    settings_obj, _ = Settings.objects.get_or_create(tenant=tenant)

    def _as_bool(value):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    def _as_decimal(value, fallback):
        try:
            cleaned = str(value).replace(',', '.').strip()
            return Decimal(cleaned)
        except (InvalidOperation, TypeError, ValueError):
            return fallback

    def _as_int(value, fallback):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    settings_obj.invoice_prefix = str(data.get('invoice_prefix', settings_obj.invoice_prefix)).strip()[:10] or 'INV'
    settings_obj.invoice_footer = str(data.get('invoice_footer', settings_obj.invoice_footer)).strip()
    settings_obj.tax_enabled = _as_bool(data.get('tax_enabled', settings_obj.tax_enabled))
    settings_obj.tax_value = _as_decimal(data.get('tax_value', settings_obj.tax_value), settings_obj.tax_value)
    settings_obj.tax_number = str(data.get('tax_number', settings_obj.tax_number)).strip()
    settings_obj.items_per_page = max(5, min(200, _as_int(data.get('items_per_page', settings_obj.items_per_page), settings_obj.items_per_page)))
    settings_obj.date_format = str(data.get('date_format', settings_obj.date_format)).strip() or settings_obj.date_format
    settings_obj.print_sale_invoice = _as_bool(data.get('print_sale_invoice', settings_obj.print_sale_invoice))
    settings_obj.print_purchase_invoice = _as_bool(data.get('print_purchase_invoice', settings_obj.print_purchase_invoice))
    settings_obj.show_zero_stock = _as_bool(data.get('show_zero_stock', settings_obj.show_zero_stock))
    settings_obj.low_stock_alert = _as_bool(data.get('low_stock_alert', settings_obj.low_stock_alert))
    settings_obj.save()

    return JsonResponse({'success': True, 'message': 'تم تحديث إعدادات النظام بنجاح'})


@login_required
def subscription_info(request):
    """معلومات الاشتراك"""
    tenant = request.tenant
    days_remaining = tenant.days_until_expiry() if tenant else None
    is_valid = tenant.is_subscription_valid() if tenant else False

    if days_remaining is None:
        status_label = 'مفتوح المدة'
        status_tone = 'info'
    elif is_valid and days_remaining > 30:
        status_label = 'ساري'
        status_tone = 'success'
    elif is_valid and days_remaining >= 0:
        status_label = 'قريب الانتهاء'
        status_tone = 'warning'
    else:
        status_label = 'منتهي / غير صالح'
        status_tone = 'danger'
    
    context = {
        'tenant': tenant,
        'days_remaining': days_remaining,
        'subscription_is_valid': is_valid,
        'subscription_status_label': status_label,
        'subscription_status_tone': status_tone,
    }
    
    return render(request, 'core/subscription.html', context)
