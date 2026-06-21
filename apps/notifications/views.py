import json
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST, require_GET
from apps.accounts.decorators import require_permission

from .models import Notification
from .services import (
    generate_low_stock_notifications,
    generate_overdue_invoice_notifications,
    generate_rfq_expiry_notifications,
)

_GEN_THROTTLE = 900  # seconds between auto-generations per tenant


def _maybe_generate(tenant):
    """Run all generators at most once every 15 minutes per tenant."""
    key = f'notif_gen_{tenant.pk}'
    if cache.get(key):
        return
    cache.set(key, 1, _GEN_THROTTLE)
    generate_low_stock_notifications(tenant)
    generate_overdue_invoice_notifications(tenant)
    generate_rfq_expiry_notifications(tenant)


def _tenant(request):
    return getattr(request, 'tenant', None)


@login_required
@require_permission('view_notifications')
def notification_list(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    _maybe_generate(tenant)

    notifications = Notification.objects.filter(tenant=tenant).order_by('-created_at')[:100]
    unread_count  = Notification.objects.filter(tenant=tenant, is_read=False).count()

    return render(request, 'notifications/notification_list.html', {
        'notifications': notifications,
        'unread_count': unread_count,
    })


@login_required
def notification_api(request):
    """Returns unread count + recent notifications for the header bell."""
    if not (getattr(request.user, 'is_tenant_admin', False) or
            request.user.has_perm_key('view_notifications')):
        return JsonResponse({'unread': 0, 'items': [], 'no_perm': True})

    tenant = _tenant(request)
    if not tenant:
        return JsonResponse({'unread': 0, 'items': []})

    _maybe_generate(tenant)

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
@require_permission('view_notifications')
def notification_detail(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    notif = Notification.objects.filter(tenant=tenant, pk=pk).first()
    if not notif:
        from django.http import Http404
        raise Http404

    if not notif.is_read:
        notif.is_read = True
        notif.save(update_fields=['is_read'])

    return render(request, 'notifications/notification_detail.html', {
        'notif': notif,
    })


@login_required
@require_permission('view_notifications')
@require_POST
def mark_read_ajax(request, pk):
    tenant = _tenant(request)
    Notification.objects.filter(tenant=tenant, pk=pk).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
@require_permission('view_notifications')
@require_POST
def mark_all_read_ajax(request):
    tenant = _tenant(request)
    Notification.objects.filter(tenant=tenant, is_read=False).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
@require_permission('generate_notifications')
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


@login_required
@require_permission('view_ai_insights')
@require_GET
def ai_analyze_notification(request, pk):
    """
    GET /notifications/api/<pk>/ai-analyze/
    Returns AI enrichment for a single notification.
    Cached for 1 hour so repeated clicks don't cost API tokens.
    """
    tenant = _tenant(request)
    if not tenant:
        return JsonResponse({'error': 'no tenant'}, status=403)

    notif = Notification.objects.filter(tenant=tenant, pk=pk).first()
    if not notif:
        return JsonResponse({'error': 'not found'}, status=404)

    cache_key = f'ai_notif_{pk}'
    analysis = cache.get(cache_key)
    if not analysis:
        try:
            from apps.ai.services import enrich_notification
            analysis = enrich_notification(notif.notification_type, notif.message, tenant)
        except Exception:
            analysis = 'تعذّر إجراء التحليل الذكي في الوقت الحالي.'
        cache.set(cache_key, analysis, 3600)

    return JsonResponse({'analysis': analysis})
