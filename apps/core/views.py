"""
Core Views - Dashboard وصفحات النظام الأساسية
"""
import json
import os
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseNotAllowed, HttpResponse, FileResponse
from django.shortcuts import get_object_or_404
from django.core.paginator import Paginator
from apps.accounts.decorators import require_permission
from apps.accounts.activity_service import log_activity
from django.db.models import Sum, Count, Q, F, Case, When, Value, CharField, DecimalField
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta, date as date_type
from django.utils import timezone as dj_timezone

from .models import Settings, Tenant, BusinessType, SupportTicket, SupportMessage, TenantBackup, SocialMediaPost
from apps.notifications.models import Notification
from .forms import TenantForm
from .constants import COUNTRY_CHOICES, COUNTRY_TIMEZONE_MAP, COUNTRY_CURRENCY_MAP, TIMEZONE_CURRENCY_MAP, CURRENCY_AR, DEFAULT_COUNTRY, get_timezone_for_country, CURRENCY_CHOICES
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
        today = dj_timezone.localdate()
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
    
    store = None
    if tenant:
        from apps.store.models import StoreSettings
        store = StoreSettings.objects.filter(tenant=tenant).first()

    context = {
        'stats': stats,
        'tenant': tenant,
        'store': store,
    }

    return render(request, 'core/dashboard.html', context)


@login_required
def admin_dashboard(request):
    """لوحة مشرف النظام"""
    if not (request.user.is_superuser or getattr(request.user, 'is_platform_staff', False)):
        return render(request, 'core/no_permission.html', status=403)

    today = dj_timezone.localdate()
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

    # Health: expiring soon
    expiring_7  = Tenant.objects.filter(is_active=True, subscription_expires__isnull=False,
                                         subscription_expires__gte=today,
                                         subscription_expires__lte=today + timedelta(days=7))
    expiring_30 = Tenant.objects.filter(is_active=True, subscription_expires__isnull=False,
                                         subscription_expires__gte=today,
                                         subscription_expires__lte=today + timedelta(days=30))

    # Old open tickets (> 3 days)
    old_tickets = SupportTicket.objects.filter(
        status__in=['open', 'in_progress'],
        created_at__date__lte=today - timedelta(days=3)
    ).select_related('tenant').order_by('created_at')[:8]

    PLAN_PRICES = {'trial': 0, 'basic': 50, 'pro': 120, 'enterprise': 300}
    mrr = sum(
        PLAN_PRICES.get(p, 0) * Tenant.objects.filter(is_active=True, subscription_plan=p).count()
        for p in PLAN_PRICES
    )

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
        'pending_support': SupportTicket.objects.filter(status__in=['open', 'in_progress']).count(),
        'backup_ready': max(active_clients, 0),
        'monthly_revenue': mrr,
        'expiring_7_count': expiring_7.count(),
        'expiring_30_count': expiring_30.count(),
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
        'expiring_7': expiring_7,
        'expiring_30': expiring_30,
        'old_tickets': old_tickets,
    })


@login_required
def admin_users(request):
    if not request.user.has_platform_perm('manage_users'):
        return redirect('core:no_permission')

    from apps.accounts.models import User

    tab = request.GET.get('tab', 'tenant')  # 'tenant' | 'staff'

    # Tenant users
    tenant_qs = User.objects.filter(
        is_superuser=False, is_platform_staff=False
    ).select_related('tenant').order_by('-date_joined')

    search = request.GET.get('q', '').strip()
    tenant_filter = request.GET.get('tenant', '')
    status_filter = request.GET.get('status', '')

    if search:
        tenant_qs = tenant_qs.filter(
            Q(username__icontains=search) | Q(first_name__icontains=search) |
            Q(last_name__icontains=search) | Q(email__icontains=search)
        )
    if tenant_filter:
        tenant_qs = tenant_qs.filter(tenant_id=tenant_filter)
    if status_filter == 'active':
        tenant_qs = tenant_qs.filter(is_active=True)
    elif status_filter == 'inactive':
        tenant_qs = tenant_qs.filter(is_active=False)
    elif status_filter == 'never_logged':
        tenant_qs = tenant_qs.filter(last_login__isnull=True)
    elif status_filter == 'admin':
        tenant_qs = tenant_qs.filter(is_tenant_admin=True)

    # Platform staff
    staff_qs = User.objects.filter(
        Q(is_superuser=True) | Q(is_platform_staff=True)
    ).order_by('-date_joined')

    # Stats
    stats = {
        'total_tenant_users': User.objects.filter(is_superuser=False, is_platform_staff=False).count(),
        'active': User.objects.filter(is_superuser=False, is_platform_staff=False, is_active=True).count(),
        'inactive': User.objects.filter(is_superuser=False, is_platform_staff=False, is_active=False).count(),
        'never_logged': User.objects.filter(is_superuser=False, is_platform_staff=False, last_login__isnull=True).count(),
        'tenant_admins': User.objects.filter(is_superuser=False, is_platform_staff=False, is_tenant_admin=True).count(),
        'platform_staff': User.objects.filter(Q(is_superuser=True) | Q(is_platform_staff=True)).count(),
    }

    tenants = Tenant.objects.filter(is_active=True).order_by('name')

    return render(request, 'core/admin_users.html', {
        'tab': tab,
        'tenant_users': tenant_qs,
        'staff_users': staff_qs,
        'stats': stats,
        'tenants': tenants,
        'search': search,
        'tenant_filter': tenant_filter,
        'status_filter': status_filter,
        'PLATFORM_PERMS': PLATFORM_PERMS,
    })


# ── Platform permission definitions ──────────────────────────────────────
PLATFORM_PERMS = [
    ('manage_tenants',    'إدارة المشتركين',         'fa-building'),
    ('manage_users',      'إدارة المستخدمين',         'fa-users'),
    ('view_reports',      'عرض التقارير',             'fa-chart-bar'),
    ('manage_support',    'إدارة الدعم الفني',        'fa-headset'),
    ('view_audit_log',    'سجل المراجعة',             'fa-clock-rotate-left'),
    ('manage_backups',    'النسخ الاحتياطية',          'fa-database'),
    ('view_notifications','إشعارات النظام',            'fa-bell'),
    ('manage_marketing',  'إدارة التسويق',              'fa-bullhorn'),
]


@login_required
def admin_user_toggle_active(request, pk):
    """Toggle tenant user active status."""
    if not request.user.has_platform_perm('manage_users'):
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    from apps.accounts.models import User
    user = get_object_or_404(User, pk=pk, is_superuser=False, is_platform_staff=False)
    user.is_active = not user.is_active
    user.save(update_fields=['is_active'])
    return JsonResponse({'success': True, 'is_active': user.is_active})


@login_required
def admin_user_detail_api(request, pk):
    """Return user detail JSON for the view modal."""
    if not request.user.has_platform_perm('manage_users'):
        return JsonResponse({'success': False}, status=403)
    from apps.accounts.models import User
    u = get_object_or_404(User, pk=pk)
    return JsonResponse({
        'success': True,
        'id': u.pk,
        'username': u.username,
        'full_name': u.get_full_name(),
        'email': u.email,
        'phone': u.phone,
        'tenant': u.tenant.name if u.tenant else '—',
        'is_active': u.is_active,
        'is_tenant_admin': u.is_tenant_admin,
        'date_joined': u.date_joined.strftime('%Y-%m-%d'),
        'last_login': u.last_login.strftime('%Y-%m-%d %H:%M') if u.last_login else 'لم يسجّل دخولاً',
    })


@login_required
def admin_user_create(request):
    """Create a new tenant user."""
    if not request.user.has_platform_perm('manage_users'):
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    from apps.accounts.models import User
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'بيانات غير صالحة'}, status=400)

    username  = data.get('username', '').strip()
    password  = data.get('password', '').strip()
    email     = data.get('email', '').strip()
    first     = data.get('first_name', '').strip()
    last      = data.get('last_name', '').strip()
    tenant_id = data.get('tenant_id')
    is_admin  = bool(data.get('is_tenant_admin', False))

    if not username or not password:
        return JsonResponse({'success': False, 'error': 'اسم المستخدم وكلمة المرور مطلوبان'})
    if User.objects.filter(username=username).exists():
        return JsonResponse({'success': False, 'error': 'اسم المستخدم مستخدم بالفعل'})

    tenant = None
    if tenant_id:
        tenant = get_object_or_404(Tenant, pk=tenant_id)

    user = User.objects.create_user(
        username=username, password=password,
        email=email, first_name=first, last_name=last,
        tenant=tenant, is_tenant_admin=is_admin,
    )
    return JsonResponse({'success': True, 'message': f'تم إنشاء المستخدم "{user.username}" بنجاح'})


@login_required
def admin_staff_save(request, pk=None):
    """Create or update a platform staff member (superuser only)."""
    if not request.user.is_superuser:
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    from apps.accounts.models import User
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'بيانات غير صالحة'}, status=400)

    perms = [p for p in data.get('permissions', []) if p in [k for k, *_ in PLATFORM_PERMS]]

    if pk:
        # Update existing staff member
        staff = get_object_or_404(User, pk=pk, is_platform_staff=True)
        staff.first_name = data.get('first_name', staff.first_name).strip()
        staff.last_name  = data.get('last_name',  staff.last_name).strip()
        staff.email      = data.get('email', staff.email).strip()
        staff.platform_permissions = perms
        staff.save(update_fields=['first_name', 'last_name', 'email', 'platform_permissions'])
        return JsonResponse({'success': True, 'message': f'تم تحديث "{staff.username}"'})
    else:
        # Create new staff member
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        if not username or not password:
            return JsonResponse({'success': False, 'error': 'اسم المستخدم وكلمة المرور مطلوبان'})
        if User.objects.filter(username=username).exists():
            return JsonResponse({'success': False, 'error': 'اسم المستخدم مستخدم بالفعل'})
        staff = User.objects.create_user(
            username=username, password=password,
            first_name=data.get('first_name', '').strip(),
            last_name=data.get('last_name', '').strip(),
            email=data.get('email', '').strip(),
            tenant=None,
            is_platform_staff=True,
            platform_permissions=perms,
        )
        return JsonResponse({'success': True, 'message': f'تم إنشاء مساعد المنصة "{staff.username}"'})


@login_required
def admin_support(request):
    if not request.user.has_platform_perm('manage_support'):
        return redirect('core:no_permission')

    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    search_q = request.GET.get('q', '').strip()

    tickets = SupportTicket.objects.select_related('tenant', 'created_by').order_by('-created_at')

    if status_filter:
        tickets = tickets.filter(status=status_filter)
    if priority_filter:
        tickets = tickets.filter(priority=priority_filter)
    if search_q:
        tickets = tickets.filter(
            Q(subject__icontains=search_q) | Q(tenant__name__icontains=search_q)
        )

    counts = {
        'all': SupportTicket.objects.count(),
        'open': SupportTicket.objects.filter(status='open').count(),
        'in_progress': SupportTicket.objects.filter(status='in_progress').count(),
        'resolved': SupportTicket.objects.filter(status='resolved').count(),
        'closed': SupportTicket.objects.filter(status='closed').count(),
    }

    return render(request, 'core/admin_support.html', {
        'tickets': tickets,
        'counts': counts,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'search_q': search_q,
    })


@login_required
def admin_support_detail(request, pk):
    if not request.user.has_platform_perm('manage_support'):
        return redirect('core:no_permission')

    ticket = get_object_or_404(SupportTicket.objects.select_related('tenant', 'created_by'), pk=pk)
    ticket_messages = ticket.messages.select_related('sender').order_by('created_at')

    if request.method == 'POST':
        body = request.POST.get('body', '').strip()
        new_status = request.POST.get('status', ticket.status)
        if body:
            SupportMessage.objects.create(
                ticket=ticket,
                sender=request.user,
                sender_type='admin',
                body=body,
            )
            from django.utils import timezone
            ticket.last_reply_at = timezone.now()
            Notification.objects.create(
                tenant=ticket.tenant,
                notification_type='general',
                priority='high',
                title=f'رد جديد على تذكرتك #{ticket.pk}',
                message=(
                    f'فريق الدعم الفني رد على التذكرة "{ticket.subject}". '
                    'افتح التذكرة للاطلاع على الرد.'
                ),
                link=f'/support/{ticket.pk}/',
            )
        ticket.status = new_status
        if new_status == 'closed':
            from django.utils import timezone
            ticket.closed_at = timezone.now()
        ticket.assigned_to = request.user
        ticket.save()
        return redirect('core:admin_support_detail', pk=pk)

    return render(request, 'core/admin_support_detail.html', {
        'ticket': ticket,
        'ticket_messages': ticket_messages,
    })


@login_required
def tenant_support(request):
    """قائمة تذاكر الدعم الخاصة بالمشترك"""
    if not request.tenant:
        return redirect('core:no_tenant')

    status_filter = request.GET.get('status', '')
    tickets = SupportTicket.objects.filter(tenant=request.tenant).select_related('created_by').order_by('-created_at')
    if status_filter:
        tickets = tickets.filter(status=status_filter)

    counts = {
        'all': SupportTicket.objects.filter(tenant=request.tenant).count(),
        'open': SupportTicket.objects.filter(tenant=request.tenant, status='open').count(),
        'in_progress': SupportTicket.objects.filter(tenant=request.tenant, status='in_progress').count(),
        'closed': SupportTicket.objects.filter(tenant=request.tenant, status__in=['resolved', 'closed']).count(),
    }

    return render(request, 'core/tenant_support.html', {
        'tickets': tickets,
        'counts': counts,
        'status_filter': status_filter,
    })


@login_required
def tenant_support_create(request):
    """إنشاء تذكرة دعم جديدة — يدعم AJAX وإعادة التوجيه العادية"""
    if not request.tenant:
        return JsonResponse({'ok': False, 'error': 'no_tenant'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    subject = request.POST.get('subject', '').strip()
    description = request.POST.get('description', '').strip()
    category = request.POST.get('category', 'other')
    priority = request.POST.get('priority', 'medium')

    errors = {}
    if not subject:
        errors['subject'] = 'الموضوع مطلوب'
    if not description:
        errors['description'] = 'الوصف مطلوب'

    if errors:
        return JsonResponse({'ok': False, 'errors': errors}, status=400)

    ticket = SupportTicket.objects.create(
        tenant=request.tenant,
        created_by=request.user,
        subject=subject,
        description=description,
        category=category,
        priority=priority,
        status='open',
    )
    SupportMessage.objects.create(
        ticket=ticket,
        sender=request.user,
        sender_type='tenant',
        body=description,
    )

    from django.urls import reverse
    return JsonResponse({'ok': True, 'redirect': reverse('core:tenant_support_detail', args=[ticket.pk])})


@login_required
def tenant_support_detail(request, pk):
    """تفاصيل تذكرة الدعم للمشترك"""
    if not request.tenant:
        return redirect('core:no_tenant')

    ticket = get_object_or_404(
        SupportTicket.objects.select_related('tenant', 'created_by'),
        pk=pk, tenant=request.tenant
    )
    ticket_messages = ticket.messages.select_related('sender').order_by('created_at')

    if request.method == 'POST' and ticket.is_open():
        body = request.POST.get('body', '').strip()
        if body:
            SupportMessage.objects.create(
                ticket=ticket,
                sender=request.user,
                sender_type='tenant',
                body=body,
            )
            from django.utils import timezone
            ticket.last_reply_at = timezone.now()
            ticket.save(update_fields=['last_reply_at', 'updated_at'])
        return redirect('core:tenant_support_detail', pk=pk)

    return render(request, 'core/tenant_support_detail.html', {
        'ticket': ticket,
        'ticket_messages': ticket_messages,
    })


@login_required
def admin_report_subscriptions(request):
    if not request.user.has_platform_perm('view_reports'):
        return redirect('core:no_permission')

    today = dj_timezone.localdate()

    tenants = Tenant.objects.select_related('business_type').order_by('-created_at')

    # Expiry buckets
    expiring_7  = tenants.filter(is_active=True, subscription_expires__isnull=False,
                                  subscription_expires__gte=today,
                                  subscription_expires__lte=today + timedelta(days=7)).count()
    expiring_30 = tenants.filter(is_active=True, subscription_expires__isnull=False,
                                  subscription_expires__gte=today,
                                  subscription_expires__lte=today + timedelta(days=30)).count()
    expired     = tenants.filter(is_active=True, subscription_expires__isnull=False,
                                  subscription_expires__lt=today).count()
    lifetime    = tenants.filter(subscription_expires__isnull=True).count()
    suspended   = tenants.filter(is_active=False).count()

    # Plan distribution
    plan_counts = {p: tenants.filter(subscription_plan=p).count() for p, _ in Tenant.SUBSCRIPTION_PLANS}

    rows = []
    for t in tenants:
        days = t.days_until_expiry()
        if not t.subscription_expires:
            status_label = 'مدى الحياة'
            status_class = 'lifetime'
        elif not t.is_active:
            status_label = 'موقوف'
            status_class = 'suspended'
        elif days is not None and days < 0:
            status_label = f'منتهي منذ {abs(days)} يوم'
            status_class = 'expired'
        elif days is not None and days <= 7:
            status_label = f'ينتهي خلال {days} يوم'
            status_class = 'danger'
        elif days is not None and days <= 30:
            status_label = f'ينتهي خلال {days} يوم'
            status_class = 'warning'
        else:
            status_label = 'نشط'
            status_class = 'active'

        rows.append({
            'id': t.pk,
            'name': t.name,
            'slug': t.slug,
            'plan': t.get_subscription_plan_display(),
            'plan_key': t.subscription_plan,
            'version': t.get_version_type_display(),
            'start': t.subscription_start.strftime('%Y-%m-%d') if t.subscription_start else '—',
            'expires': t.subscription_expires.strftime('%Y-%m-%d') if t.subscription_expires else 'مدى الحياة',
            'days': days,
            'status_label': status_label,
            'status_class': status_class,
        })

    return render(request, 'core/admin_report_subscriptions.html', {
        'rows': rows,
        'today': today,
        'expiring_7': expiring_7,
        'expiring_30': expiring_30,
        'expired': expired,
        'lifetime': lifetime,
        'suspended': suspended,
        'total': tenants.count(),
        'plan_counts': plan_counts,
        'plan_counts_json': json.dumps(plan_counts, ensure_ascii=False),
    })


@login_required
def admin_report_revenue(request):
    if not request.user.has_platform_perm('view_reports'):
        return redirect('core:no_permission')

    from apps.sales.models import SaleInvoice
    from apps.purchases.models import PurchaseInvoice

    today = dj_timezone.localdate()
    # Build last 12 months
    months = []
    for i in range(11, -1, -1):
        d = today.replace(day=1) - timedelta(days=i * 30)
        months.append(d.replace(day=1))

    PLAN_PRICES = {'trial': 0, 'basic': 50, 'pro': 120, 'enterprise': 300}

    # Per-tenant usage stats
    tenants = Tenant.objects.filter(is_active=True).order_by('-created_at')
    tenant_stats = []
    for t in tenants:
        sales_count = SaleInvoice.objects.filter(tenant=t, status='confirmed').count()
        purchases_count = PurchaseInvoice.objects.filter(tenant=t, status='confirmed').count()
        sales_total = SaleInvoice.objects.filter(
            tenant=t, status='confirmed'
        ).aggregate(s=Sum('grand_total'))['s'] or 0

        tenant_stats.append({
            'name': t.name,
            'slug': t.slug,
            'plan': t.get_subscription_plan_display(),
            'plan_key': t.subscription_plan,
            'sales_count': sales_count,
            'purchases_count': purchases_count,
            'sales_total': float(sales_total),
            'monthly_fee': PLAN_PRICES.get(t.subscription_plan, 0),
        })

    tenant_stats.sort(key=lambda x: x['sales_count'], reverse=True)

    # Subscription revenue estimate per plan
    plan_revenue = {}
    for plan_key, price in PLAN_PRICES.items():
        count = Tenant.objects.filter(is_active=True, subscription_plan=plan_key).count()
        plan_revenue[plan_key] = {'count': count, 'monthly': count * price, 'price': price}

    total_mrr = sum(v['monthly'] for v in plan_revenue.values())

    # Monthly growth (new tenants per month for last 12m)
    _rev_tz = dj_timezone.get_current_timezone()
    monthly_new = []
    for m in months:
        next_m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        m_start = dj_timezone.make_aware(datetime.combine(m, datetime.min.time()), _rev_tz)
        m_end = dj_timezone.make_aware(datetime.combine(next_m, datetime.min.time()), _rev_tz)
        cnt = Tenant.objects.filter(created_at__gte=m_start, created_at__lt=m_end).count()
        monthly_new.append({'month': m.strftime('%b %Y'), 'count': cnt})

    return render(request, 'core/admin_report_revenue.html', {
        'tenant_stats': tenant_stats,
        'plan_revenue': plan_revenue,
        'total_mrr': total_mrr,
        'monthly_new': monthly_new,
        'monthly_new_json': json.dumps([m['count'] for m in monthly_new], ensure_ascii=False),
        'monthly_labels_json': json.dumps([m['month'] for m in monthly_new], ensure_ascii=False),
        'PLAN_PRICES': PLAN_PRICES,
    })


@login_required
def admin_report_activity(request):
    if not request.user.has_platform_perm('view_reports'):
        return redirect('core:no_permission')

    from apps.accounts.models import UserActivity

    now = dj_timezone.now()
    today = dj_timezone.localdate()
    week_ago_dt = now - timedelta(days=7)
    month_ago_dt = now - timedelta(days=30)

    # Per-tenant activity summary
    tenant_activity = (
        UserActivity.objects.filter(created_at__gte=month_ago_dt)
        .values('tenant__id', 'tenant__name', 'tenant__slug', 'tenant__subscription_plan')
        .annotate(total=Count('id'))
        .order_by('-total')[:20]
    )

    # Recent logins
    recent_logins = (
        UserActivity.objects.filter(action_type='login')
        .select_related('tenant', 'user')
        .order_by('-created_at')[:30]
    )

    # Action distribution
    action_counts = (
        UserActivity.objects.filter(created_at__gte=month_ago_dt)
        .values('action_type')
        .annotate(count=Count('id'))
        .order_by('-count')
    )

    action_map = {a: d for a, d in UserActivity.ACTION_CHOICES}
    action_dist = [
        {'key': row['action_type'], 'label': action_map.get(row['action_type'], row['action_type']), 'count': row['count']}
        for row in action_counts
    ]

    # Daily activity last 14 days
    daily = []
    local_tz = dj_timezone.get_current_timezone()
    for i in range(13, -1, -1):
        d = today - timedelta(days=i)
        d_start = dj_timezone.make_aware(datetime.combine(d, datetime.min.time()), local_tz)
        d_end = d_start + timedelta(days=1)
        cnt = UserActivity.objects.filter(created_at__gte=d_start, created_at__lt=d_end).count()
        daily.append({'date': d.strftime('%m/%d'), 'count': cnt})

    # Tenants with zero activity in last 30 days
    active_tenant_ids = set(
        UserActivity.objects.filter(created_at__gte=month_ago_dt)
        .values_list('tenant_id', flat=True).distinct()
    )
    active_tenant_ids.discard(None)
    inactive_tenants = Tenant.objects.filter(is_active=True).exclude(id__in=active_tenant_ids)

    active_count = len(active_tenant_ids)
    return render(request, 'core/admin_report_activity.html', {
        'tenant_activity': tenant_activity,
        'recent_logins': recent_logins,
        'action_dist': action_dist,
        'daily': daily,
        'daily_counts_json': json.dumps([d['count'] for d in daily], ensure_ascii=False),
        'daily_labels_json': json.dumps([d['date'] for d in daily], ensure_ascii=False),
        'inactive_tenants': inactive_tenants,
        'inactive_count': inactive_tenants.count(),
        'active_count': active_count,
        'today': today,
        'week_ago': (today - timedelta(days=7)),
        'month_ago': (today - timedelta(days=30)),
    })


@login_required
def admin_audit_log(request):
    if not request.user.has_platform_perm('view_audit_log'):
        return redirect('core:no_permission')

    from apps.accounts.models import UserActivity

    qs = UserActivity.objects.select_related('tenant', 'user').order_by('-created_at')

    tenant_id  = request.GET.get('tenant', '')
    action     = request.GET.get('action', '')
    date_from  = request.GET.get('date_from', '')
    date_to    = request.GET.get('date_to', '')
    search     = request.GET.get('q', '')

    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)
    if action:
        qs = qs.filter(action_type=action)
    local_tz = dj_timezone.get_current_timezone()
    if date_from:
        try:
            d = datetime.strptime(date_from, '%Y-%m-%d').date()
            qs = qs.filter(created_at__gte=dj_timezone.make_aware(datetime.combine(d, datetime.min.time()), local_tz))
        except ValueError:
            pass
    if date_to:
        try:
            d = datetime.strptime(date_to, '%Y-%m-%d').date()
            qs = qs.filter(created_at__lt=dj_timezone.make_aware(datetime.combine(d, datetime.min.time()), local_tz) + timedelta(days=1))
        except ValueError:
            pass
    if search:
        qs = qs.filter(
            Q(title__icontains=search) |
            Q(details__icontains=search) |
            Q(user__username__icontains=search) |
            Q(tenant__name__icontains=search)
        )

    total_count = qs.count()
    logs = qs[:500]

    tenants = Tenant.objects.filter(is_active=True).order_by('name')
    action_choices = UserActivity.ACTION_CHOICES

    return render(request, 'core/admin_audit_log.html', {
        'logs': logs,
        'tenants': tenants,
        'action_choices': action_choices,
        'total_count': total_count,
        'filters': {
            'tenant_id': tenant_id,
            'action': action,
            'date_from': date_from,
            'date_to': date_to,
            'q': search,
        },
    })


@login_required
def admin_notifications(request):
    """صفحة إشعارات مدير النظام"""
    if not request.user.has_platform_perm('view_notifications'):
        return redirect('core:no_permission')

    from .models import AdminNotification, SupportTicket

    # Auto-generate expiring/expired subscription notifications on each visit
    today = dj_timezone.localdate()
    expiring_soon = Tenant.objects.filter(
        is_active=True,
        subscription_expires__isnull=False,
        subscription_expires__gte=today,
        subscription_expires__lte=today + timedelta(days=7),
    )
    for t in expiring_soon:
        days_left = (t.subscription_expires - today).days
        AdminNotification.push(
            notification_type='subscription_expiring',
            title=f'اشتراك "{t.name}" سينتهي خلال {days_left} يوم',
            message=f'الباقة: {t.get_subscription_plan_display()} — ينتهي في {t.subscription_expires}',
            link='/tenants/',
            priority='high',
            ref_key=f'expiring_{t.pk}_{t.subscription_expires}',
        )

    expired = Tenant.objects.filter(is_active=True, subscription_expires__lt=today)
    for t in expired:
        AdminNotification.push(
            notification_type='subscription_expired',
            title=f'اشتراك "{t.name}" منتهٍ',
            message=f'انتهى الاشتراك في {t.subscription_expires} — يحتاج تجديداً.',
            link='/tenants/',
            priority='high',
            ref_key=f'expired_{t.pk}_{t.subscription_expires}',
        )

    # Old open tickets (> 3 days)
    old_cutoff = dj_timezone.now() - timedelta(days=3)
    old_tickets = SupportTicket.objects.filter(status='open', created_at__lt=old_cutoff)
    for ticket in old_tickets:
        AdminNotification.push(
            notification_type='old_ticket',
            title=f'تذكرة مفتوحة منذ أكثر من 3 أيام #{ticket.pk}',
            message=f'{ticket.tenant.name if ticket.tenant else ""}: {ticket.subject}',
            link='/support/',
            priority='medium',
            ref_key=f'old_ticket_{ticket.pk}',
        )

    notifications = AdminNotification.objects.all()
    unread_count = notifications.filter(is_read=False).count()

    return render(request, 'core/admin_notifications.html', {
        'notifications': notifications[:100],
        'unread_count': unread_count,
    })


@login_required
@require_POST
def admin_notifications_mark_read(request, pk):
    if not request.user.is_superuser:
        return JsonResponse({'success': False}, status=403)
    from .models import AdminNotification
    AdminNotification.objects.filter(pk=pk).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
@require_POST
def admin_notifications_mark_all_read(request):
    if not request.user.is_superuser:
        return JsonResponse({'success': False}, status=403)
    from .models import AdminNotification
    AdminNotification.objects.filter(is_read=False).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
def admin_notifications_api(request):
    """Returns unread count + recent items for the navbar bell (superuser only)."""
    if not request.user.is_superuser:
        return JsonResponse({'unread': 0, 'items': []})
    from .models import AdminNotification

    TYPE_ICONS = {
        'new_tenant':            'fa-building text-success',
        'new_ticket':            'fa-ticket text-primary',
        'subscription_expiring': 'fa-clock text-warning',
        'subscription_expired':  'fa-times-circle text-danger',
        'old_ticket':            'fa-hourglass-half text-purple',
        'general':               'fa-bell text-secondary',
    }

    unread = AdminNotification.objects.filter(is_read=False).count()
    recent = AdminNotification.objects.all()[:8]
    items = [
        {
            'id':      n.pk,
            'title':   n.title,
            'message': n.message[:80],
            'is_read': n.is_read,
            'link':    n.link,
            'priority': n.priority,
            'icon':    TYPE_ICONS.get(n.notification_type, 'fa-bell text-secondary'),
            'time':    n.created_at.strftime('%Y-%m-%d %H:%M'),
        }
        for n in recent
    ]
    return JsonResponse({'unread': unread, 'items': items})


@login_required
def admin_settings(request):
    if not request.user.is_superuser:
        return redirect('core:no_permission')
    from .models import PlatformSettings
    from .constants import COUNTRY_TIMEZONE_MAP
    ps = PlatformSettings.get()
    timezones = sorted(set(COUNTRY_TIMEZONE_MAP.values()))
    return render(request, 'core/admin_settings.html', {
        'ps': ps,
        'currency_choices': CURRENCY_CHOICES,
        'timezones': timezones,
        'timezone_currency_map_json': json.dumps(TIMEZONE_CURRENCY_MAP, ensure_ascii=False),
    })


@login_required
def admin_settings_update_api(request):
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'error': 'غير مصرح'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'طريقة غير مدعومة'}, status=405)

    from .models import PlatformSettings
    from django.core.cache import cache

    ps = PlatformSettings.get()
    section = request.POST.get('section', '')

    if section == 'branding':
        name = request.POST.get('platform_name', '').strip()
        if name:
            ps.platform_name = name
        ps.platform_tagline = request.POST.get('platform_tagline', '').strip()
        ps.primary_color    = request.POST.get('primary_color', ps.primary_color).strip()
        ps.footer_text      = request.POST.get('footer_text', '').strip()
        if 'platform_logo' in request.FILES:
            ps.platform_logo = request.FILES['platform_logo']
        if 'platform_favicon' in request.FILES:
            ps.platform_favicon = request.FILES['platform_favicon']

    elif section == 'maintenance':
        ps.maintenance_mode    = request.POST.get('maintenance_mode') == 'true'
        msg = request.POST.get('maintenance_message', '').strip()
        if msg:
            ps.maintenance_message = msg
        cache.delete('platform_maintenance_mode')

    elif section == 'announcement':
        ps.announcement_active = request.POST.get('announcement_active') == 'true'
        ps.announcement_text   = request.POST.get('announcement_text', '').strip()
        ps.announcement_type   = request.POST.get('announcement_type', 'info')

    elif section == 'registration':
        ps.self_registration_enabled = request.POST.get('self_registration_enabled') == 'true'

    elif section == 'defaults':
        currency = request.POST.get('default_currency', '').strip()
        if currency:
            ps.default_currency = currency
        tz = request.POST.get('default_timezone', '').strip()
        if tz:
            ps.default_timezone = tz
        ps.default_tax_enabled    = request.POST.get('default_tax_enabled') == 'true'
        try:
            ps.default_tax_value  = float(request.POST.get('default_tax_value') or 0)
        except (ValueError, TypeError):
            pass
        prefix = request.POST.get('default_invoice_prefix', '').strip()
        if prefix:
            ps.default_invoice_prefix = prefix
        try:
            ps.default_trial_days = int(request.POST.get('default_trial_days') or 0)
        except (ValueError, TypeError):
            pass
    else:
        return JsonResponse({'success': False, 'error': 'قسم غير معروف'}, status=400)

    ps.save()
    cache.delete('platform_settings_ctx')
    return JsonResponse({'success': True})


@login_required
def admin_backup(request):
    """لوحة النسخ الاحتياطي — قائمة جميع المشتركين مع إحصاء نسخهم"""
    if not request.user.has_platform_perm('manage_backups'):
        return redirect('core:no_permission')

    from .backup_service import get_tenant_backup_stats

    tenants = Tenant.objects.order_by('name')
    tenant_rows = []
    total_backups = 0
    total_size = 0

    for tenant in tenants:
        stats = get_tenant_backup_stats(tenant)
        total_backups += stats['total']
        total_size += stats['total_size']
        tenant_rows.append({
            'tenant': tenant,
            'count': stats['total'],
            'total_size': stats['total_size'],
            'latest': stats['latest'],
        })

    def _fmt_size(size):
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.2f} MB"

    context = {
        'tenant_rows': tenant_rows,
        'total_backups': total_backups,
        'total_size_display': _fmt_size(total_size),
        'tenants_count': tenants.count(),
        'tenants_no_backup': sum(1 for r in tenant_rows if r['count'] == 0),
    }
    return render(request, 'core/admin_backup.html', context)


@login_required
def admin_backup_detail(request, tenant_slug):
    """تفاصيل النسخ الاحتياطية لمشترك محدد"""
    if not request.user.has_platform_perm('manage_backups'):
        return redirect('core:no_permission')
    tenant = get_object_or_404(Tenant, slug=tenant_slug)
    backups = TenantBackup.objects.filter(tenant=tenant).order_by('-created_at')
    context = {
        'tenant': tenant,
        'backups': backups,
        'total_size': sum(b.file_size for b in backups if b.status == 'completed'),
    }
    return render(request, 'core/admin_backup_detail.html', context)


@login_required
@require_POST
def admin_backup_create_api(request, tenant_slug):
    """API: إنشاء نسخة احتياطية يدوية"""
    err = _superuser_required(request, 'manage_backups')
    if err:
        return err
    tenant = get_object_or_404(Tenant, slug=tenant_slug)
    from .backup_service import create_backup
    try:
        record = create_backup(tenant, backup_type='manual')
        if record.status == 'completed':
            return JsonResponse({
                'success': True,
                'message': f'تم إنشاء النسخة الاحتياطية بنجاح ({record.file_size_display})',
                'backup': {
                    'id': record.id,
                    'filename': record.filename,
                    'size': record.file_size_display,
                    'type': record.get_backup_type_display(),
                    'created_at': record.created_at.strftime('%Y-%m-%d %H:%M'),
                    'status': record.status,
                },
            })
        return JsonResponse({'success': False, 'message': 'فشل إنشاء النسخة الاحتياطية'}, status=500)
    except Exception as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=500)


@login_required
@require_POST
def admin_backup_restore_api(request, backup_id):
    """API: استعادة نسخة احتياطية"""
    err = _superuser_required(request, 'manage_backups')
    if err:
        return err
    from .backup_service import restore_backup
    success, message = restore_backup(backup_id)
    status_code = 200 if success else 500
    return JsonResponse({'success': success, 'message': message}, status=status_code)


@login_required
@require_POST
def admin_backup_delete_api(request, backup_id):
    """API: حذف نسخة احتياطية"""
    err = _superuser_required(request, 'manage_backups')
    if err:
        return err
    from .backup_service import delete_backup
    success, message = delete_backup(backup_id)
    return JsonResponse({'success': success, 'message': message})


@login_required
def admin_backup_download(request, backup_id):
    """تنزيل ملف النسخة الاحتياطية — للمشرف"""
    if not request.user.has_platform_perm('manage_backups'):
        return redirect('core:no_permission')
    from django.http import FileResponse, Http404
    from pathlib import Path
    backup = get_object_or_404(TenantBackup, pk=backup_id, status='completed')
    file_path = Path(backup.file_path)
    if not file_path.exists():
        raise Http404("الملف غير موجود")
    response = FileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=backup.filename,
        content_type='application/sql',
    )
    return response


# ─────────────────────────────────────────────────────────────────────────────
# TENANT BACKUP — يدوي للمشتركين (إنشاء + تنزيل، بدون استرجاع)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def tenant_backup_create_api(request):
    """إنشاء نسخة احتياطية يدوية — متاح لجميع الباقات"""
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط مرتبط بالمستخدم'}, status=400)

    from .backup_service import create_backup
    try:
        record = create_backup(tenant, backup_type='manual')
        if record.status == 'completed':
            return JsonResponse({
                'success': True,
                'backup': {
                    'id':        record.pk,
                    'filename':  record.filename,
                    'size':      record.file_size_display,
                    'created_at': record.created_at.strftime('%Y-%m-%d %H:%M'),
                }
            })
        return JsonResponse({'success': False, 'message': 'فشل إنشاء النسخة الاحتياطية'}, status=500)
    except Exception as exc:
        return JsonResponse({'success': False, 'message': str(exc)}, status=500)


@login_required
def tenant_backup_download(request, backup_id):
    """تنزيل نسخة احتياطية — للمشترك نفسه فقط"""
    from django.http import FileResponse, Http404
    from pathlib import Path
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return redirect('core:no_permission')
    backup = get_object_or_404(TenantBackup, pk=backup_id, tenant=tenant, status='completed')
    file_path = Path(backup.file_path)
    if not file_path.exists():
        raise Http404("الملف غير موجود")
    return FileResponse(
        open(file_path, 'rb'),
        as_attachment=True,
        filename=backup.filename,
        content_type='application/sql',
    )


@login_required
def admin_training(request):
    if not (request.user.is_superuser or getattr(request.user, 'is_platform_staff', False)):
        return redirect('core:no_permission')
    return render(request, 'core/admin_training.html', {})


def subscription_expired(request):
    """صفحة انتهاء الاشتراك"""
    return render(request, 'core/subscription_expired.html')


def terms_of_service(request):
    """صفحة اتفاقية الاستخدام — عرض + قبول"""
    from django.conf import settings as dj_settings
    from django.utils import timezone as tz

    current_version = dj_settings.TERMS_VERSION

    if request.method == 'POST' and request.user.is_authenticated:
        tenant = getattr(request, 'tenant', None)
        if tenant:
            tenant.terms_accepted_at = tz.now()
            tenant.terms_version = current_version
            tenant.save(update_fields=['terms_accepted_at', 'terms_version'])
            next_url = request.POST.get('next') or '/'
            return redirect(next_url)

    next_url = request.GET.get('next', '/')
    needs_acceptance = (
        request.user.is_authenticated
        and not getattr(request.user, 'is_superuser', False)
        and not getattr(request.user, 'is_platform_staff', False)
        and hasattr(request, 'tenant')
        and request.tenant
        and (not request.tenant.terms_accepted_at or request.tenant.terms_version != current_version)
    )
    return render(request, 'core/terms_of_service.html', {
        'terms_version': current_version,
        'next': next_url,
        'needs_acceptance': needs_acceptance,
    })


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
    tenant = getattr(request, 'tenant', None)
    backups = (
        TenantBackup.objects.filter(tenant=tenant).order_by('-created_at')[:10]
        if tenant else []
    )
    return render(request, 'core/tenant_settings.html', {
        'country_choices': COUNTRY_CHOICES,
        'country_timezone_map_json': json.dumps(COUNTRY_TIMEZONE_MAP, ensure_ascii=False),
        'country_currency_map_json': json.dumps(COUNTRY_CURRENCY_MAP, ensure_ascii=False),
        'currency_ar_json': json.dumps(CURRENCY_AR, ensure_ascii=False),
        'default_country': DEFAULT_COUNTRY,
        'currency_choices': CURRENCY_CHOICES,
        'backups': backups,
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
    raw_color = str(data.get('invoice_color', settings_obj.invoice_color)).strip()
    import re as _re
    if _re.match(r'^#[0-9a-fA-F]{6}$', raw_color):
        settings_obj.invoice_color = raw_color
    settings_obj.tax_enabled = _as_bool(data.get('tax_enabled', settings_obj.tax_enabled))
    raw_tax = data.get('tax_value')
    settings_obj.tax_value = _as_decimal(raw_tax or '0', settings_obj.tax_value) if raw_tax is not None else settings_obj.tax_value
    settings_obj.tax_number = str(data.get('tax_number', settings_obj.tax_number)).strip()
    settings_obj.items_per_page = max(5, min(200, _as_int(data.get('items_per_page', settings_obj.items_per_page), settings_obj.items_per_page)))
    settings_obj.date_format = str(data.get('date_format', settings_obj.date_format)).strip() or settings_obj.date_format
    settings_obj.print_sale_invoice = _as_bool(data.get('print_sale_invoice', settings_obj.print_sale_invoice))
    settings_obj.print_purchase_invoice = _as_bool(data.get('print_purchase_invoice', settings_obj.print_purchase_invoice))
    settings_obj.print_agent_name = _as_bool(data.get('print_agent_name', settings_obj.print_agent_name))
    settings_obj.show_zero_stock = _as_bool(data.get('show_zero_stock', settings_obj.show_zero_stock))
    settings_obj.low_stock_alert = _as_bool(data.get('low_stock_alert', settings_obj.low_stock_alert))
    settings_obj.save()

    return JsonResponse({'success': True, 'message': 'تم تحديث إعدادات النظام بنجاح'})


@login_required
@require_permission('change_tenant_settings')
@require_POST
def exchange_rate_update_api(request):
    """API: تحديث سعر الصرف وإعادة حساب أسعار كل المنتجات تلقائياً."""
    from apps.items.models import Item
    from django.db import transaction as db_transaction

    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط مرتبط'}, status=400)

    if not tenant.hard_currency_mode:
        return JsonResponse({'success': False, 'message': 'وضع العملة الصعبة غير مفعّل'}, status=400)

    try:
        payload = json.loads(request.body or '{}')
        new_rate = Decimal(str(payload.get('exchange_rate', '')).replace(',', '.').strip())
        if new_rate <= 0:
            raise ValueError
    except (InvalidOperation, ValueError, TypeError):
        return JsonResponse({'success': False, 'message': 'سعر الصرف غير صالح'}, status=400)

    notes = payload.get('notes', '').strip()[:200]
    with db_transaction.atomic():
        from .models import ExchangeRateHistory
        previous_rate = tenant.exchange_rate
        tenant.exchange_rate = new_rate
        tenant.exchange_rate_updated_at = dj_timezone.now()
        tenant.save(update_fields=['exchange_rate', 'exchange_rate_updated_at', 'updated_at'])
        ExchangeRateHistory.objects.create(
            tenant=tenant,
            rate=new_rate,
            changed_by=request.user,
            notes=notes or f'السعر السابق: {previous_rate}',
        )

        from apps.core.models import ExchangeRateHistory
        ExchangeRateHistory.objects.create(
            tenant=tenant,
            rate=new_rate,
            changed_by=request.user if request.user.is_authenticated else None,
            notes=notes,
        )

        items = Item.objects.for_tenant(tenant).filter(selling_price_hc__isnull=False)
        updated = 0
        bulk = []
        for item in items:
            if item.selling_price_hc:
                item.selling_price = (item.selling_price_hc * new_rate).quantize(Decimal('0.01'))
            if item.cost_price_hc:
                item.cost_price = (item.cost_price_hc * new_rate).quantize(Decimal('0.01'))
            if item.min_selling_price_hc:
                item.min_selling_price = (item.min_selling_price_hc * new_rate).quantize(Decimal('0.01'))
            bulk.append(item)
            updated += 1
        if bulk:
            Item.objects.bulk_update(bulk, ['selling_price', 'cost_price', 'min_selling_price'])

    log_activity(request, 'تغيير سعر الصرف', f'{previous_rate} ← {new_rate}', 'update')
    return JsonResponse({
        'success': True,
        'message': f'تم تحديث سعر الصرف وإعادة تسعير {updated} منتج',
        'updated_items': updated,
        'new_rate': str(new_rate),
    })


@login_required
@require_permission('change_tenant_settings')
def exchange_rate_page(request):
    """صفحة سعر الصرف المستقلة — فورم + سجل + مخطط."""
    from apps.core.models import ExchangeRateHistory
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        from django.shortcuts import redirect
        return redirect('core:dashboard')

    history = ExchangeRateHistory.objects.filter(tenant=tenant).order_by('-changed_at')[:90]
    return render(request, 'core/exchange_rate.html', {
        'tenant': tenant,
        'history': history,
    })


@login_required
@require_permission('change_tenant_settings')
def exchange_rate_history_api(request):
    """JSON: آخر 90 يوم من سجل سعر الصرف للمخطط."""
    from apps.core.models import ExchangeRateHistory
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return JsonResponse({'success': False}, status=400)

    qs = ExchangeRateHistory.objects.filter(tenant=tenant).order_by('changed_at')[:90]
    data = [
        {
            'date': e.changed_at.strftime('%Y-%m-%d'),
            'rate': str(e.rate),
            'user': e.changed_by.get_full_name() or e.changed_by.username if e.changed_by else '—',
            'notes': e.notes or '',
        }
        for e in qs
    ]
    return JsonResponse({'success': True, 'data': data})


@login_required
@require_permission('change_tenant_settings')
@require_POST
def tenant_logo_upload_api(request):
    """رفع شعار النشاط التجاري — يحذف القديم ويحفظ بـ tenant_<id>.<ext>"""
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط مرتبط'}, status=400)

    if 'logo' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'لم يتم اختيار ملف'}, status=400)

    uploaded = request.FILES['logo']
    allowed = ('image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/svg+xml')
    if uploaded.content_type not in allowed:
        return JsonResponse({'success': False, 'message': 'صيغة الملف غير مدعومة'}, status=400)

    ext = os.path.splitext(uploaded.name)[1].lower() or '.png'
    new_name = f'tenant_{tenant.pk}{ext}'

    # حذف الشعار القديم من القرص إن وجد
    if tenant.logo:
        old_path = tenant.logo.path
        try:
            if os.path.isfile(old_path):
                os.remove(old_path)
        except OSError:
            pass

    # حفظ الملف الجديد — ImageField يضع المسار ضمن upload_to='tenants/logos/'
    tenant.logo.save(new_name, uploaded, save=True)

    logo_url = tenant.logo.url if tenant.logo else ''
    return JsonResponse({'success': True, 'message': 'تم رفع الشعار بنجاح', 'logo_url': logo_url})


@login_required
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

def _superuser_required(request, perm=None):
    """Return 403 JsonResponse if not authorized, else None.
    If perm is given, checks has_platform_perm(); otherwise requires is_superuser.
    """
    if perm:
        if not request.user.has_platform_perm(perm):
            return JsonResponse({'success': False, 'message': 'غير مصرح'}, status=403)
    else:
        if not request.user.is_superuser:
            return JsonResponse({'success': False, 'message': 'غير مصرح'}, status=403)
    return None


@login_required
def tenant_list(request):
    """قائمة المشتركين (المشتركين) - للمشرف فقط"""
    if not request.user.has_platform_perm('manage_tenants'):
        return render(request, 'core/no_permission.html', status=403)
    business_types = BusinessType.objects.filter(is_active=True).order_by('display_order', 'name_ar')
    total = Tenant.objects.count()
    active = Tenant.objects.filter(is_active=True).count()
    suspended = total - active
    today = dj_timezone.localdate()
    expired = Tenant.objects.filter(is_active=True, subscription_expires__lt=today).count()
    context = {
        'form': TenantForm(),
        'business_types': business_types,
        'stats': {'total': total, 'active': active, 'suspended': suspended, 'expired': expired},
        'country_timezone_map': COUNTRY_TIMEZONE_MAP,
        'country_currency_map': COUNTRY_CURRENCY_MAP,
        'currency_ar': CURRENCY_AR,
    }
    return render(request, 'core/tenant_list.html', context)


@login_required
def tenant_table_api(request):
    """API: جدول المشتركين لـ DataTable"""
    err = _superuser_required(request, 'manage_tenants')
    if err:
        return err

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '').strip()
    plan_filter = request.GET.get('plan', '').strip()

    from apps.store.models import StoreSettings

    qs = Tenant.objects.select_related('business_type').prefetch_related('storesettings_set').all()
    records_total = qs.count()

    today = dj_timezone.localdate()

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

    import logging as _logging
    _logger = _logging.getLogger(__name__)

    data = []
    for t in qs:
        try:
            days = t.days_until_expiry()
            if not t.subscription_expires:
                exp_label = 'مفتوحة'
                exp_status = 'lifetime'
            elif days is not None and days < 0:
                exp_label = f'منتهية منذ {abs(days)} يوم'
                exp_status = 'expired'
            elif days is not None and days <= 30:
                exp_label = f'ينتهي خلال {days} يوم'
                exp_status = 'soon'
            else:
                exp_label = t.subscription_expires.strftime('%Y-%m-%d') if t.subscription_expires else '—'
                exp_status = 'ok'

            store = next(iter(t.storesettings_set.all()), None)
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
                'is_demo': getattr(t, 'is_demo', False),
                'subscription_expires': t.subscription_expires.strftime('%Y-%m-%d') if t.subscription_expires else None,
                'exp_label': exp_label,
                'exp_status': exp_status,
                'email': t.email or '—',
                'phone': t.phone or '—',
                'city': t.city or '—',
                'store_slug': store.slug if store else None,
                'store_enabled': store.is_enabled if store else False,
            })
        except Exception as _e:
            _logger.error('tenant_table_api: error serializing tenant %s: %s', t.id, _e)

    response = JsonResponse({'draw': draw, 'recordsTotal': records_total, 'recordsFiltered': records_filtered, 'data': data})
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    return response


@login_required
def tenant_create_api(request):
    """API: إنشاء مشترك جديد مع مستخدم مدير"""
    err = _superuser_required(request, 'manage_tenants')
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

    # Send admin notification email (non-blocking)
    try:
        from django.core.mail import EmailMessage
        from django.template.loader import render_to_string
        from django.utils import timezone

        dashboard_url = request.build_absolute_uri('/tenants/')
        html_body = render_to_string('core/email/new_tenant_notification.html', {
            'tenant': tenant,
            'admin_full_name': full_name,
            'admin_username': username,
            'admin_email': email,
            'created_at': timezone.now(),
            'dashboard_url': dashboard_url,
        })
        msg = EmailMessage(
            subject=f'New Tenant Registered: {tenant.name}',
            body=html_body,
            from_email='EnjazIMS <{}>'.format(settings.EMAIL_HOST_USER),
            to=['khattabaljaily@gmail.com'],
        )
        msg.content_subtype = 'html'
        msg.send(fail_silently=False)
    except Exception as _email_err:
        import logging
        logging.getLogger(__name__).error('tenant notification email failed: %s', _email_err, exc_info=True)

    return JsonResponse({
        'success': True,
        'message': f'تم إنشاء المشترك "{tenant.name}" ومدير النشاط "{username}" بنجاح',
        'id': tenant.id,
    })


@login_required
def tenant_detail_api(request, pk):
    """API: تفاصيل مشترك"""
    err = _superuser_required(request, 'manage_tenants')
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
            'hard_currency_mode': tenant.hard_currency_mode,
            'hard_currency': tenant.hard_currency or 'USD',
            'exchange_rate': float(tenant.exchange_rate) if tenant.exchange_rate else 1,
        }
    })


@login_required
def tenant_update_api(request, pk):
    """API: تعديل بيانات مشترك"""
    err = _superuser_required(request, 'manage_tenants')
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

    return JsonResponse({'success': True, 'message': 'تم تعديل بيانات المشترك بنجاح'})


@login_required
def tenant_delete_api(request, pk):
    """API: حذف مشترك"""
    err = _superuser_required(request, 'manage_tenants')
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
    return JsonResponse({'success': True, 'message': f'تم حذف المشترك "{name}" بنجاح'})


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
        from apps.store.models import OnlineOrderLine, OnlineOrder
        from apps.agents.models import AgentInvoiceRequestLine, AgentInvoiceRequest, AgentLedger

        # --- agent sub-documents (AgentLedger.agent / AgentInvoiceRequest.agent PROTECT Agent,
        #     AgentInvoiceRequestLine.item PROTECT Item) ---
        AgentInvoiceRequestLine.objects.filter(**t).delete()
        AgentInvoiceRequest.objects.filter(**t).delete()
        AgentLedger.objects.filter(**t).delete()
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
        # --- online store orders (OnlineOrderLine.item PROTECT Item) ---
        OnlineOrderLine.objects.filter(**t).delete()
        OnlineOrder.objects.filter(**t).delete()
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
        # After the above, tenant.delete() cascades safely through remaining relations.
    finally:
        tenant_deletion_in_progress.reset(token)


@login_required
def tenant_suspend_api(request, pk):
    """API: تعليق / إلغاء تعليق مشترك"""
    err = _superuser_required(request, 'manage_tenants')
    if err:
        return err
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    tenant = get_object_or_404(Tenant, pk=pk)
    tenant.is_active = not tenant.is_active
    tenant.save(update_fields=['is_active', 'updated_at'])
    action = 'تم تفعيل' if tenant.is_active else 'تم تعليق'
    return JsonResponse({'success': True, 'message': f'{action} المشترك "{tenant.name}" بنجاح', 'is_active': tenant.is_active})


@login_required
def tenant_renew_api(request, pk):
    """API: تجديد اشتراك مشترك"""
    err = _superuser_required(request, 'manage_tenants')
    if err:
        return err
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    tenant = get_object_or_404(Tenant, pk=pk)

    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'بيانات غير صالحة'}, status=400)

    days = int(payload.get('days', 30))
    plan = payload.get('plan', '').strip()

    if days <= 0 or days > 3650:
        return JsonResponse({'success': False, 'error': 'عدد الأيام غير صالح'}, status=400)

    today = dj_timezone.localdate()
    base_date = tenant.subscription_expires if (tenant.subscription_expires and tenant.subscription_expires >= today) else today
    tenant.subscription_expires = base_date + timedelta(days=days)

    update_fields = ['subscription_expires', 'updated_at']

    if plan and plan in dict(Tenant.SUBSCRIPTION_PLANS):
        tenant.subscription_plan = plan
        update_fields.append('subscription_plan')

    if not tenant.is_active:
        tenant.is_active = True
        update_fields.append('is_active')

    tenant.save(update_fields=update_fields)

    return JsonResponse({
        'success': True,
        'message': f'تم تجديد اشتراك "{tenant.name}" حتى {tenant.subscription_expires.strftime("%Y-%m-%d")}',
        'new_expires': tenant.subscription_expires.strftime('%Y-%m-%d'),
        'plan': tenant.get_subscription_plan_display(),
    })


def pricing(request):
    """صفحة خطط التسعير"""
    trial_days = 30
    plans = [
        {
            'key': 'basic',
            'name': 'Basic',
            'title_ar': 'أساسي',
            'description': 'محل واحد مع مخزن واحد',
            'monthly': '$35',
            'annual': '$378',
            'perpetual': '$3,400',
            'highlight': False,
            'features': [
                {'text': '1 مخزن',                          'ok': True},
                {'text': 'حتى 5 مستخدمين',                  'ok': True},
                {'text': f'تجربة مجانية {trial_days} أيام', 'ok': True},
                {'text': 'فواتير مبيعات وشراء',              'ok': True},
                {'text': 'تقارير تفصيلية',                   'ok': True},
                {'text': 'نسخ احتياطي يدوي',                 'ok': True},
                {'text': 'متجر إلكتروني مدمج',               'ok': False},
                {'text': 'مساعد ذكاء اصطناعي',              'ok': False},
                {'text': 'نصائح ذكية',                       'ok': False},
                {'text': 'إدارة المناديب وبوابتهم',           'ok': False},
                {'text': 'نسخ احتياطي آلي',                  'ok': False},
            ],
        },
        {
            'key': 'pro',
            'name': 'Pro',
            'title_ar': 'احترافي',
            'description': 'محل واحد مع ما يصل إلى 5 مخازن',
            'monthly': '$55',
            'annual': '$594',
            'perpetual': '$5,340',
            'highlight': True,
            'features': [
                {'text': 'حتى 5 مخازن',                      'ok': True},
                {'text': 'حتى 15 مستخدمًا',                  'ok': True},
                {'text': f'تجربة مجانية {trial_days} أيام', 'ok': True},
                {'text': 'فواتير مبيعات وشراء',              'ok': True},
                {'text': 'تقارير تفصيلية',                   'ok': True},
                {'text': 'نسخ احتياطي يدوي',                 'ok': True},
                {'text': 'متجر إلكتروني مدمج',               'ok': True},
                {'text': 'مساعد ذكاء اصطناعي',              'ok': True},
                {'text': 'نصائح ذكية',                       'ok': True},
                {'text': 'إدارة المناديب وبوابتهم',           'ok': True},
                {'text': 'نسخ احتياطي آلي (مرة يومياً)',     'ok': True},
            ],
        },
        {
            'key': 'enterprise',
            'name': 'Enterprise',
            'title_ar': 'مؤسسات',
            'description': 'فروع ومخازن متعددة مع تحكم كامل',
            'monthly': '$149',
            'annual': '$1,610',
            'perpetual': '$14,490',
            'highlight': False,
            'features': [
                {'text': 'حتى 20 مخزن',                      'ok': True},
                {'text': 'حتى 40 مستخدمًا',                  'ok': True},
                {'text': f'تجربة مجانية {trial_days} أيام', 'ok': True},
                {'text': 'فواتير مبيعات وشراء',              'ok': True},
                {'text': 'تقارير تفصيلية',                   'ok': True},
                {'text': 'نسخ احتياطي يدوي',                 'ok': True},
                {'text': 'متجر إلكتروني مدمج',               'ok': True},
                {'text': 'مساعد ذكاء اصطناعي',              'ok': True},
                {'text': 'نصائح ذكية',                       'ok': True},
                {'text': 'إدارة المناديب وبوابتهم',           'ok': True},
                {'text': 'نسخ احتياطي آلي (مرتين يومياً)',   'ok': True},
            ],
        },
    ]

    tenant = getattr(request, 'tenant', None)
    current_plan_key = getattr(tenant, 'subscription_plan', None) if tenant else None
    for p in plans:
        p['is_current'] = bool(current_plan_key and current_plan_key == p['key'])

    return render(request, 'core/pricing.html', {
        'plans': plans,
        'trial_days': trial_days,
        'current_subscription_plan_display': tenant.get_subscription_plan_display() if tenant else None,
    })



@login_required
@require_POST
def help_open_track(request):
    """Called from JS when user opens the help panel."""
    from apps.accounts.activity_service import log_activity
    try:
        body = json.loads(request.body)
        page = (body.get('page') or '')[:200]
    except (json.JSONDecodeError, ValueError):
        page = ''
    log_activity(request, 'فتح لوحة المساعدة', f'الصفحة: {page}' if page else '', 'other')
    return JsonResponse({'ok': True})



def service_worker(request):
    """Serve the PWA service worker from root scope /sw.js"""
    import os
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'js', 'sw.js')
    return FileResponse(open(sw_path, 'rb'), content_type='application/javascript')


def pwa_manifest(request):
    """Serve the PWA manifest from /manifest.json"""
    import os
    manifest_path = os.path.join(settings.BASE_DIR, 'static', 'manifest.json')
    return FileResponse(open(manifest_path, 'rb'), content_type='application/manifest+json')


@login_required
@require_permission('view_analytics')
def analytics(request):
    """Advanced analytics dashboard — 12-month trends, period comparison, profit, top customers"""
    tenant = request.tenant
    if not tenant:
        return redirect('core:no_tenant')

    today = dj_timezone.localdate()
    first_this_month = today.replace(day=1)

    # ── Previous month boundaries ───────────────────────────────────
    last_day_prev = first_this_month - timedelta(days=1)
    first_prev_month = last_day_prev.replace(day=1)

    from apps.sales.models import SaleInvoice, SaleInvoiceLine
    from apps.purchases.models import PurchaseInvoice
    from apps.customers.models import Customer
    from apps.suppliers.models import Supplier

    def sales_total(qs_filter):
        return float(SaleInvoice.objects.filter(tenant=tenant, status='confirmed', **qs_filter)
                     .aggregate(t=Sum('grand_total'))['t'] or 0)

    def purchase_total(qs_filter):
        return float(PurchaseInvoice.objects.filter(tenant=tenant, status='confirmed', **qs_filter)
                     .aggregate(t=Sum('grand_total'))['t'] or 0)

    def expense_total(qs_filter):
        return float(Expense.objects.filter(tenant=tenant, status='confirmed', **qs_filter)
                     .aggregate(t=Sum('amount'))['t'] or 0)

    # ── Period comparison KPIs ──────────────────────────────────────
    this_sales    = sales_total({'invoice_date__gte': first_this_month, 'invoice_date__lte': today})
    prev_sales    = sales_total({'invoice_date__gte': first_prev_month, 'invoice_date__lte': last_day_prev})
    this_purchases = purchase_total({'invoice_date__gte': first_this_month, 'invoice_date__lte': today})
    prev_purchases = purchase_total({'invoice_date__gte': first_prev_month, 'invoice_date__lte': last_day_prev})
    this_expenses = expense_total({'expense_date__gte': first_this_month, 'expense_date__lte': today})
    prev_expenses = expense_total({'expense_date__gte': first_prev_month, 'expense_date__lte': last_day_prev})

    this_profit  = this_sales - this_purchases - this_expenses
    prev_profit  = prev_sales - prev_purchases - prev_expenses

    def pct_change(curr, prev):
        if prev == 0:
            return None
        return round(((curr - prev) / prev) * 100, 1)

    kpis = {
        'sales':     {'value': this_sales,     'prev': prev_sales,     'change': pct_change(this_sales, prev_sales)},
        'purchases': {'value': this_purchases, 'prev': prev_purchases, 'change': pct_change(this_purchases, prev_purchases)},
        'expenses':  {'value': this_expenses,  'prev': prev_expenses,  'change': pct_change(this_expenses, prev_expenses)},
        'profit':    {'value': this_profit,    'prev': prev_profit,    'change': pct_change(this_profit, prev_profit)},
    }

    # ── 12-month trend (revenue, purchases, expenses) ───────────────
    months_labels, monthly_sales, monthly_purchases, monthly_expenses, monthly_profit = [], [], [], [], []
    arabic_months = ['يناير','فبراير','مارس','أبريل','مايو','يونيو',
                     'يوليو','أغسطس','سبتمبر','أكتوبر','نوفمبر','ديسمبر']

    for i in range(11, -1, -1):
        # Build month start/end
        year  = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year  -= 1
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        m_start = date_type(year, month, 1)
        m_end   = date_type(year, month, last_day)

        s = sales_total({'invoice_date__gte': m_start, 'invoice_date__lte': m_end})
        p = purchase_total({'invoice_date__gte': m_start, 'invoice_date__lte': m_end})
        e = expense_total({'expense_date__gte': m_start, 'expense_date__lte': m_end})

        months_labels.append(f"{arabic_months[month-1]} {year}")
        monthly_sales.append(s)
        monthly_purchases.append(p)
        monthly_expenses.append(e)
        monthly_profit.append(round(s - p - e, 2))

    # ── Top 10 customers by revenue (this month) ────────────────────
    top_customers = (
        SaleInvoiceLine.objects
        .filter(tenant=tenant, invoice__status='confirmed', invoice__invoice_date__gte=first_this_month)
        .values('invoice__customer__name')
        .annotate(total=Sum('line_total'))
        .order_by('-total')[:10]
    )
    top_customers_list = [
        {'name': r['invoice__customer__name'] or 'عميل نقدي', 'total': float(r['total'])}
        for r in top_customers
    ]
    max_customer_total = max((c['total'] for c in top_customers_list), default=1)

    # ── Treasury balances ───────────────────────────────────────────
    from apps.treasury.models import Treasury
    treasuries = Treasury.objects.filter(tenant=tenant, is_active=True).values('name', 'current_balance')
    treasury_total = float(Treasury.objects.filter(tenant=tenant, is_active=True)
                           .aggregate(t=Sum('current_balance'))['t'] or 0)

    # ── Outstanding balances ────────────────────────────────────────
    # customer debt = sum of unpaid portions of confirmed credit/mixed sales invoices
    from apps.sales.models import SaleInvoice
    from django.db.models import F
    customer_debt = float(
        SaleInvoice.objects.filter(
            tenant=tenant,
            status__in=('confirmed', 'partially_returned'),
            payment_method__in=('credit', 'mixed'),
        ).aggregate(t=Sum(F('grand_total') - F('paid_amount')))['t'] or 0
    )
    # supplier debt — calculated per-supplier to handle HC vs local correctly
    from apps.purchases.models import SupplierLedger
    from apps.suppliers.models import Supplier as SupplierModel
    _hc_mode = getattr(tenant, 'hard_currency_mode', False)
    _hc_rate = Decimal(str(tenant.exchange_rate or 1)) if _hc_mode and tenant.exchange_rate else Decimal('1')
    _tenant_currency = (getattr(tenant, 'currency', '') or '').strip()
    supplier_debt = Decimal('0')
    for _sup in SupplierModel.objects.filter(tenant=tenant):
        _sup_cur = (_sup.currency or '').strip()
        _is_hc_sup = _hc_mode and bool(_sup_cur) and _sup_cur != _tenant_currency
        if _is_hc_sup:
            # balance in HC currency — use hc_amount field (same as payment view)
            _hc_bal = (
                SupplierLedger.objects.filter(tenant=tenant, supplier=_sup, hc_amount__isnull=False)
                .aggregate(s=Sum('hc_amount'))['s'] or Decimal('0')
            ) + (_sup.opening_balance or Decimal('0'))
            if _hc_bal > 0:
                supplier_debt += (_hc_bal * _hc_rate).quantize(Decimal('0.01'))
        else:
            _local_bal = (
                SupplierLedger.objects.filter(tenant=tenant, supplier=_sup)
                .aggregate(s=Sum('amount'))['s'] or Decimal('0')
            ) + (_sup.opening_balance or Decimal('0'))
            if _local_bal > 0:
                supplier_debt += _local_bal
    supplier_debt = float(supplier_debt)

    context = {
        'kpis': kpis,
        'this_month_label': f"{arabic_months[today.month-1]} {today.year}",
        'prev_month_label': f"{arabic_months[last_day_prev.month-1]} {last_day_prev.year}",
        'months_labels_json': json.dumps(months_labels),
        'monthly_sales_json': json.dumps(monthly_sales),
        'monthly_purchases_json': json.dumps(monthly_purchases),
        'monthly_expenses_json': json.dumps(monthly_expenses),
        'monthly_profit_json': json.dumps(monthly_profit),
        'top_customers': top_customers_list,
        'max_customer_total': max_customer_total,
        'treasuries': list(treasuries),
        'treasury_total': treasury_total,
        'customer_debt': customer_debt,
        'supplier_debt': supplier_debt,
    }
    return render(request, 'core/analytics.html', context)


# ============================================================
# MARKETING — Social Media Posts (Platform Admin Only)
# ============================================================

@login_required
def admin_marketing_posts(request):
    if not request.user.has_platform_perm('manage_marketing'):
        return redirect('core:no_permission')

    category_filter = request.GET.get('category', '')
    published_filter = request.GET.get('published', '')

    posts = SocialMediaPost.objects.select_related('created_by').all()
    if category_filter:
        posts = posts.filter(category=category_filter)
    if published_filter == 'published':
        posts = posts.filter(is_published=True)
    elif published_filter == 'unpublished':
        posts = posts.filter(is_published=False)

    total_count = SocialMediaPost.objects.count()
    published_count = SocialMediaPost.objects.filter(is_published=True).count()
    unpublished_count = total_count - published_count
    category_stats = [
        (key, label, SocialMediaPost.objects.filter(category=key).count())
        for key, label in SocialMediaPost.CATEGORY_CHOICES
    ]

    paginator = Paginator(posts, 12)
    page_obj = paginator.get_page(request.GET.get('page'))

    extra_qs_parts = []
    if category_filter:
        extra_qs_parts.append(f'category={category_filter}')
    if published_filter:
        extra_qs_parts.append(f'published={published_filter}')
    extra_qs = '&'.join(extra_qs_parts) + ('&' if extra_qs_parts else '')

    return render(request, 'core/admin_marketing_posts.html', {
        'posts': page_obj,
        'page_obj': page_obj,
        'extra_qs': extra_qs,
        'total_count': total_count,
        'published_count': published_count,
        'unpublished_count': unpublished_count,
        'category_stats': category_stats,
        'category_filter': category_filter,
        'published_filter': published_filter,
        'CATEGORY_CHOICES': SocialMediaPost.CATEGORY_CHOICES,
        'PUBLISH_CHANNEL_CHOICES': SocialMediaPost.PUBLISH_CHANNEL_CHOICES,
    })


@login_required
@require_POST
def admin_marketing_post_create(request):
    err = _superuser_required(request, 'manage_marketing')
    if err:
        return err

    category = request.POST.get('category', '').strip()
    content = request.POST.get('content', '').strip()

    valid_categories = dict(SocialMediaPost.CATEGORY_CHOICES)
    if category not in valid_categories:
        return JsonResponse({'success': False, 'message': 'تصنيف غير صالح'}, status=400)
    if not content:
        return JsonResponse({'success': False, 'message': 'محتوى المنشور مطلوب'}, status=400)

    post = SocialMediaPost.objects.create(
        category=category,
        content=content,
        is_ai_generated=request.POST.get('is_ai_generated') == '1',
        created_by=request.user,
    )

    return JsonResponse({'success': True, 'message': 'تم إنشاء المنشور بنجاح', 'post_id': post.pk})


@login_required
@require_POST
def admin_marketing_post_update(request, pk):
    err = _superuser_required(request, 'manage_marketing')
    if err:
        return err

    post = get_object_or_404(SocialMediaPost, pk=pk)

    category = request.POST.get('category', '').strip()
    content = request.POST.get('content', '').strip()

    valid_categories = dict(SocialMediaPost.CATEGORY_CHOICES)
    if category not in valid_categories:
        return JsonResponse({'success': False, 'message': 'تصنيف غير صالح'}, status=400)
    if not content:
        return JsonResponse({'success': False, 'message': 'محتوى المنشور مطلوب'}, status=400)

    post.category = category
    post.content = content
    post.save(update_fields=['category', 'content', 'updated_at'])

    return JsonResponse({'success': True, 'message': 'تم تحديث المنشور بنجاح'})


@login_required
@require_POST
def admin_marketing_post_delete(request, pk):
    err = _superuser_required(request, 'manage_marketing')
    if err:
        return err

    post = get_object_or_404(SocialMediaPost, pk=pk)
    post.delete()

    return JsonResponse({'success': True, 'message': 'تم حذف المنشور'})


@login_required
@require_POST
def admin_marketing_post_publish(request, pk):
    err = _superuser_required(request, 'manage_marketing')
    if err:
        return err

    post = get_object_or_404(SocialMediaPost, pk=pk)

    channel = request.POST.get('channel', '').strip()
    published_at = request.POST.get('published_at', '').strip()

    valid_channels = dict(SocialMediaPost.PUBLISH_CHANNEL_CHOICES)
    if channel not in valid_channels:
        return JsonResponse({'success': False, 'message': 'قناة غير صالحة'}, status=400)

    from datetime import date as date_cls
    if published_at:
        try:
            published_date = date_cls.fromisoformat(published_at)
        except ValueError:
            return JsonResponse({'success': False, 'message': 'تاريخ غير صالح'}, status=400)
    else:
        published_date = dj_timezone.localdate()

    post.is_published = True
    post.published_channel = channel
    post.published_at = published_date
    post.save(update_fields=['is_published', 'published_channel', 'published_at'])

    return JsonResponse({
        'success': True,
        'message': 'تم تحديد المنشور كمنشور',
        'channel_label': post.get_published_channel_display(),
        'published_at': post.published_at.strftime('%Y-%m-%d'),
    })


@login_required
@require_POST
def admin_marketing_post_unpublish(request, pk):
    err = _superuser_required(request, 'manage_marketing')
    if err:
        return err

    post = get_object_or_404(SocialMediaPost, pk=pk)
    post.is_published = False
    post.published_channel = ''
    post.published_at = None
    post.save(update_fields=['is_published', 'published_channel', 'published_at'])

    return JsonResponse({'success': True, 'message': 'تم إلغاء تحديد النشر'})


@login_required
@require_POST
def admin_marketing_post_generate(request):
    err = _superuser_required(request, 'manage_marketing')
    if err:
        return err

    category = request.POST.get('category', '').strip()
    topic_hint = request.POST.get('topic_hint', '').strip()

    valid_categories = dict(SocialMediaPost.CATEGORY_CHOICES)
    if category not in valid_categories:
        return JsonResponse({'success': False, 'message': 'تصنيف غير صالح'}, status=400)

    from apps.ai.services import generate_marketing_post, find_similar_post

    existing_posts = list(
        SocialMediaPost.objects.filter(category=category).values_list('content', flat=True)
    )

    content = generate_marketing_post(category, topic_hint, existing_posts=existing_posts)
    similar = find_similar_post(content, existing_posts)

    if similar:
        # One retry: give the model an even more explicit nudge before giving up on avoiding it.
        content = generate_marketing_post(
            category,
            topic_hint or 'اكتب بزاوية مختلفة تماماً عن المنشورات الموجودة',
            existing_posts=existing_posts,
        )
        similar = find_similar_post(content, existing_posts)

    return JsonResponse({
        'success': True,
        'content': content,
        'similar_warning': bool(similar),
        'similar_content': similar['content'] if similar else None,
    })
