"""
شحنات المخزن المركزي (النمط الهجين): الإدارة تنشئ وترسل، والفرع المستلِم يؤكد الكمية الفعلية.

النطاقان (require_scoped_permission):
  - الإدارة (request.central_scope=True): كل شحنات المشترك، وتنشئ/ترسل/تلغي.
  - الفرع: الشحنات المرسَلة إلى فرعه فقط (لا المسودات)، ويؤكد استلامها.
"""
import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.activity_service import log_activity
from apps.accounts.decorators import require_any_permission, require_permission, require_scoped_permission
from apps.core.utils import is_central_request

from .models import Shipment, Stock, StockQuantity
from .shipment_services import cancel_shipment, create_shipment, receive_shipment, send_shipment

STATUS_LABELS = {'draft': 'مسودة', 'in_transit': 'في الطريق', 'received': 'تم الاستلام', 'cancelled': 'ملغية'}
STATUS_COLORS = {'draft': 'secondary', 'in_transit': 'warning', 'received': 'success', 'cancelled': 'danger'}
DIRECTION_LABELS = {'to_branch': 'توزيع', 'to_central': 'مرتجع'}


def _tenant(request):
    return getattr(request, 'tenant', None)


def _err(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status, json_dumps_params={'ensure_ascii': False})


def _shipments(request, tenant):
    """شحنات نطاق الطلب: الإدارة كلها؛ الفرع شحنات فرعه المرسَلة فقط."""
    qs = Shipment.objects.for_tenant(tenant)
    if is_central_request(request):
        return qs
    branch = getattr(request, 'branch', None)
    if branch is None:
        return qs.none()
    return qs.filter(branch=branch, sent_at__isnull=False).exclude(status='draft')


def _central_only(request):
    """إنشاء/إرسال/إلغاء الشحنات للمستخدم المركزي في النمط الهجين فقط."""
    tenant = _tenant(request)
    if not tenant or not tenant.is_hybrid_purchasing() or getattr(request, 'branch', None) is not None:
        raise Http404


def _notify(shipment, *, to_branch, title, message):
    from apps.notifications.models import Notification
    Notification.objects.create(
        tenant=shipment.tenant, branch=shipment.branch if to_branch else None,
        notification_type='transfer_done', title=title, message=message,
        link=reverse('stocks:shipment_detail', args=[shipment.pk]), priority='medium')


# ──────────────────────────────────────────────────────────────
# القائمة
# ──────────────────────────────────────────────────────────────

@login_required
@require_scoped_permission('view_incoming_shipments', 'view_central_shipments')
def shipment_list(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    qs = _shipments(request, tenant)
    stats = {
        'total': qs.count(),
        'in_transit': qs.filter(status='in_transit').count(),
        'received': qs.filter(status='received').count(),
        'with_difference': qs.filter(has_difference=True).count(),
    }
    central = is_central_request(request)
    return render(request, 'stocks/shipment_list.html', {
        'stats': stats, 'central_scope': central,
        'can_create_return': (not central) and request.user.has_perm_key('create_central_returns')
        and getattr(request, 'branch', None) is not None,
    })


@login_required
@require_scoped_permission('view_incoming_shipments', 'view_central_shipments')
def shipment_table_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')
    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status_f = request.GET.get('status', '').strip()
    direction_f = request.GET.get('direction', '').strip()

    qs = _shipments(request, tenant).select_related('from_stock', 'to_stock', 'branch')
    total = qs.count()
    if status_f:
        qs = qs.filter(status=status_f)
    if direction_f in ('to_branch', 'to_central'):
        qs = qs.filter(direction=direction_f)
    if search:
        qs = qs.filter(Q(shipment_number__icontains=search) | Q(branch__name__icontains=search)
                       | Q(to_stock__name__icontains=search) | Q(notes__icontains=search))
    filtered = qs.count()
    rows = []
    for sh in qs[start:start + length]:
        rows.append({
            'id': sh.id,
            'shipment_number': sh.shipment_number,
            'shipment_date': str(sh.shipment_date),
            'branch': sh.branch.name,
            'direction': sh.direction,
            'direction_label': DIRECTION_LABELS.get(sh.direction, ''),
            'from_stock': sh.from_stock.name,
            'to_stock': sh.to_stock.name,
            'status': sh.status,
            'status_label': STATUS_LABELS.get(sh.status, sh.status),
            'status_color': STATUS_COLORS.get(sh.status, 'secondary'),
            'has_difference': sh.has_difference,
            'total_lines': sh.lines.count(),
        })
    return JsonResponse({'draw': draw, 'recordsTotal': total, 'recordsFiltered': filtered, 'data': rows},
                        json_dumps_params={'ensure_ascii': False})


# ──────────────────────────────────────────────────────────────
# الإنشاء (الإدارة)
# ──────────────────────────────────────────────────────────────

@login_required
@require_permission('add_central_shipments')
def shipment_create(request):
    _central_only(request)
    tenant = _tenant(request)
    central = Stock.objects.for_tenant(tenant).filter(is_central=True, is_active=True).first()

    if request.method == 'POST':
        if central is None:
            return _err('لا يوجد مخزن مركزي نشط. أنشئ المخزن المركزي أولاً.')
        try:
            data = json.loads(request.body or '{}')
        except (json.JSONDecodeError, ValueError):
            return _err('بيانات غير صالحة')
        try:
            to_stock = Stock.objects.for_tenant(tenant).get(pk=int(data.get('to_stock')))
        except (Stock.DoesNotExist, TypeError, ValueError):
            return _err('يرجى اختيار مخزن الفرع المستلِم')

        from apps.items.models import Item
        lines = []
        for ln in data.get('lines') or []:
            try:
                item = Item.objects.for_tenant(tenant).get(pk=int(ln.get('item_id')))
                qty = Decimal(str(ln.get('quantity')))
            except (Item.DoesNotExist, TypeError, ValueError, InvalidOperation):
                return _err('بيانات البنود غير صالحة')
            lines.append({'item': item, 'quantity': qty})

        try:
            with transaction.atomic():
                shipment = create_shipment(
                    tenant, central, to_stock, data.get('shipment_date') or timezone.localdate(),
                    data.get('notes', ''), lines, user=request.user)
        except ValueError as exc:
            return _err(str(exc))

        redirect_url = reverse('stocks:shipment_detail', args=[shipment.pk])
        log_activity(request, 'إنشاء شحنة مخزن مركزي',
                     f'{shipment.shipment_number}\nإلى: {to_stock.name} ({shipment.branch.name})', 'create')
        if data.get('send'):
            try:
                send_shipment(shipment, request.user)
            except ValueError as exc:
                return JsonResponse({'success': False, 'message': str(exc), 'redirect': redirect_url, 'id': shipment.id},
                                    status=400, json_dumps_params={'ensure_ascii': False})
            _notify(shipment, to_branch=True, title='شحنة جديدة في الطريق',
                    message=f'شحنة {shipment.shipment_number} من المخزن المركزي إلى {shipment.to_stock.name}. أكّد الاستلام عند وصولها.')
            log_activity(request, 'إرسال شحنة مخزن مركزي', shipment.shipment_number, 'create')
        return JsonResponse({'success': True, 'id': shipment.id, 'redirect': redirect_url})

    branch_stocks = (
        Stock.objects.for_tenant(tenant).filter(is_active=True, is_central=False, branch__isnull=False)
        .select_related('branch').order_by('branch__name', 'name'))
    return render(request, 'stocks/shipment_form.html', {
        'central_stock': central, 'branch_stocks': branch_stocks, 'today': str(timezone.localdate()),
    })


@login_required
@require_permission('add_central_shipments')
def shipment_items_api(request):
    """أصناف المخزن المركزي مع الكمية المتاحة فيه (لبحث إضافة بنود الشحنة)."""
    _central_only(request)
    tenant = _tenant(request)
    central = Stock.objects.for_tenant(tenant).filter(is_central=True).first()
    search = request.GET.get('q', '').strip()
    from apps.items.models import Item
    qs = Item.objects.for_tenant(tenant).filter(is_active=True, item_type__in=['product', 'material'])
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search))
    items = list(qs.order_by('name')[:60])
    qty = {}
    if central and items:
        for sq in StockQuantity.objects.filter(tenant=tenant, stock=central, item__in=items):
            qty[sq.item_id] = float((sq.quantity or 0) - (sq.reserved_quantity or 0))
    data = [{'id': i.id, 'name': i.name, 'sku': i.sku or '', 'unit': i.base_unit_name,
             'available_qty': qty.get(i.id, 0)} for i in items]
    return JsonResponse({'items': data}, json_dumps_params={'ensure_ascii': False})


# ──────────────────────────────────────────────────────────────
# مرتجع من الفرع إلى المخزن المركزي (يرسله الفرع وتؤكد الإدارة استلامه)
# ──────────────────────────────────────────────────────────────

def _branch_only(request):
    tenant = _tenant(request)
    if not tenant or not tenant.is_hybrid_purchasing() or getattr(request, 'branch', None) is None:
        raise Http404


@login_required
@require_permission('create_central_returns')
def return_create(request):
    _branch_only(request)
    tenant = _tenant(request)
    branch = request.branch
    own_stocks = Stock.objects.for_tenant(tenant).filter(branch=branch, is_active=True, is_central=False).order_by('name')
    central = Stock.objects.for_tenant(tenant).filter(is_central=True, is_active=True).first()

    if request.method == 'POST':
        if central is None:
            return _err('المخزن المركزي غير متاح حالياً.')
        try:
            data = json.loads(request.body or '{}')
            from_stock = own_stocks.get(pk=int(data.get('from_stock')))
        except (json.JSONDecodeError, ValueError, TypeError, Stock.DoesNotExist):
            return _err('يرجى اختيار مخزن من مخازن فرعك')
        from apps.items.models import Item
        lines = []
        for ln in data.get('lines') or []:
            try:
                item = Item.objects.for_tenant(tenant).get(pk=int(ln.get('item_id')))
                qty = Decimal(str(ln.get('quantity')))
            except (Item.DoesNotExist, TypeError, ValueError, InvalidOperation):
                return _err('بيانات البنود غير صالحة')
            lines.append({'item': item, 'quantity': qty})
        try:
            with transaction.atomic():  # إنشاء + إرسال معاً: إن فشل الإرسال لا يبقى شيء
                shipment = create_shipment(
                    tenant, from_stock, central, timezone.localdate(), data.get('notes', ''), lines,
                    user=request.user, direction='to_central')
                send_shipment(shipment, request.user)
        except ValueError as exc:
            return _err(str(exc))
        _notify(shipment, to_branch=False, title='مرتجع من فرع في الطريق',
                message=f'أرسل فرع {shipment.branch.name} المرتجع {shipment.shipment_number} إلى المخزن المركزي. أكّد استلامه عند وصوله.')
        log_activity(request, 'إرسال مرتجع إلى المخزن المركزي', shipment.shipment_number, 'create')
        return JsonResponse({'success': True, 'id': shipment.id,
                             'redirect': reverse('stocks:shipment_detail', args=[shipment.pk])})

    return render(request, 'stocks/shipment_form.html', {
        'mode': 'return', 'central_stock': central, 'branch_stocks': own_stocks, 'today': str(timezone.localdate()),
    })


@login_required
@require_permission('create_central_returns')
def return_items_api(request):
    """أصناف مخزن من مخازن فرع المستخدم مع الكمية المتاحة فيه."""
    _branch_only(request)
    tenant = _tenant(request)
    stock = Stock.objects.for_tenant(tenant).filter(
        pk=request.GET.get('stock_id') or 0, branch=request.branch, is_central=False).first()
    search = request.GET.get('q', '').strip()
    from apps.items.models import Item
    qs = Item.objects.for_tenant(tenant).filter(is_active=True, item_type__in=['product', 'material'])
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search))
    items = list(qs.order_by('name')[:60])
    qty = {}
    if stock and items:
        for sq in StockQuantity.objects.filter(tenant=tenant, stock=stock, item__in=items):
            qty[sq.item_id] = float((sq.quantity or 0) - (sq.reserved_quantity or 0))
    data = [{'id': i.id, 'name': i.name, 'sku': i.sku or '', 'unit': i.base_unit_name,
             'available_qty': qty.get(i.id, 0)} for i in items]
    return JsonResponse({'items': data}, json_dumps_params={'ensure_ascii': False})


# ──────────────────────────────────────────────────────────────
# التفاصيل والإجراءات
# ──────────────────────────────────────────────────────────────

@login_required
@require_scoped_permission('view_incoming_shipments', 'view_central_shipments')
def shipment_detail(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    shipment = get_object_or_404(
        _shipments(request, tenant).select_related('from_stock', 'to_stock', 'branch', 'received_by')
        .prefetch_related('lines__item', 'lines__batches'), pk=pk)
    central = is_central_request(request)
    user = request.user
    is_return = shipment.direction == 'to_central'
    return render(request, 'stocks/shipment_detail.html', {
        'shipment': shipment,
        'central_scope': central,
        'is_return': is_return,
        'status_label': STATUS_LABELS.get(shipment.status, shipment.status),
        'status_color': STATUS_COLORS.get(shipment.status, 'secondary'),
        'can_send': central and not is_return and shipment.status == 'draft' and user.has_perm_key('add_central_shipments'),
        'can_cancel': (
            (central and not is_return and shipment.status in ('draft', 'in_transit')
             and user.has_perm_key('cancel_central_shipments'))
            or ((not central) and is_return and shipment.status == 'in_transit'
                and user.has_perm_key('create_central_returns'))),
        # التوزيع يؤكده الفرع؛ والمرتجع تؤكده الإدارة المركزية.
        'can_receive': shipment.status == 'in_transit' and (
            ((not central) and not is_return and user.has_perm_key('receive_incoming_shipments'))
            or (central and is_return and user.has_perm_key('receive_central_returns'))),
    })


@login_required
@require_permission('add_central_shipments')
@require_POST
def shipment_send_ajax(request, pk):
    _central_only(request)
    tenant = _tenant(request)
    shipment = get_object_or_404(Shipment.objects.for_tenant(tenant), pk=pk, direction='to_branch')
    try:
        send_shipment(shipment, request.user)
    except ValueError as exc:
        return _err(str(exc))
    _notify(shipment, to_branch=True, title='شحنة جديدة في الطريق',
            message=f'شحنة {shipment.shipment_number} من المخزن المركزي إلى {shipment.to_stock.name}. أكّد الاستلام عند وصولها.')
    log_activity(request, 'إرسال شحنة مخزن مركزي', shipment.shipment_number, 'create')
    return JsonResponse({'success': True, 'message': 'تم إرسال الشحنة، وهي الآن في الطريق'},
                        json_dumps_params={'ensure_ascii': False})


@login_required
@require_any_permission('cancel_central_shipments', 'create_central_returns')
@require_POST
def shipment_cancel_ajax(request, pk):
    """
    توزيع: تلغيه الإدارة (cancel_central_shipments). مرتجع: يلغيه الفرع صاحبه قبل الاستلام
    (create_central_returns)، فتعود الكمية لمخزن الفرع.
    """
    tenant = _tenant(request)
    if not tenant or not tenant.is_hybrid_purchasing():
        raise Http404
    shipment = get_object_or_404(Shipment.objects.for_tenant(tenant), pk=pk)
    branch = getattr(request, 'branch', None)
    user = request.user
    if shipment.direction == 'to_branch':
        if branch is not None or not user.has_perm_key('cancel_central_shipments'):
            raise Http404
    else:
        if branch is None or shipment.branch_id != branch.pk or not user.has_perm_key('create_central_returns'):
            raise Http404
    was_in_transit = shipment.status == 'in_transit'
    try:
        cancel_shipment(shipment, request.user)
    except ValueError as exc:
        return _err(str(exc))
    if was_in_transit and shipment.direction == 'to_branch':
        _notify(shipment, to_branch=True, title='أُلغيت شحنة في الطريق',
                message=f'ألغت الإدارة الشحنة {shipment.shipment_number}؛ لا تنتظر وصولها.')
    elif was_in_transit:
        _notify(shipment, to_branch=False, title='أُلغي مرتجع في الطريق',
                message=f'ألغى فرع {shipment.branch.name} المرتجع {shipment.shipment_number}؛ لا تنتظر وصوله.')
    log_activity(request, 'إلغاء شحنة مخزن مركزي', shipment.shipment_number, 'delete')
    return JsonResponse({'success': True, 'message': 'تم إلغاء الشحنة'}, json_dumps_params={'ensure_ascii': False})


@login_required
@require_any_permission('receive_incoming_shipments', 'receive_central_returns')
@require_POST
def shipment_receive_ajax(request, pk):
    """
    تأكيد الكميات الفعلية: التوزيع يؤكده الفرع المستلِم فقط؛ والمرتجع تؤكده الإدارة المركزية فقط.
    """
    tenant = _tenant(request)
    if not tenant or not tenant.is_hybrid_purchasing():
        raise Http404
    branch = getattr(request, 'branch', None)
    user = request.user
    shipment = get_object_or_404(
        Shipment.objects.for_tenant(tenant).filter(sent_at__isnull=False).exclude(status='draft'), pk=pk)
    if shipment.direction == 'to_branch':
        if branch is None or shipment.branch_id != branch.pk or not user.has_perm_key('receive_incoming_shipments'):
            raise Http404
    else:
        if branch is not None or not user.has_perm_key('receive_central_returns'):
            raise Http404
    try:
        body = json.loads(request.body or '{}')
        received = {int(k): Decimal(str(v)) for k, v in (body.get('lines') or {}).items()}
    except (json.JSONDecodeError, ValueError, InvalidOperation, TypeError):
        return _err('كميات غير صالحة')
    valid_ids = set(shipment.lines.values_list('id', flat=True))
    if set(received) - valid_ids:
        return _err('بند غير تابع لهذه الشحنة')
    try:
        receive_shipment(shipment, received, request.user)
    except ValueError as exc:
        return _err(str(exc))
    shipment.refresh_from_db()
    if shipment.direction == 'to_branch':
        if shipment.has_difference:
            _notify(shipment, to_branch=False, title='فرق في استلام شحنة',
                    message=f'استلم فرع {shipment.branch.name} الشحنة {shipment.shipment_number} بكميات أقل من المرسلة؛ عاد الفرق للمخزن المركزي.')
        else:
            _notify(shipment, to_branch=False, title='تم استلام شحنة',
                    message=f'أكّد فرع {shipment.branch.name} استلام الشحنة {shipment.shipment_number} كاملة.')
    else:
        if shipment.has_difference:
            _notify(shipment, to_branch=True, title='فرق في استلام مرتجعك',
                    message=f'استلمت الإدارة المرتجع {shipment.shipment_number} بكميات أقل من المرسلة؛ عاد الفرق إلى مخزن فرعك.')
        else:
            _notify(shipment, to_branch=True, title='تم استلام مرتجعك',
                    message=f'أكّدت الإدارة استلام المرتجع {shipment.shipment_number} كاملاً.')
    log_activity(request, 'تأكيد استلام شحنة مخزن مركزي', shipment.shipment_number, 'update')
    return JsonResponse({'success': True, 'message': 'تم تأكيد الاستلام', 'has_difference': shipment.has_difference},
                        json_dumps_params={'ensure_ascii': False})
