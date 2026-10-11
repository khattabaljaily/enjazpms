"""
طلبات الشراء من الفروع (النمط المركزي): الفرع يطلب أصنافاً وكميات، والإدارة تراجع وتنفّذ بشحنة
أو بأمر شراء مركزي. الشاشات نفسها للطرفين بنطاقين:
  - request.req_scope == 'branch': طلبات فرع المستخدم فقط.
  - request.req_scope == 'ho': كل طلبات المشترك (الإدارة).
"""
import json
from datetime import date
from decimal import Decimal
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.activity_service import log_activity
from apps.accounts.decorators import _deny, branch_scope_exempt
from apps.stocks.models import Stock, StockQuantity

from . import services
from .models import PurchaseRequisition

def requisition_access(branch_key=None, head_office_key=None):
    """
    يسمح لمستخدم الفرع بـ branch_key ولمستخدم الإدارة (بلا فرع) بـ head_office_key، في النمط المركزي فقط.
    يمرّر النطاق للـ view عبر request.req_scope. الترتيب: الصلاحية أولاً (403)، ثم بوابة النمط (404).
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            user = request.user
            holds_branch = bool(branch_key and user.has_perm_key(branch_key))
            holds_ho = bool(head_office_key and user.has_perm_key(head_office_key))
            if not (holds_branch or holds_ho):
                return _deny(request)
            tenant = getattr(request, 'tenant', None)
            if tenant is None or not tenant.is_central_purchasing():
                raise Http404
            if getattr(request, 'branch', None) is not None:
                if not holds_branch:
                    return _deny(request)  # مستخدم فرع بصلاحية إدارة فقط: مرفوض
                request.req_scope = 'branch'
            else:
                if not holds_ho:
                    raise Http404  # مستخدم بلا فرع بصلاحية فرع فقط: لا فرع ينسب إليه الطلب
                request.req_scope = 'ho'
            return view_func(request, *args, **kwargs)
        wrapper._required_permission_keys = tuple(k for k in (head_office_key, branch_key) if k)
        wrapper._permission_check_mode = 'any'
        return wrapper
    return decorator


def _err(message, status=400, **extra):
    return JsonResponse({'success': False, 'message': message, **extra}, status=status,
                        json_dumps_params={'ensure_ascii': False})


def _scoped(request):
    qs = PurchaseRequisition.objects.filter(tenant=request.tenant)
    if request.req_scope == 'branch':
        qs = qs.filter(branch=request.branch)
    else:
        qs = qs.exclude(status='draft')  # مسودات الفروع خاصة بها ولا تراها الإدارة
    return qs


def _get_or_404(request, pk):
    return get_object_or_404(_scoped(request).select_related('branch', 'stock', 'reviewed_by'), pk=pk)


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError('تاريخ غير صالح.')


# ──────────────────────────────────────────────────────────────
# القائمة
# ──────────────────────────────────────────────────────────────

@login_required
@requisition_access('view_purchase_requisitions', 'view_branch_requisitions')
def requisition_list(request):
    qs = (_scoped(request).select_related('branch', 'stock').annotate(line_count=Count('lines'))
          .order_by('-request_date', '-id'))
    status = request.GET.get('status', '').strip()
    branch_id = request.GET.get('branch', '').strip()
    search = request.GET.get('q', '').strip()
    base = qs
    if status in dict(PurchaseRequisition.STATUS_CHOICES):
        qs = qs.filter(status=status)
    if request.req_scope == 'ho' and branch_id.isdigit():
        qs = qs.filter(branch_id=branch_id)
    if search:
        qs = qs.filter(Q(number__icontains=search) | Q(branch__name__icontains=search))
    stats = {
        'total': base.count(),
        'submitted': base.filter(status='submitted').count(),
        'approved': base.filter(status='approved').count(),
        'fulfilled': base.filter(status='fulfilled').count(),
    }
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    branches = []
    if request.req_scope == 'ho':
        from apps.core.models import Branch
        branches = Branch.objects.filter(tenant=request.tenant, is_active=True).order_by('name')
    return render(request, 'requisitions/requisition_list.html', {
        'page': page, 'stats': stats, 'status_choices': PurchaseRequisition.STATUS_CHOICES,
        'branches': branches, 'filters': {
            'status': status, 'branch': branch_id, 'q': search},
        'scope': request.req_scope,
    })


# ──────────────────────────────────────────────────────────────
# إنشاء / تعديل (الفرع)
# ──────────────────────────────────────────────────────────────

def _branch_stocks(request):
    return Stock.objects.for_tenant(request.tenant).for_branch(request.branch).filter(
        is_active=True, is_central=False).order_by('name')


def _requisition_form(request, requisition=None):
    tenant = request.tenant
    stocks = _branch_stocks(request)
    if request.method == 'POST':
        try:
            data = json.loads(request.body or '{}')
        except (json.JSONDecodeError, ValueError):
            return _err('بيانات غير صالحة')
        try:
            stock = stocks.filter(pk=int(data.get('stock_id') or 0)).first()
            from apps.items.models import Item
            lines = []
            for ln in data.get('lines') or []:
                item = Item.objects.for_tenant(tenant).filter(pk=int(ln.get('item_id') or 0), is_active=True).first()
                if item is None:
                    raise ValueError('أحد الأصناف غير موجود.')
                lines.append({'item': item, 'quantity': ln.get('quantity'), 'notes': ln.get('notes', '')})
            with transaction.atomic():
                saved = services.save_requisition(
                    tenant, request.branch, request.user, stock=stock,
                    request_date=_parse_date(data.get('request_date')) or timezone.localdate(),
                    needed_by=_parse_date(data.get('needed_by')), priority=data.get('priority', 'normal'),
                    notes=data.get('notes', ''), lines=lines, requisition=requisition)
                if data.get('action') == 'submit':
                    if not request.user.has_perm_key('add_purchase_requisitions'):
                        raise ValueError('ليس لديك صلاحية إرسال الطلب.')
                    services.submit_requisition(saved, request.user)
        except (ValueError, TypeError) as exc:
            return _err(str(exc))
        log_activity(request, 'طلب شراء من فرع',
                     f'{saved.number}\nالفرع: {saved.branch.name}\n'
                     f'{"إرسال للإدارة" if data.get("action") == "submit" else "حفظ كمسودة"}', 'create')
        return JsonResponse({'success': True, 'id': saved.pk,
                             'redirect': reverse('requisitions:detail', args=[saved.pk])})

    initial_lines = []
    if requisition is not None:
        initial_lines = [{
            'id': ln.item_id, 'name': ln.item.name, 'sku': ln.item.sku or '', 'unit': ln.item.base_unit_name,
            'quantity': float(ln.requested_quantity), 'notes': ln.notes,
        } for ln in requisition.lines.select_related('item')]
    return render(request, 'requisitions/requisition_form.html', {
        'requisition': requisition, 'stocks': stocks, 'today': str(timezone.localdate()),
        'initial_lines': initial_lines,
    })


@login_required
@requisition_access(branch_key='add_purchase_requisitions')
def requisition_create(request):
    return _requisition_form(request)


@login_required
@requisition_access(branch_key='add_purchase_requisitions')
def requisition_edit(request, pk):
    requisition = _get_or_404(request, pk)
    if requisition.status != 'draft':
        raise Http404
    return _requisition_form(request, requisition)


@login_required
@requisition_access(branch_key='add_purchase_requisitions')
@branch_scope_exempt('المخزن المقروء رصيده يُجلب من _branch_stocks() المقيَّد بفرع المستخدم؛ والأصناف كتالوج مشترك')
def requisition_items_api(request):
    """أصناف قابلة للطلب مع رصيدها الحالي في مخزن الفرع المختار (ليُقدّر الفرع حاجته)."""
    from apps.items.models import Item
    tenant = request.tenant
    stock = _branch_stocks(request).filter(pk=request.GET.get('stock_id') or 0).first()
    search = request.GET.get('q', '').strip()
    qs = Item.objects.for_tenant(tenant).filter(is_active=True, item_type__in=['product', 'material'])
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search))
    items = list(qs.order_by('name')[:60])
    qty = {}
    if stock and items:
        for sq in StockQuantity.objects.filter(tenant=tenant, stock=stock, item__in=items):
            qty[sq.item_id] = float((sq.quantity or 0) - (sq.reserved_quantity or 0))
    return JsonResponse({'items': [
        {'id': i.id, 'name': i.name, 'sku': i.sku or '', 'unit': i.base_unit_name, 'stock_qty': qty.get(i.id, 0)}
        for i in items]}, json_dumps_params={'ensure_ascii': False})


# ──────────────────────────────────────────────────────────────
# التفاصيل والإجراءات
# ──────────────────────────────────────────────────────────────

@login_required
@requisition_access('view_purchase_requisitions', 'view_branch_requisitions')
def requisition_detail(request, pk):
    requisition = _get_or_404(request, pk)
    user = request.user
    lines = list(requisition.lines.select_related('item'))
    for ln in lines:
        ln.fulfilled = ln.fulfilled_quantity
        ln.remaining = ln.remaining_quantity
    ho = request.req_scope == 'ho'
    has_remaining = requisition.status == 'approved' and any(ln.remaining > 0 for ln in lines)
    shipments = list(requisition.shipments.exclude(status='cancelled').select_related('to_stock').order_by('id'))
    context = {
        'requisition': requisition, 'lines': lines, 'scope': request.req_scope,
        'shipments': shipments,
        'invoices': list(requisition.purchase_invoices.exclude(status='cancelled').order_by('id')) if ho else [],
        'can_edit': not ho and requisition.status == 'draft' and user.has_perm_key('add_purchase_requisitions'),
        'can_submit': not ho and requisition.status == 'draft' and user.has_perm_key('add_purchase_requisitions'),
        'can_cancel': (not ho and requisition.status in ('draft', 'submitted')
                       and user.has_perm_key('cancel_purchase_requisitions'))
                      or (ho and requisition.status in ('submitted', 'approved')
                          and user.has_perm_key('review_branch_requisitions')
                          and not (requisition.status == 'approved' and shipments)),
        'can_review': ho and requisition.status == 'submitted' and user.has_perm_key('review_branch_requisitions'),
        'can_ship': ho and has_remaining and user.has_perm_key('add_central_shipments'),
        'can_buy': ho and has_remaining and user.has_perm_key('add_purchases'),
    }
    return render(request, 'requisitions/requisition_detail.html', context)


@login_required
@requisition_access(branch_key='add_purchase_requisitions')
@require_POST
def requisition_submit(request, pk):
    requisition = _get_or_404(request, pk)
    try:
        services.submit_requisition(requisition, request.user)
    except ValueError as exc:
        return _err(str(exc))
    log_activity(request, 'إرسال طلب شراء للإدارة', requisition.number, 'create')
    return JsonResponse({'success': True, 'message': 'تم إرسال الطلب للإدارة'})


@login_required
@requisition_access('cancel_purchase_requisitions', 'review_branch_requisitions')
@require_POST
def requisition_cancel(request, pk):
    requisition = _get_or_404(request, pk)
    try:
        services.cancel_requisition(requisition, request.user, by_head_office=request.req_scope == 'ho')
    except ValueError as exc:
        return _err(str(exc))
    log_activity(request, 'إلغاء طلب شراء', requisition.number, 'delete')
    return JsonResponse({'success': True, 'message': 'تم إلغاء الطلب'})


@login_required
@requisition_access(head_office_key='review_branch_requisitions')
@require_POST
def requisition_review(request, pk):
    requisition = _get_or_404(request, pk)
    try:
        body = json.loads(request.body or '{}')
    except (json.JSONDecodeError, ValueError):
        return _err('بيانات غير صالحة')
    decisions = {}
    for row in body.get('lines') or []:
        try:
            decisions[int(row.get('line_id'))] = row.get('approved_quantity')
        except (TypeError, ValueError):
            return _err('بيانات البنود غير صالحة')
    try:
        reviewed = services.review_requisition(
            requisition, request.user, decisions, notes=body.get('notes', ''),
            reject_all=body.get('action') == 'reject')
    except ValueError as exc:
        return _err(str(exc))
    log_activity(request, 'مراجعة طلب شراء من فرع',
                 f'{reviewed.number}\nالنتيجة: {reviewed.get_status_display()}', 'update')
    return JsonResponse({'success': True, 'status': reviewed.status,
                         'message': 'تم اعتماد الطلب' if reviewed.status == 'approved' else 'تم رفض الطلب'})
