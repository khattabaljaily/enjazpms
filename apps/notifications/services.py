"""
Notification generation services.
Called from views or management commands.
"""
import pytz
from datetime import timedelta
from django.db.models import Q
from django.utils import timezone
from .models import Notification

_RESEND_DAYS = 7  # re-alert after this many days if condition still exists


def _already_notified(tenant, notification_type, link):
    """
    Skip creation if:
      - an unread notification already exists for this condition, OR
      - any notification (read or not) was created within the last _RESEND_DAYS.
    This prevents duplicates on server restart and respects the user's read action.
    """
    since = timezone.now() - timedelta(days=_RESEND_DAYS)
    return Notification.objects.filter(
        tenant=tenant,
        notification_type=notification_type,
        link=link,
    ).filter(
        Q(is_read=False) | Q(created_at__gte=since)
    ).exists()


def generate_low_stock_notifications(tenant):
    """Create notifications for items below min_quantity in any stock."""
    from apps.stocks.models import StockQuantity

    low_items = StockQuantity.objects.filter(
        tenant=tenant,
        stock__is_active=True,
    ).select_related('item', 'stock').exclude(item__item_type='service')

    count = 0
    for sq in low_items:
        threshold = sq.min_quantity or sq.item.min_quantity
        if threshold <= 0:
            continue
        if sq.quantity > threshold:
            continue

        if _already_notified(tenant, 'low_stock',
                             f'/stocks/quantities/?item={sq.item_id}&stock={sq.stock_id}'):
            continue

        Notification.objects.create(
            tenant=tenant,
            notification_type='low_stock',
            priority='high',
            title=f'مخزون منخفض: {sq.item.name}',
            message=(
                f'الكمية المتبقية من "{sq.item.name}" في {sq.stock.name} '
                f'هي {sq.quantity:.2f} وهي دون الحد الأدنى ({threshold:.2f}).'
            ),
            link=f'/stocks/quantities/?item={sq.item_id}&stock={sq.stock_id}',
        )
        count += 1

    return count


def generate_overdue_invoice_notifications(tenant):
    """Create notifications for sale invoices that are past their due date and unpaid."""
    from apps.sales.models import SaleInvoice
    from decimal import Decimal

    tenant_tz = pytz.timezone(tenant.timezone)
    today = timezone.localtime(timezone.now(), tenant_tz).date()
    overdue = SaleInvoice.objects.filter(
        tenant=tenant,
        status='confirmed',
        due_date__lt=today,
    ).exclude(grand_total__lte=Decimal('0'))

    count = 0
    for inv in overdue:
        if _already_notified(tenant, 'overdue_invoice', f'/sales/{inv.id}/'):
            continue

        Notification.objects.create(
            tenant=tenant,
            notification_type='overdue_invoice',
            priority='high',
            title=f'فاتورة متأخرة: {inv.invoice_number}',
            message=(
                f'الفاتورة {inv.invoice_number}'
                f'{" للعميل " + inv.customer.name if inv.customer else ""}'
                f' بمبلغ {inv.grand_total:,.2f} متأخرة منذ {inv.due_date}.'
            ),
            link=f'/sales/{inv.id}/',
        )
        count += 1

    return count


def generate_rfq_expiry_notifications(tenant):
    """Notify about RFQs expiring within 3 days."""
    from apps.purchases.models import PurchaseRFQ

    tenant_tz = pytz.timezone(tenant.timezone)
    today = timezone.localtime(timezone.now(), tenant_tz).date()
    from datetime import timedelta
    soon = today + timedelta(days=3)

    expiring = PurchaseRFQ.objects.filter(
        tenant=tenant,
        status__in=('draft', 'sent', 'received'),
        expiry_date__lte=soon,
        expiry_date__gte=today,
    )

    count = 0
    for rfq in expiring:
        if _already_notified(tenant, 'rfq_expiry', f'/purchases/rfq/{rfq.id}/'):
            continue

        days_left = (rfq.expiry_date - today).days
        Notification.objects.create(
            tenant=tenant,
            notification_type='rfq_expiry',
            priority='medium',
            title=f'طلب سعر قارب على الانتهاء: {rfq.rfq_number}',
            message=(
                f'طلب عرض الأسعار {rfq.rfq_number} '
                f'ينتهي خلال {days_left} يوم (في {rfq.expiry_date}).'
            ),
            link=f'/purchases/rfq/{rfq.id}/',
        )
        count += 1

    return count
