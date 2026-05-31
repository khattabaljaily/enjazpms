"""
Core Views - Dashboard وصفحات النظام الأساسية
"""
import json
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404
from apps.accounts.decorators import require_permission
from django.db.models import Sum, Count, Q, F, Case, When, Value, CharField, DecimalField
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta

from .models import Settings, Tenant, BusinessType
from .forms import TenantForm
from .constants import COUNTRY_CHOICES, COUNTRY_TIMEZONE_MAP, DEFAULT_COUNTRY, get_timezone_for_country
from apps.treasury.models import TreasuryMovement
from apps.expenses.models import Expense


def about(request):
    """صفحة عن النظام - About page, accessible بدون تسجيل دخول"""
    return render(request, 'core/about.html')


@login_required
def dashboard(request):
    """الصفحة الرئيسية - Dashboard"""
    
    if request.user.is_superuser:
        return redirect('core:admin_dashboard')

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
    
    if tenant:
        # Users
        from apps.accounts.models import User
        stats['total_users'] = User.objects.filter(tenant=tenant).count()
        
        # Customers
        from apps.customers.models import Customer
        stats['total_customers'] = Customer.objects.filter(tenant=tenant).count()
        
        # Products
        from apps.items.models import Item
        stats['total_products'] = Item.objects.filter(tenant=tenant).count()
        
        # Suppliers
        from apps.suppliers.models import Supplier
        stats['total_suppliers'] = Supplier.objects.filter(tenant=tenant).count()
        
        # Low stock items
        from apps.stocks.models import StockQuantity
        low_stock_count = StockQuantity.objects.filter(
            tenant=tenant,
            quantity__lte=F('item__min_quantity'),
            item__min_quantity__gt=0
        ).values('item').distinct().count()
        stats['low_stock_items'] = low_stock_count
        
        # Sales today
        from apps.sales.models import SaleInvoice
        today = datetime.today().date()
        today_sales = SaleInvoice.objects.filter(
            tenant=tenant,
            invoice_date=today,
            status='confirmed'
        ).aggregate(total=Sum('grand_total'))['total'] or 0
        stats['today_sales'] = float(today_sales)
        
        # Sales this month
        first_day = today.replace(day=1)
        month_sales = SaleInvoice.objects.filter(
            tenant=tenant,
            invoice_date__gte=first_day,
            status='confirmed'
        ).aggregate(total=Sum('grand_total'))['total'] or 0
        stats['this_month_sales'] = float(month_sales)
        
        # Additional stats
        # Number of invoices today
        stats['today_invoices'] = SaleInvoice.objects.filter(
            tenant=tenant,
            invoice_date=today,
            status='confirmed'
        ).count()
        
        # Number of pending invoices (credit)
        stats['pending_invoices'] = SaleInvoice.objects.filter(
            tenant=tenant,
            status='confirmed',
            payment_method='credit'
        ).exclude(paid_amount__gte=F('grand_total')).count()
        
        # Payment percentage
        total_invoices = SaleInvoice.objects.filter(
            tenant=tenant,
            status='confirmed'
        ).count()
        paid_invoices = SaleInvoice.objects.filter(
            tenant=tenant,
            status='confirmed',
            paid_amount__gte=F('grand_total')
        ).count()
        stats['payment_percentage'] = int((paid_invoices / total_invoices) * 100) if total_invoices > 0 else 0
        
        # Top categories by sales (products only, not services)
        from apps.items.models import Category
        from apps.sales.models import SaleInvoiceLine
        
        # Get ALL categories (not just top 4) to calculate total for percentage
        all_categories = SaleInvoiceLine.objects.filter(
            tenant=tenant,
            invoice__status='confirmed',
            invoice__invoice_date__gte=first_day,
            item__item_type='product'  # Only products, not services
        ).annotate(
            category_name=Case(
                When(item__category__name__isnull=True, then=Value('غير مصنف')),
                default='item__category__name',
                output_field=CharField()
            )
        ).values('category_name').annotate(
            total_sales=Sum('line_total')
        ).order_by('-total_sales')
        
        # Calculate total from ALL categories
        total_sales_all = sum(cat['total_sales'] for cat in all_categories)
        
        # Get top 4 only for display
        top_categories = list(all_categories)[:4]
        
        # Calculate percentages based on total from ALL categories
        for cat in top_categories:
            cat['percentage'] = round((cat['total_sales'] / total_sales_all) * 100) if total_sales_all > 0 else 0
        
        # Weekly sales data for chart
        from datetime import timedelta
        week_ago = today - timedelta(days=6)
        weekly_sales = []
        weekly_revenues = []
        weekly_expenses = []
        for i in range(7):
            day = week_ago + timedelta(days=i)
            
            # Sales for the day
            day_sales = SaleInvoice.objects.filter(
                tenant=tenant,
                invoice_date=day,
                status='confirmed'
            ).aggregate(total=Sum('grand_total'))['total'] or 0
            
            # Customer payments (receipts) for the day
            day_receipts = TreasuryMovement.objects.filter(
                tenant=tenant,
                movement_date=day,
                movement_type='receipt'
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            # Total revenues = sales + customer payments
            day_revenues = float(day_sales) + float(day_receipts)
            
            # Expenses for the day
            day_expenses = Expense.objects.filter(
                tenant=tenant,
                expense_date=day,
                status='confirmed'
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            # Supplier payments (disbursements) for the day
            day_disbursements = TreasuryMovement.objects.filter(
                tenant=tenant,
                movement_date=day,
                movement_type='disbursement'
            ).aggregate(total=Sum('amount'))['total'] or 0
            
            # Total expenses = regular expenses + supplier payments
            day_expenses_total = float(day_expenses) + float(day_disbursements)
            
            weekly_sales.append(float(day_sales))
            weekly_revenues.append(day_revenues)
            weekly_expenses.append(day_expenses_total)
        
        stats['weekly_sales'] = weekly_sales
        stats['weekly_revenues'] = weekly_revenues
        stats['weekly_expenses'] = weekly_expenses
        # Arabic day names
        arabic_days = ['الأحد', 'الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت']
        stats['weekly_labels'] = [arabic_days[(week_ago + timedelta(days=i)).weekday()] for i in range(7)]
        
        # JSON for charts
        stats['weekly_sales_json'] = json.dumps(weekly_sales)
        stats['weekly_revenues_json'] = json.dumps(weekly_revenues)
        stats['weekly_expenses_json'] = json.dumps(weekly_expenses)
        stats['weekly_labels_json'] = json.dumps(stats['weekly_labels'])
        
        # Convert Decimal to float for JSON serialization
        top_categories_list = []
        for cat in top_categories:
            top_categories_list.append({
                'item__category__name': cat['category_name'],
                'total_sales': float(cat['total_sales']),
                'percentage': cat['percentage']
            })
        stats['top_categories_json'] = json.dumps(top_categories_list)
        stats['top_categories'] = top_categories_list
        
        # Top selling products
        top_products = SaleInvoiceLine.objects.filter(
            tenant=tenant,
            invoice__status='confirmed',
            invoice__invoice_date__gte=first_day
        ).values('item__name').annotate(
            total_qty=Sum('quantity'),
            total_revenue=Sum('line_total')
        ).order_by('-total_revenue')[:5]
        
        top_products_list = []
        for prod in top_products:
            top_products_list.append({
                'item__name': prod['item__name'],
                'total_qty': float(prod['total_qty']),
                'total_revenue': float(prod['total_revenue'])
            })
        stats['top_products_json'] = json.dumps(top_products_list)
        
        # Low stock items (available but low quantity)
        from apps.stocks.models import StockQuantity
        low_stock_items = StockQuantity.objects.filter(
            tenant=tenant,
            quantity__gt=0,  # Available items only
            item__item_type='product'  # Only products, not services
        ).annotate(
            effective_min_quantity=Case(
                When(min_quantity__gt=0, then='min_quantity'),
                default='item__min_quantity',
                output_field=DecimalField()
            )
        ).filter(
            quantity__lte=F('effective_min_quantity'),
            effective_min_quantity__gt=0  # Only items with defined min quantity
        ).select_related('item').order_by('quantity')[:5]  # Lowest quantity first
        
        low_stock_list = []
        for stock_item in low_stock_items:
            low_stock_list.append({
                'item__name': stock_item.item.name,
                'quantity': float(stock_item.quantity),
                'min_quantity': float(stock_item.effective_min_quantity)
            })
        stats['low_stock_items_json'] = json.dumps(low_stock_list)
        
        # Stock status summary for pie chart
        # Get all available products (quantity > 0 and item_type='product')
        available_items = StockQuantity.objects.filter(
            tenant=tenant,
            quantity__gt=0,
            item__item_type='product'  # Only products, not services
        ).annotate(
            effective_min_quantity=Case(
                When(min_quantity__gt=0, then='min_quantity'),
                default='item__min_quantity',
                output_field=DecimalField()
            )
        )
        
        # Available items: those with no min_quantity threshold or quantity > threshold
        available_count = available_items.filter(
            Q(effective_min_quantity=0) | Q(quantity__gt=F('effective_min_quantity'))
        ).count()
        
        # Low stock items: those with quantity <= threshold and threshold > 0
        low_stock_count = available_items.filter(
            quantity__lte=F('effective_min_quantity'),
            effective_min_quantity__gt=0
        ).count()
        
        stats['stock_status_data'] = {
            'available': available_count,
            'low_stock': low_stock_count
        }
        stats['stock_status_json'] = json.dumps([available_count, low_stock_count])
    
    context = {
        'stats': stats,
        'tenant': tenant,
    }
    
    return render(request, 'core/dashboard.html', context)


@login_required
def admin_dashboard(request):
    """لوحة مشرف النظام"""
    if not request.user.is_superuser:
        return render(request, 'core/no_permission.html', status=403)

    today = datetime.today().date()
    total_clients = Tenant.objects.count()
    active_clients = Tenant.objects.filter(is_active=True).count()
    expired_clients = Tenant.objects.filter(is_active=True, subscription_expires__lt=today).count()
    trial_clients = Tenant.objects.filter(subscription_plan='trial').count()
    basic_clients = Tenant.objects.filter(subscription_plan='basic').count()
    pro_clients = Tenant.objects.filter(subscription_plan='pro').count()
    enterprise_clients = Tenant.objects.filter(subscription_plan='enterprise').count()
    single_store_clients = Tenant.objects.filter(version_type='single_store').count()
    multi_stock_clients = Tenant.objects.filter(version_type='multi_stock').count()
    multi_branch_clients = Tenant.objects.filter(version_type='multi_branch').count()

    recent_tenants = Tenant.objects.order_by('-created_at')[:5]
    recent_tenants_data = [
        {
            'name': tenant.name,
            'plan': tenant.get_subscription_plan_display(),
            'version': tenant.get_version_type_display(),
            'status': 'نشط' if tenant.is_active else 'معلق',
            'expires': tenant.subscription_expires.strftime('%Y-%m-%d') if tenant.subscription_expires else 'مدى الحياة',
        }
        for tenant in recent_tenants
    ]

    stats = {
        'total_clients': total_clients,
        'active_clients': active_clients,
        'expired_clients': expired_clients,
        'trial_clients': trial_clients,
        'basic_clients': basic_clients,
        'pro_clients': pro_clients,
        'enterprise_clients': enterprise_clients,
        'single_store_clients': single_store_clients,
        'multi_stock_clients': multi_stock_clients,
        'multi_branch_clients': multi_branch_clients,
        'pending_support': 12,
        'backup_ready': max(active_clients, 0),
        'monthly_revenue': 0,
        'plan_distribution_json': json.dumps([
            {'name': 'تجريبي', 'value': trial_clients},
            {'name': 'أساسي', 'value': basic_clients},
            {'name': 'احترافي', 'value': pro_clients},
            {'name': 'مؤسسات', 'value': enterprise_clients},
        ], ensure_ascii=False),
        'version_distribution_json': json.dumps([
            {'name': 'محل واحد بمخزن واحد', 'value': single_store_clients},
            {'name': 'محل بمخازن متعددة', 'value': multi_stock_clients},
            {'name': 'فروع ومخازن متعددة', 'value': multi_branch_clients},
        ], ensure_ascii=False),
        'recent_tenants': recent_tenants_data,
    }

    return render(request, 'core/admin_dashboard.html', {
        'stats': stats,
    })


@login_required
def admin_users(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    from apps.accounts.models import User
    users = User.objects.select_related('tenant').order_by('-date_joined')
    return render(request, 'core/admin_users.html', {'users': users})


@login_required
def admin_user_create(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_user_create.html', {})


@login_required
def admin_support(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_support.html', {})


@login_required
def admin_report_subscriptions(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_report_subscriptions.html', {})


@login_required
def admin_report_revenue(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_report_revenue.html', {})


@login_required
def admin_report_activity(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_report_activity.html', {})


@login_required
def admin_audit_log(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_audit_log.html', {})


@login_required
def admin_settings(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_settings.html', {})


@login_required
def admin_backup(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_backup.html', {})


@login_required
def admin_training(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    return render(request, 'core/admin_training.html', {})


def subscription_expired(request):
    """صفحة انتهاء الاشتراك"""
    return render(request, 'core/subscription_expired.html')


def no_tenant(request):
    """صفحة عدم وجود tenant"""
    return render(request, 'core/no_tenant.html')


def no_permission(request):
    """صفحة رفض الصلاحية"""
    return render(request, 'core/no_permission.html', status=403)


@login_required
@require_permission('view_tenant_settings')
def tenant_settings(request):
    """إعدادات النشاط التجاري"""
    return render(request, 'core/tenant_settings.html', {
        'country_choices': COUNTRY_CHOICES,
        'country_timezone_map_json': json.dumps(COUNTRY_TIMEZONE_MAP, ensure_ascii=False),
        'default_country': DEFAULT_COUNTRY,
    })


@login_required
@require_permission('change_tenant_settings')
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
        timezone_value = str(data.get('timezone', tenant.timezone)).strip()
        if not timezone_value:
            timezone_value = get_timezone_for_country(tenant.country)
        tenant.timezone = timezone_value
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
@require_permission('view_tenant_settings')
def subscription_info(request):
    """معلومات الاشتراك"""
    from .models import Tenant
    tenant = Tenant.objects.select_related('business_type').get(pk=request.tenant.pk) if request.tenant else None
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

    from apps.accounts.models import User as TenantUser
    from apps.stocks.models import Stock
    current_users  = TenantUser.objects.filter(tenant=tenant).count() if tenant else 0
    current_stocks = Stock.objects.filter(tenant=tenant, is_active=True).count() if tenant else 0

    def pct(used, limit):
        if not limit:
            return 0
        return min(round(used / limit * 100), 100)

    context = {
        'tenant': tenant,
        'current_tenant': tenant,
        'days_remaining': days_remaining,
        'subscription_is_valid': is_valid,
        'subscription_status_label': status_label,
        'subscription_status_tone': status_tone,
        'current_users': current_users,
        'current_stocks': current_stocks,
        'users_pct': pct(current_users, tenant.max_users if tenant else 1),
        'stocks_pct': pct(current_stocks, tenant.max_stocks if tenant else 1),
    }

    return render(request, 'core/subscription.html', context)


# ============================================================
# TENANT MANAGEMENT — Superuser Only
# ============================================================

def _superuser_required(request):
    """Return 403 JsonResponse if not superuser, else None."""
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': 'غير مصرح'}, status=403)
    return None


@login_required
def tenant_list(request):
    """قائمة العملاء (المستأجرين) - للمشرف فقط"""
    if not request.user.is_superuser:
        return render(request, 'core/no_permission.html', status=403)
    business_types = BusinessType.objects.filter(is_active=True).order_by('display_order', 'name_ar')
    total = Tenant.objects.count()
    active = Tenant.objects.filter(is_active=True).count()
    suspended = total - active
    today = datetime.today().date()
    expired = Tenant.objects.filter(is_active=True, subscription_expires__lt=today).count()
    context = {
        'form': TenantForm(),
        'business_types': business_types,
        'stats': {'total': total, 'active': active, 'suspended': suspended, 'expired': expired},
    }
    return render(request, 'core/tenant_list.html', context)


@login_required
def tenant_table_api(request):
    """API: جدول العملاء لـ DataTable"""
    err = _superuser_required(request)
    if err:
        return err

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '').strip()
    plan_filter = request.GET.get('plan', '').strip()

    qs = Tenant.objects.select_related('business_type').all()
    records_total = qs.count()

    today = datetime.today().date()

    if status_filter == 'active':
        qs = qs.filter(is_active=True)
    elif status_filter == 'suspended':
        qs = qs.filter(is_active=False)
    elif status_filter == 'expired':
        qs = qs.filter(is_active=True, subscription_expires__lt=today)

    if plan_filter:
        qs = qs.filter(subscription_plan=plan_filter)

    if search_value:
        qs = qs.filter(
            Q(name__icontains=search_value)
            | Q(email__icontains=search_value)
            | Q(phone__icontains=search_value)
            | Q(city__icontains=search_value)
            | Q(slug__icontains=search_value)
        )

    records_filtered = qs.count()

    order_col_idx = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'desc')
    col_map = {
        '0': 'name', '1': 'business_type__name_ar', '2': 'subscription_plan',
        '3': 'version_type', '4': 'subscription_expires', '5': 'is_active',
    }
    order_field = col_map.get(order_col_idx, 'created_at')
    if order_dir == 'desc':
        order_field = f'-{order_field}'
    qs = qs.order_by(order_field)[start:start + length]

    data = []
    for t in qs:
        days = t.days_until_expiry()
        if not t.subscription_expires:
            exp_label = 'مفتوحة'
            exp_status = 'lifetime'
        elif days is not None and days < 0:
            exp_label = f'منتهية منذ {abs(days)} يوم'
            exp_status = 'expired'
        elif days is not None and days <= 30:
            exp_label = f'تنتهي خلال {days} يوم'
            exp_status = 'soon'
        else:
            exp_label = t.subscription_expires.strftime('%Y-%m-%d') if t.subscription_expires else '—'
            exp_status = 'ok'

        data.append({
            'id': t.id,
            'name': t.name,
            'slug': t.slug,
            'business_type': t.business_type.name_ar if t.business_type else '—',
            'subscription_plan': t.get_subscription_plan_display(),
            'subscription_plan_key': t.subscription_plan,
            'version_type': t.get_version_type_display(),
            'version_type_key': t.version_type,
            'is_active': t.is_active,
            'is_demo': t.is_demo,
            'subscription_expires': t.subscription_expires.strftime('%Y-%m-%d') if t.subscription_expires else None,
            'exp_label': exp_label,
            'exp_status': exp_status,
            'email': t.email or '—',
            'phone': t.phone or '—',
            'city': t.city or '—',
        })

    return JsonResponse({'draw': draw, 'recordsTotal': records_total, 'recordsFiltered': records_filtered, 'data': data})


@login_required
def tenant_create_api(request):
    """API: إنشاء عميل جديد مع مستخدم مدير"""
    err = _superuser_required(request)
    if err:
        return err
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = TenantForm(request.POST)

    # Validate admin user fields
    from apps.accounts.models import User, PermissionGroup
    username   = request.POST.get('admin_username', '').strip()
    password   = request.POST.get('admin_password', '').strip()
    password2  = request.POST.get('admin_password2', '').strip()
    email      = request.POST.get('admin_email', '').strip()
    full_name  = request.POST.get('admin_full_name', '').strip()

    user_errors = {}
    if not username:
        user_errors['admin_username'] = ['اسم المستخدم مطلوب']
    elif User.objects.filter(username=username).exists():
        user_errors['admin_username'] = ['اسم المستخدم مستخدم بالفعل']
    if not password:
        user_errors['admin_password'] = ['كلمة المرور مطلوبة']
    elif len(password) < 6:
        user_errors['admin_password'] = ['كلمة المرور يجب أن تكون 6 أحرف على الأقل']
    elif password != password2:
        user_errors['admin_password2'] = ['كلمتا المرور غير متطابقتين']
    if email and User.objects.filter(email=email).exists():
        user_errors['admin_email'] = ['البريد الإلكتروني مستخدم بالفعل']

    if not form.is_valid() or user_errors:
        errors = {f: [str(e) for e in errs] for f, errs in form.errors.items()}
        errors.update(user_errors)
        first_msg = next(iter(errors.values()), ['يرجى مراجعة الحقول'])[0]
        return JsonResponse({'success': False, 'message': first_msg, 'errors': errors}, status=400)

    from django.db import transaction
    try:
        with transaction.atomic():
            tenant = form.save()

            # Create admin user
            first, _, last = full_name.partition(' ')
            user = User.objects.create_user(
                username=username,
                email=email or '',
                password=password,
                first_name=first,
                last_name=last,
                tenant=tenant,
                is_tenant_admin=True,
            )
            owner_group = PermissionGroup.create_owner_group(tenant, name='مدير النشاط')
            owner_group.users.add(user)

            # Create Settings
            tax_enabled = request.POST.get('tax_enabled') in ('on', 'true', '1', 'True')
            try:
                tax_value = float(request.POST.get('tax_value', 0) or 0)
            except (ValueError, TypeError):
                tax_value = 0
            from .models import Settings
            Settings.objects.create(tenant=tenant, tax_enabled=tax_enabled, tax_value=tax_value)

    except Exception as e:
        import logging
        logging.getLogger(__name__).error('tenant_create_api error: %s', e, exc_info=True)
        return JsonResponse({'success': False, 'message': f'حدث خطأ: {e}'}, status=500)

    return JsonResponse({
        'success': True,
        'message': f'تم إنشاء العميل "{tenant.name}" ومدير النشاط "{username}" بنجاح',
        'id': tenant.id,
    })


@login_required
def tenant_detail_api(request, pk):
    """API: تفاصيل عميل"""
    err = _superuser_required(request)
    if err:
        return err

    tenant = get_object_or_404(Tenant.objects.select_related('business_type'), pk=pk)
    days = tenant.days_until_expiry()
    is_valid = tenant.is_subscription_valid()

    from apps.accounts.models import User
    admin_user = User.objects.filter(tenant=tenant, is_tenant_admin=True).order_by('id').first()
    user_count = User.objects.filter(tenant=tenant).count()

    settings_obj = tenant.settings if hasattr(tenant, 'settings') else None
    try:
        from .models import Settings as TenantSettings
        settings_obj = TenantSettings.objects.filter(tenant=tenant).first()
    except Exception:
        settings_obj = None

    return JsonResponse({
        'success': True,
        'data': {
            'id': tenant.id,
            'name': tenant.name,
            'slug': tenant.slug,
            'business_type_id': tenant.business_type_id,
            'business_type': tenant.business_type.name_ar if tenant.business_type else '—',
            'email': tenant.email or '',
            'phone': tenant.phone or '',
            'city': tenant.city or '',
            'country': tenant.country or '',
            'address': tenant.address or '',
            'subscription_plan': tenant.subscription_plan,
            'subscription_plan_display': tenant.get_subscription_plan_display(),
            'subscription_start': tenant.subscription_start.strftime('%Y-%m-%d') if tenant.subscription_start else '',
            'subscription_expires': tenant.subscription_expires.strftime('%Y-%m-%d') if tenant.subscription_expires else '',
            'version_type': tenant.version_type,
            'version_type_display': tenant.get_version_type_display(),
            'max_users': tenant.max_users,
            'max_stocks': tenant.max_stocks,
            'max_branches': tenant.max_branches,
            'timezone': tenant.timezone or '',
            'currency': tenant.currency or '',
            'is_active': tenant.is_active,
            'is_demo': tenant.is_demo,
            'days_until_expiry': days,
            'is_subscription_valid': is_valid,
            'user_count': user_count,
            'created_at': tenant.created_at.strftime('%Y-%m-%d'),
            'admin_username': admin_user.username if admin_user else '',
            'admin_email': admin_user.email if admin_user else '',
            'admin_full_name': admin_user.get_full_name() if admin_user else '',
            'tax_enabled': settings_obj.tax_enabled if settings_obj else False,
            'tax_value': float(settings_obj.tax_value) if settings_obj else 0,
        }
    })


@login_required
def tenant_update_api(request, pk):
    """API: تعديل بيانات عميل"""
    err = _superuser_required(request)
    if err:
        return err
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    tenant = get_object_or_404(Tenant, pk=pk)
    form = TenantForm(request.POST, instance=tenant)

    from apps.accounts.models import User
    password  = request.POST.get('admin_password', '').strip()
    password2 = request.POST.get('admin_password2', '').strip()
    email     = request.POST.get('admin_email', '').strip()
    full_name = request.POST.get('admin_full_name', '').strip()

    user_errors = {}
    if password and password != password2:
        user_errors['admin_password2'] = ['كلمتا المرور غير متطابقتين']
    if password and len(password) < 6:
        user_errors['admin_password'] = ['كلمة المرور يجب أن تكون 6 أحرف على الأقل']
    admin_user = User.objects.filter(tenant=tenant, is_tenant_admin=True).order_by('id').first()
    if email and admin_user and email != admin_user.email:
        if User.objects.filter(email=email).exclude(pk=admin_user.pk).exists():
            user_errors['admin_email'] = ['البريد الإلكتروني مستخدم بالفعل']

    if not form.is_valid() or user_errors:
        errors = {f: [str(e) for e in errs] for f, errs in form.errors.items()}
        errors.update(user_errors)
        first_msg = next(iter(errors.values()), ['يرجى مراجعة الحقول'])[0]
        return JsonResponse({'success': False, 'message': first_msg, 'errors': errors}, status=400)

    from django.db import transaction
    with transaction.atomic():
        form.save()

        if admin_user:
            first, _, last = full_name.partition(' ')
            if full_name:
                admin_user.first_name = first
                admin_user.last_name  = last
            if email:
                admin_user.email = email
            if password:
                admin_user.set_password(password)
            admin_user.save()

        tax_enabled = request.POST.get('tax_enabled') in ('on', 'true', '1', 'True')
        try:
            tax_value = float(request.POST.get('tax_value', 0) or 0)
        except (ValueError, TypeError):
            tax_value = 0
        from .models import Settings as TenantSettings
        TenantSettings.objects.update_or_create(
            tenant=tenant,
            defaults={'tax_enabled': tax_enabled, 'tax_value': tax_value},
        )

    return JsonResponse({'success': True, 'message': 'تم تعديل بيانات العميل بنجاح'})


@login_required
def tenant_delete_api(request, pk):
    """API: حذف عميل"""
    err = _superuser_required(request)
    if err:
        return err
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    tenant = get_object_or_404(Tenant, pk=pk)
    name = tenant.name
    try:
        _delete_tenant_data(tenant)
        tenant.delete()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error('tenant_delete_api error pk=%s: %s', pk, e, exc_info=True)
        return JsonResponse({'success': False, 'message': f'تعذر الحذف: {e}'}, status=400)
    return JsonResponse({'success': True, 'message': f'تم حذف العميل "{name}" بنجاح'})


def _delete_tenant_data(tenant):
    """
    Delete all tenant-scoped records in an order that avoids PROTECT FK violations.
    Many models use on_delete=PROTECT to guard referential integrity within a tenant,
    but when wiping an entire tenant all those objects must go together.
    """
    from apps.core.models import tenant_deletion_in_progress
    token = tenant_deletion_in_progress.set(True)
    try:
        t = {'tenant': tenant}
        # 1. Deepest dependents first (protect SaleInvoiceLine / SaleReturn / etc.)
        from apps.sales.models import SaleReturnLine, SaleReturn, SaleInvoiceLine
        from apps.sales.models import SaleQuoteLine, SaleInvoice, SaleQuote
        from apps.sales.models import StockMovement, CustomerLedger, SalePayment
        from apps.purchases.models import (
            PurchaseReturnLine, PurchaseReturn,
            PurchaseInvoiceLine, PurchaseInvoice,
            PurchasePayment, SupplierLedger,
        )
        from apps.expenses.models import Expense
        from apps.treasury.models import TreasuryMovement
        from apps.stocks.models import (
            StocktakeLine, Stocktake,
            StockTransferLine, StockTransfer,
            ManufacturingOrder,
        )
        from apps.purchases.models import PurchaseRFQLine, PurchaseRFQ
        from apps.items.models import BOMRecipe

        # --- stock sub-documents (all PROTECT Stock or Item) ---
        StocktakeLine.objects.filter(**t).delete()
        Stocktake.objects.filter(**t).delete()
        StockTransferLine.objects.filter(**t).delete()
        StockTransfer.objects.filter(**t).delete()
        ManufacturingOrder.objects.filter(**t).delete()
        # --- purchase RFQ (PROTECT Stock / Item) ---
        PurchaseRFQLine.objects.filter(**t).delete()
        PurchaseRFQ.objects.filter(**t).delete()
        # --- BOM (BOMRecipe cascades to BOMLine which PROTECT Item) ---
        BOMRecipe.objects.filter(**t).delete()
        SaleReturnLine.objects.filter(**t).delete()
        SaleReturn.objects.filter(**t).delete()
        PurchaseReturnLine.objects.filter(**t).delete()
        PurchaseReturn.objects.filter(**t).delete()
        StockMovement.objects.filter(**t).delete()
        SaleInvoiceLine.objects.filter(**t).delete()
        PurchaseInvoiceLine.objects.filter(**t).delete()
        SaleQuoteLine.objects.filter(**t).delete()
        SalePayment.objects.filter(**t).delete()
        PurchasePayment.objects.filter(**t).delete()
        SaleInvoice.objects.filter(**t).delete()
        SaleQuote.objects.filter(**t).delete()
        PurchaseInvoice.objects.filter(**t).delete()
        CustomerLedger.objects.filter(**t).delete()
        SupplierLedger.objects.filter(**t).delete()
        Expense.objects.filter(**t).delete()
        TreasuryMovement.objects.filter(**t).delete()
        # After the above, tenant.delete() cascades safely through
        # Customer, Supplier, Item, ItemVariant, Stock, StockQuantity,
        # Treasury, ExpenseCategory, Users, Settings, etc.
    finally:
        tenant_deletion_in_progress.reset(token)


@login_required
def tenant_suspend_api(request, pk):
    """API: تعليق / إلغاء تعليق عميل"""
    err = _superuser_required(request)
    if err:
        return err
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    tenant = get_object_or_404(Tenant, pk=pk)
    tenant.is_active = not tenant.is_active
    tenant.save(update_fields=['is_active', 'updated_at'])
    action = 'تم تفعيل' if tenant.is_active else 'تم تعليق'
    return JsonResponse({'success': True, 'message': f'{action} العميل "{tenant.name}" بنجاح', 'is_active': tenant.is_active})


def pricing(request):
    """صفحة خطط التسعير"""
    plans = [
        {
            'name': 'Basic',
            'title_ar': 'أساسي',
            'description': 'محل واحد مع مخزن واحد',
            'monthly': '$49',
            'annual': '$499',
            'perpetual': '$3,999',
            'stocks': '1 مخزن',
            'users': 'حتى 5 مستخدمين',
            'tag': 'مناسب للمتاجر الصغيرة',
            'highlight': False,
        },
        {
            'name': 'Pro',
            'title_ar': 'احترافي',
            'description': 'محل واحد مع ما يصل إلى 5 مخازن',
            'monthly': '$89',
            'annual': '$899',
            'perpetual': '$6,999',
            'stocks': 'حتى 5 مخازن',
            'users': 'حتى 15 مستخدمًا',
            'tag': 'الحل الأكثر توازناً',
            'highlight': True,
        },
        {
            'name': 'Enterprise',
            'title_ar': 'مؤسسات',
            'description': 'فروع ومخازن متعددة مع تحكم كامل',
            'monthly': '$159',
            'annual': '$1,599',
            'perpetual': '$11,999',
            'stocks': 'حتى 20 مخزن',
            'users': 'حتى 40 مستخدمًا',
            'tag': 'للشركات الكبيرة والموزعين',
            'highlight': False,
        },
    ]

    benefits = [
        'تقارير مبيعات ومشتريات شاملة',
        'إدارة المخزون بدقة مع تنبيهات المخزون المنخفض',
        'تشغيل متعدد الفروع والمخازن',
        'صلاحيات مستخدمين قابلة للتخصيص',
        'دعم فني وتحديثات مستمرة',
    ]

    # Determine current tenant's plan to highlight on pricing page
    tenant = getattr(request, 'tenant', None)
    current_plan_key = getattr(tenant, 'subscription_plan', None) if tenant else None
    # Normalize and mark plans
    for p in plans:
        p_key = p['name'].lower()
        p['is_current'] = False
        if current_plan_key and current_plan_key.lower() == p_key:
            p['is_current'] = True

    return render(request, 'core/pricing.html', {
        'plans': plans,
        'benefits': benefits,
        'trial_days': 7,
        'current_subscription_plan_display': tenant.get_subscription_plan_display() if tenant else None,
    })
