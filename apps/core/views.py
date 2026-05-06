"""
Core Views - Dashboard وصفحات النظام الأساسية
"""
import json
from decimal import Decimal, InvalidOperation

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Sum, Count, Q, F, Case, When, Value, CharField, DecimalField
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta
import json

from .models import Settings
from .constants import COUNTRY_CHOICES, COUNTRY_TIMEZONE_MAP, DEFAULT_COUNTRY, get_timezone_for_country
from apps.treasury.models import TreasuryMovement
from apps.expenses.models import Expense


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
def tenant_settings(request):
    """إعدادات النشاط التجاري"""
    return render(request, 'core/tenant_settings.html', {
        'country_choices': COUNTRY_CHOICES,
        'country_timezone_map_json': json.dumps(COUNTRY_TIMEZONE_MAP, ensure_ascii=False),
        'default_country': DEFAULT_COUNTRY,
    })


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
