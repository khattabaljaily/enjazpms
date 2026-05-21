import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Notification
from .services import (
    generate_low_stock_notifications,
    generate_overdue_invoice_notifications,
    generate_rfq_expiry_notifications,
)


def _tenant(request):
    return getattr(request, 'tenant', None)


@login_required
def notification_list(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    notifications = Notification.objects.filter(tenant=tenant).order_by('-created_at')[:100]
    unread_count  = Notification.objects.filter(tenant=tenant, is_read=False).count()

    return render(request, 'notifications/notification_list.html', {
        'notifications': notifications,
        'unread_count': unread_count,
    })


@login_required
def notification_api(request):
    """Returns unread count + recent notifications for the header bell."""
    tenant = _tenant(request)
    if not tenant:
        return JsonResponse({'unread': 0, 'items': []})

    unread = Notification.objects.filter(tenant=tenant, is_read=False).count()
    recent = Notification.objects.filter(tenant=tenant).order_by('-created_at')[:8]

    ICONS = {
        'low_stock':       'fa-triangle-exclamation text-warning',
        'overdue_invoice': 'fa-file-invoice-dollar text-danger',
        'rfq_expiry':      'fa-clock text-info',
        'transfer_done':   'fa-arrows-left-right text-success',
        'stocktake_done':  'fa-clipboard-check text-success',
        'general':         'fa-bell text-secondary',
    }

    items = []
    for n in recent:
        items.append({
            'id': n.id,
            'title': n.title,
            'message': n.message[:80],
            'is_read': n.is_read,
            'link': n.link,
            'priority': n.priority,
            'icon': ICONS.get(n.notification_type, 'fa-bell'),
            'time': n.created_at.strftime('%Y-%m-%d %H:%M'),
        })

    return JsonResponse({'unread': unread, 'items': items})


@login_required
@require_POST
def mark_read_ajax(request, pk):
    tenant = _tenant(request)
    Notification.objects.filter(tenant=tenant, pk=pk).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
@require_POST
def mark_all_read_ajax(request):
    tenant = _tenant(request)
    Notification.objects.filter(tenant=tenant, is_read=False).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
@require_POST
def generate_notifications_ajax(request):
    """Manually trigger notification generation (can be called from UI or cron)."""
    tenant = _tenant(request)
    if not tenant:
        return JsonResponse({'success': False}, status=400)

    low_stock = generate_low_stock_notifications(tenant)
    overdue   = generate_overdue_invoice_notifications(tenant)
    rfq_exp   = generate_rfq_expiry_notifications(tenant)

    return JsonResponse({
        'success': True,
        'created': {
            'low_stock': low_stock,
            'overdue_invoices': overdue,
            'rfq_expiry': rfq_exp,
        },
        'total': low_stock + overdue + rfq_exp,
    })
