from django.db.models.signals import post_save, post_delete
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver

from apps.core.models import Tenant, TenantCapabilities


# ── Admin Notification helpers ─────────────────────────────────────────────

def _admin_notify(notification_type, title, message, link='', priority='medium', ref_key=''):
    try:
        from apps.core.models import AdminNotification
        AdminNotification.push(
            notification_type=notification_type,
            title=title, message=message,
            link=link, priority=priority, ref_key=ref_key,
        )
    except Exception:
        pass


@receiver(post_save, sender=Tenant)
def on_tenant_created(sender, instance, created, **kwargs):
    if not created:
        return
    _admin_notify(
        notification_type='new_tenant',
        title=f'مشترك جديد: {instance.name}',
        message=f'انضم مشترك جديد "{instance.name}" بالباقة {instance.get_subscription_plan_display()}.',
        link='/tenants/',
        priority='medium',
        ref_key=f'new_tenant_{instance.pk}',
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _log(tenant, user, action, description, model_name='', object_id=None,
         ip=None, ua='', metadata=None):
    """Write one ActivityLog row, swallowing all errors so logging never breaks the app."""
    from apps.core.models import tenant_deletion_in_progress
    if tenant_deletion_in_progress.get():
        return
    try:
        from apps.core.models import ActivityLog
        ActivityLog.objects.create(
            tenant=tenant,
            user=user,
            action=action,
            model_name=model_name,
            object_id=object_id,
            description=description,
            ip_address=ip,
            user_agent=ua or '',
            metadata=metadata or {},
        )
    except Exception:
        pass


def _get_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')


# ── Login / Logout ─────────────────────────────────────────────────────────

@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    tenant = getattr(user, 'tenant', None)
    if not tenant:
        return
    _log(
        tenant=tenant, user=user, action='login',
        description=f'تسجيل دخول: {user.get_full_name() or user.username}',
        ip=_get_ip(request),
        ua=request.META.get('HTTP_USER_AGENT', ''),
    )


@receiver(user_logged_out)
def on_user_logged_out(sender, request, user, **kwargs):
    if not user:
        return
    tenant = getattr(user, 'tenant', None)
    if not tenant:
        return
    _log(
        tenant=tenant, user=user, action='logout',
        description=f'تسجيل خروج: {user.get_full_name() or user.username}',
        ip=_get_ip(request),
    )


# ── Model-level signals for key documents ─────────────────────────────────

def _make_doc_handler(model_label, name_ar):
    """Factory: returns post_save + post_delete receivers for a document model."""

    def on_save(sender, instance, created, **kwargs):
        tenant = getattr(instance, 'tenant', None)
        user = getattr(instance, 'created_by' if created else 'updated_by', None)
        if not tenant:
            return
        action = 'create' if created else 'update'
        ref = getattr(instance, 'invoice_number', None) or getattr(instance, 'number', None) or f'#{instance.pk}'
        verb = 'إنشاء' if created else 'تعديل'
        _log(tenant=tenant, user=user, action=action,
             model_name=model_label, object_id=instance.pk,
             description=f'{verb} {name_ar}: {ref}')

    def on_delete(sender, instance, **kwargs):
        tenant = getattr(instance, 'tenant', None)
        try:
            user = getattr(instance, 'updated_by', None) or getattr(instance, 'created_by', None)
        except Exception:
            user = None
        if not tenant:
            return
        ref = getattr(instance, 'invoice_number', None) or f'#{instance.pk}'
        _log(tenant=tenant, user=user, action='delete',
             model_name=model_label, object_id=instance.pk,
             description=f'حذف {name_ar}: {ref}')

    return on_save, on_delete


@receiver(post_save, sender=Tenant)
def create_tenant_defaults(sender, instance, created, **kwargs):
    if not created:
        return

    from apps.stocks.models import Stock
    from apps.treasury.models import Treasury

    # Default stock (non-deletable system stock)
    Stock.objects.get_or_create(
        tenant=instance,
        is_system_default=True,
        defaults={
            'name': 'المخزن الرئيسي',
            'code': 'WH-MAIN',
            'stock_type': 'main',
            'is_default': True,
            'is_active': True,
        },
    )

    # Create additional stocks if max_stocks > 1
    if instance.max_stocks > 1:
        for i in range(2, instance.max_stocks + 1):
            Stock.objects.get_or_create(
                tenant=instance,
                code=f'WH-{i:03d}',
                defaults={
                    'name': f'مخزن {i}',
                    'stock_type': 'main',
                    'is_active': True,
                },
            )

    # Ensure at least one default stock flag exists
    if not Stock.objects.for_tenant(instance).filter(is_default=True).exists():
        fallback_stock = Stock.objects.for_tenant(instance).order_by('id').first()
        if fallback_stock:
            fallback_stock.is_default = True
            fallback_stock.save(update_fields=['is_default', 'updated_at'])

    # Default treasury (non-deletable system treasury)
    Treasury.objects.get_or_create(
        tenant=instance,
        is_system_default=True,
        defaults={
            'name': 'الخزينة الرئيسية',
            'code': 'TR-MAIN',
            'is_default': True,
            'is_active': True,
            'current_balance': 0,
        },
    )

    if not Treasury.objects.for_tenant(instance).filter(is_default=True).exists():
        fallback_treasury = Treasury.objects.for_tenant(instance).order_by('id').first()
        if fallback_treasury:
            fallback_treasury.is_default = True
            fallback_treasury.save(update_fields=['is_default', 'updated_at'])

    # Capabilities derived from business type
    caps = TenantCapabilities.from_business_type(instance)
    caps.save()

    # Default online store (disabled until tenant activates it)
    from apps.store.models import StoreSettings, DEFAULT_HOURS
    StoreSettings.objects.get_or_create(
        tenant=instance,
        defaults={
            'display_name':  instance.name,
            'is_enabled':    False,
            'working_hours': DEFAULT_HOURS.copy(),
        },
    )


# ── Connect document signals ───────────────────────────────────────────────

def _connect_doc(app_label, model_name, name_ar):
    """Lazy-connect so we don't import models at module load time."""
    from django.apps import apps as django_apps

    try:
        Model = django_apps.get_model(app_label, model_name)
    except LookupError:
        return

    on_save, on_delete = _make_doc_handler(model_name, name_ar)
    post_save.connect(on_save, sender=Model, weak=False,
                      dispatch_uid=f'actlog_{app_label}_{model_name}_save')
    post_delete.connect(on_delete, sender=Model, weak=False,
                        dispatch_uid=f'actlog_{app_label}_{model_name}_delete')


def _on_ticket_created(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        from django.urls import reverse
        link = reverse('core:admin_support')
    except Exception:
        link = '/support/'
    tenant_name = instance.tenant.name if instance.tenant else ''
    _admin_notify(
        notification_type='new_ticket',
        title=f'تذكرة دعم جديدة #{instance.pk}',
        message=f'{tenant_name}: {instance.subject}',
        link=link,
        priority='high',
        ref_key=f'new_ticket_{instance.pk}',
    )


# Called once from CoreConfig.ready() — safe to import models here
def connect_activity_signals():
    _connect_doc('sales',     'SaleInvoice',      'فاتورة مبيعات')
    _connect_doc('sales',     'SaleReturn',        'مرتجع مبيعات')
    _connect_doc('sales',     'SaleQuote',         'عرض سعر')
    _connect_doc('purchases', 'PurchaseInvoice',   'فاتورة مشتريات')
    _connect_doc('purchases', 'PurchaseReturn',    'مرتجع مشتريات')
    _connect_doc('purchases', 'PurchaseRFQ',       'طلب عرض سعر')
    _connect_doc('stocks',    'StockTransfer',     'تحويل مخزون')
    _connect_doc('stocks',    'StockTake',         'جرد مخزون')
    _connect_doc('items',     'Item',              'صنف')
    _connect_doc('customers', 'Customer',          'عميل')
    _connect_doc('suppliers', 'Supplier',          'مورد')
    _connect_doc('expenses',  'Expense',           'مصروف')

    # Support ticket → admin notification
    from apps.core.models import SupportTicket
    post_save.connect(_on_ticket_created, sender=SupportTicket, weak=False,
                      dispatch_uid='admin_notif_new_ticket')
