"""
تحويلات الفروع: القسم الوحيد للعلاقة المالية بين الإدارة والفروع (نقداً أو بنكياً، بنفس العملة).
  - الإدارة (بلا فرع): ترسل إلى أي فرع، وتؤكد استلام ما يرسله الفرع.
  - الفرع: يرسل إلى الإدارة فقط، ويؤكد استلام ما ترسله الإدارة إليه.
"""
import json
from datetime import date
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.activity_service import log_activity
from apps.accounts.decorators import _deny
from apps.bank_accounts.models import BankAccount
from apps.core.models import Branch
from apps.treasury.models import Treasury

from . import services
from .models import BranchTransfer


def transfer_access(permission_key):
    """مفتاح صلاحية + نسخة المؤسسات فقط؛ يمرّر النطاق عبر request.bt_scope ('ho' أو 'branch')."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            if not request.user.has_perm_key(permission_key):
                return _deny(request)
            tenant = getattr(request, 'tenant', None)
            if tenant is None or not tenant.is_enterprise():
                raise Http404
            request.bt_scope = 'branch' if getattr(request, 'branch', None) is not None else 'ho'
            return view_func(request, *args, **kwargs)
        wrapper._required_permission_keys = (permission_key,)
        wrapper._permission_check_mode = 'all'
        return wrapper
    return decorator


def _err(message, status=400, **extra):
    return JsonResponse({'success': False, 'message': message, **extra}, status=status,
                        json_dumps_params={'ensure_ascii': False})


def _scoped(request):
    qs = BranchTransfer.objects.filter(tenant=request.tenant)
    if request.bt_scope == 'branch':
        qs = qs.filter(branch=request.branch)
    return qs


def _get_or_404(request, pk):
    return get_object_or_404(
        _scoped(request).select_related('branch', 'from_treasury', 'from_bank', 'to_treasury', 'to_bank',
                                        'received_by', 'created_by', 'cancelled_by'), pk=pk)


def _can_receive(request, transfer):
    if transfer.status != 'in_transit' or not request.user.has_perm_key('receive_branch_transfers'):
        return False
    if transfer.direction == 'to_branch':
        return request.bt_scope == 'branch' and transfer.branch_id == request.branch.pk
    return request.bt_scope == 'ho'


def _can_cancel(request, transfer):
    if transfer.status != 'in_transit' or not request.user.has_perm_key('cancel_branch_transfers'):
        return False
    if transfer.direction == 'to_branch':
        return request.bt_scope == 'ho'
    return request.bt_scope == 'branch' and transfer.branch_id == request.branch.pk


# ──────────────────────────────────────────────────────────────
# القائمة
# ──────────────────────────────────────────────────────────────

@login_required
@transfer_access('view_branch_transfers')
def transfer_list(request):
    qs = _scoped(request).select_related('branch', 'from_treasury', 'from_bank', 'to_treasury', 'to_bank')
    base = qs
    status = request.GET.get('status', '').strip()
    direction = request.GET.get('direction', '').strip()
    branch_id = request.GET.get('branch', '').strip()
    search = request.GET.get('q', '').strip()
    if status in dict(BranchTransfer.STATUS_CHOICES):
        qs = qs.filter(status=status)
    if direction in dict(BranchTransfer.DIRECTION_CHOICES):
        qs = qs.filter(direction=direction)
    if request.bt_scope == 'ho' and branch_id.isdigit():
        qs = qs.filter(branch_id=branch_id)
    if search:
        qs = qs.filter(Q(number__icontains=search) | Q(branch__name__icontains=search))
    qs = qs.order_by('-transfer_date', '-id')
    # بانتظار تأكيد هذا الطرف (للتنبيه في أعلى الصفحة).
    awaiting = base.filter(status='in_transit', direction='to_branch' if request.bt_scope == 'branch' else 'to_head_office')
    stats = {
        'total': base.count(),
        'in_transit': base.filter(status='in_transit').count(),
        'awaiting_me': awaiting.count(),
        'with_difference': base.filter(has_difference=True).count(),
    }
    branches = Branch.objects.filter(tenant=request.tenant, is_active=True).order_by('name') \
        if request.bt_scope == 'ho' else []
    return render(request, 'branch_transfers/transfer_list.html', {
        'page': Paginator(qs, 25).get_page(request.GET.get('page')), 'stats': stats, 'scope': request.bt_scope,
        'status_choices': BranchTransfer.STATUS_CHOICES, 'direction_choices': BranchTransfer.DIRECTION_CHOICES,
        'branches': branches, 'filters': {'status': status, 'direction': direction, 'branch': branch_id, 'q': search},
        'can_add': request.user.has_perm_key('add_branch_transfers'),
    })


# ──────────────────────────────────────────────────────────────
# الإنشاء (إرسال)
# ──────────────────────────────────────────────────────────────

def _account_rows(tenant, kind, qs):
    return [{'kind': kind, 'id': a.pk, 'name': a.name, 'currency': services.currency_of(tenant, a),
             'branch_id': a.branch_id, 'is_head_office': a.is_head_office} for a in qs]


def _form_data(request):
    """
    حسابات الإرسال والاستلام حسب الطرف المُرسِل (والخادم يعيد التحقق منها عند الحفظ):
    الإدارة: تُرسل من خزائن/حسابات الإدارة إلى خزائن/حسابات الفروع. الفرع: من فرعه إلى الإدارة.
    """
    tenant = request.tenant
    treasuries = Treasury.objects.for_tenant(tenant).filter(is_active=True)
    banks = BankAccount.objects.for_tenant(tenant).filter(is_active=True)
    if request.bt_scope == 'ho':
        sources = (_account_rows(tenant, 'cash', treasuries.filter(is_head_office=True).order_by('name'))
                   + _account_rows(tenant, 'bank', banks.filter(is_head_office=True).order_by('name')))
        dests = (_account_rows(tenant, 'cash', treasuries.filter(is_head_office=False, branch__isnull=False).order_by('name'))
                 + _account_rows(tenant, 'bank', banks.filter(is_head_office=False, branch__isnull=False).order_by('name')))
        branches = [{'id': b.pk, 'name': b.name} for b in Branch.objects.filter(tenant=tenant, is_active=True).order_by('name')]
        direction = 'to_branch'
    else:
        own = request.branch
        sources = (_account_rows(tenant, 'cash', treasuries.filter(branch=own, is_head_office=False).order_by('name'))
                   + _account_rows(tenant, 'bank', banks.filter(branch=own, is_head_office=False).order_by('name')))
        dests = (_account_rows(tenant, 'cash', treasuries.filter(is_head_office=True).order_by('name'))
                 + _account_rows(tenant, 'bank', banks.filter(is_head_office=True).order_by('name')))
        branches = [{'id': own.pk, 'name': own.name}]
        direction = 'to_head_office'
    return {'sources': sources, 'destinations': dests, 'branches': branches, 'direction': direction,
            'branch_fixed': request.bt_scope == 'branch', 'local_currency': tenant.currency or ''}


def _resolve_account(tenant, kind, pk):
    model = Treasury if kind == 'cash' else BankAccount
    try:
        return model.objects.for_tenant(tenant).get(pk=int(pk))
    except (model.DoesNotExist, TypeError, ValueError):
        return None


@login_required
@transfer_access('add_branch_transfers')
def transfer_create(request):
    tenant = request.tenant
    if request.method == 'POST':
        try:
            data = json.loads(request.body or '{}')
        except (json.JSONDecodeError, ValueError):
            return _err('بيانات غير صالحة')
        direction = 'to_branch' if request.bt_scope == 'ho' else 'to_head_office'
        if request.bt_scope == 'branch':
            branch = request.branch
        else:
            branch = Branch.objects.filter(tenant=tenant, pk=data.get('branch_id') or 0).first()
        source_kind, dest_kind = data.get('source_kind'), data.get('dest_kind')
        source = _resolve_account(tenant, source_kind, data.get('source_id'))
        dest = _resolve_account(tenant, dest_kind, data.get('dest_id'))
        try:
            transfer_date = date.fromisoformat(str(data.get('transfer_date') or timezone.localdate())[:10])
            transfer = services.create_transfer(
                tenant, request.user, direction=direction, branch=branch, source_kind=source_kind, source=source,
                dest_kind=dest_kind, dest=dest, amount=data.get('amount'), transfer_date=transfer_date,
                notes=str(data.get('notes') or '').strip())
        except ValueError as exc:
            return _err(str(exc))
        log_activity(request, 'إرسال تحويل فروع',
                     f'{transfer.number}\n{transfer.get_direction_display()} — {transfer.branch.name}\n'
                     f'{transfer.amount} {transfer.currency}', 'create')
        return JsonResponse({'success': True, 'id': transfer.pk, 'message': 'تم إرسال التحويل',
                             'redirect': reverse('branch_transfers:detail', args=[transfer.pk])})
    return render(request, 'branch_transfers/transfer_form.html', {
        'form_data': _form_data(request), 'today': str(timezone.localdate()), 'scope': request.bt_scope})


# ──────────────────────────────────────────────────────────────
# التفاصيل والإجراءات
# ──────────────────────────────────────────────────────────────

@login_required
@transfer_access('view_branch_transfers')
def transfer_detail(request, pk):
    transfer = _get_or_404(request, pk)
    return render(request, 'branch_transfers/transfer_detail.html', {
        'transfer': transfer, 'scope': request.bt_scope,
        'can_receive': _can_receive(request, transfer), 'can_cancel': _can_cancel(request, transfer)})


@login_required
@transfer_access('receive_branch_transfers')
@require_POST
def transfer_receive(request, pk):
    transfer = _get_or_404(request, pk)
    if transfer.status != 'in_transit':
        return _err('يؤكد استلام التحويلات التي في الطريق فقط.')
    if not _can_receive(request, transfer):
        return _err('لا يمكنك تأكيد استلام هذا التحويل.', 403)
    try:
        body = json.loads(request.body or '{}')
    except (json.JSONDecodeError, ValueError):
        return _err('بيانات غير صالحة')
    try:
        services.receive_transfer(transfer, body.get('received_amount', transfer.amount), request.user)
    except ValueError as exc:
        return _err(str(exc))
    log_activity(request, 'تأكيد استلام تحويل فروع', transfer.number, 'update')
    return JsonResponse({'success': True, 'message': 'تم تأكيد الاستلام'})


@login_required
@transfer_access('cancel_branch_transfers')
@require_POST
def transfer_cancel(request, pk):
    transfer = _get_or_404(request, pk)
    if transfer.status != 'in_transit':
        return _err('لا يمكن إلغاء تحويل تم استلامه أو ألغي من قبل.')
    if not _can_cancel(request, transfer):
        return _err('لا يمكنك إلغاء هذا التحويل.', 403)
    try:
        services.cancel_transfer(transfer, request.user)
    except ValueError as exc:
        return _err(str(exc))
    log_activity(request, 'إلغاء تحويل فروع', transfer.number, 'delete')
    return JsonResponse({'success': True, 'message': 'تم إلغاء التحويل وعاد المبلغ إلى المصدر'})
