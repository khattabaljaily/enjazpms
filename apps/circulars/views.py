from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import CircularRead
from .services import visible_circulars


@login_required
def circular_list(request):
    qs = visible_circulars(request).select_related('branch', 'actor')
    level = request.GET.get('level', '')
    if level in ('red', 'yellow'):
        qs = qs.filter(level=level)
    page = Paginator(qs, 30).get_page(request.GET.get('page'))
    read_ids = set(CircularRead.objects.filter(
        user=request.user, circular__in=[c.pk for c in page]).values_list('circular_id', flat=True))
    for c in page:
        c.is_read = c.pk in read_ids
    return render(request, 'circulars/circular_list.html', {'page': page, 'level': level})


@login_required
@require_POST
def mark_all_read(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return JsonResponse({'success': False}, status=400)
    unread = visible_circulars(request).exclude(reads__user=request.user)
    CircularRead.objects.bulk_create(
        [CircularRead(tenant=tenant, circular=c, user=request.user) for c in unread],
        ignore_conflicts=True,
    )
    return JsonResponse({'success': True})
