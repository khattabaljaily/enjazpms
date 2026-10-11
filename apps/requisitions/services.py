"""
خدمات طلبات الشراء (النمط المركزي): إرسال، مراجعة، إلغاء، وتحديث الحالة حسب الشحنات.
كل دالة ترفع ValueError برسالة عربية جاهزة للعرض عند عملية غير مسموحة.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import PurchaseRequisition, PurchaseRequisitionLine


def _notify(req, *, to_branch, title, message):
    """إشعار داخلي: للفرع صاحب الطلب (to_branch) أو للإدارة المركزية (بلا فرع)."""
    from apps.notifications.models import Notification
    Notification.objects.create(
        tenant=req.tenant, branch=req.branch if to_branch else None,
        notification_type='general', title=title, message=message,
        link=reverse('requisitions:detail', args=[req.pk]), priority='high' if req.priority == 'urgent' else 'medium')


def to_decimal(value, field_label='الكمية'):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_label} غير صالحة.')
    if not number.is_finite():
        raise ValueError(f'{field_label} غير صالحة.')
    return number


@transaction.atomic
def save_requisition(tenant, branch, user, *, stock, request_date, needed_by, priority, notes, lines,
                     requisition=None):
    """
    ينشئ طلباً جديداً أو يحدّث مسودة قائمة. lines: [{'item': Item, 'quantity': Decimal, 'notes': str}].
    """
    if stock is None or stock.branch_id != branch.pk or stock.is_central or not stock.is_active:
        raise ValueError('اختر مخزناً نشطاً تابعاً لفرعك ليستلم الطلب.')
    clean = []
    seen = set()
    for ln in lines:
        qty = to_decimal(ln['quantity'])
        if qty <= 0:
            continue
        item = ln['item']
        if item.pk in seen:
            raise ValueError(f'الصنف «{item.name}» مكرر في الطلب.')
        if item.item_type == 'service':
            raise ValueError(f'«{item.name}» خدمة ولا تُطلب.')
        seen.add(item.pk)
        clean.append((item, qty, (ln.get('notes') or '')[:200]))
    if not clean:
        raise ValueError('أضف بنداً واحداً على الأقل بكمية أكبر من صفر.')
    if priority not in dict(PurchaseRequisition.PRIORITY_CHOICES):
        priority = 'normal'

    if requisition is None:
        requisition = PurchaseRequisition(tenant=tenant, branch=branch, created_by=user)
    elif requisition.status != 'draft':
        raise ValueError('يمكن تعديل المسودات فقط.')
    requisition.stock = stock
    requisition.request_date = request_date
    requisition.needed_by = needed_by or None
    requisition.priority = priority
    requisition.notes = notes or ''
    requisition.updated_by = user
    requisition.save()
    requisition.lines.all().delete()
    for item, qty, note in clean:
        PurchaseRequisitionLine.objects.create(
            tenant=tenant, requisition=requisition, item=item, requested_quantity=qty, notes=note,
            created_by=user, updated_by=user)
    return requisition


@transaction.atomic
def submit_requisition(req, user=None):
    req = PurchaseRequisition.objects.select_for_update().get(pk=req.pk)
    if req.status != 'draft':
        raise ValueError('يمكن إرسال المسودات فقط.')
    if not req.lines.exists():
        raise ValueError('لا توجد بنود في الطلب.')
    req.status = 'submitted'
    req.submitted_at = timezone.now()
    req.updated_by = user
    req.save(update_fields=['status', 'submitted_at', 'updated_by', 'updated_at'])
    _notify(req, to_branch=False, title='طلب شراء جديد من فرع',
            message=f'طلب {req.number} من فرع {req.branch.name}'
                    f'{" (عاجل)" if req.priority == "urgent" else ""}. بانتظار مراجعة الإدارة.')
    return req


@transaction.atomic
def review_requisition(req, user, decisions, notes='', reject_all=False):
    """
    مراجعة الإدارة. decisions: {line_id: approved_quantity}؛ بند غير مذكور يُعتمد بكمية الطلب.
    الصفر يرفض البند، وإن رُفضت كل البنود (أو reject_all) يصير الطلب «مرفوضاً».
    """
    req = PurchaseRequisition.objects.select_for_update().get(pk=req.pk)
    if req.status != 'submitted':
        raise ValueError('تُراجع الطلبات المُرسَلة فقط.')
    lines = list(req.lines.select_related('item'))
    total_approved = Decimal('0')
    for line in lines:
        if reject_all:
            qty = Decimal('0')
        else:
            raw = decisions.get(line.pk, decisions.get(str(line.pk)))
            qty = line.requested_quantity if raw is None else to_decimal(raw, f'كمية «{line.item.name}»')
        if qty < 0:
            raise ValueError(f'كمية «{line.item.name}» لا تكون سالبة.')
        if qty > line.requested_quantity:
            raise ValueError(
                f'كمية «{line.item.name}» المعتمدة ({qty:.2f}) تتجاوز المطلوبة ({line.requested_quantity:.2f}).')
        line.approved_quantity = qty
        line.updated_by = user
        line.save(update_fields=['approved_quantity', 'updated_by', 'updated_at'])
        total_approved += qty
    req.status = 'approved' if total_approved > 0 else 'rejected'
    req.reviewed_at = timezone.now()
    req.reviewed_by = user
    req.review_notes = notes or ''
    req.updated_by = user
    req.save(update_fields=['status', 'reviewed_at', 'reviewed_by', 'review_notes', 'updated_by', 'updated_at'])
    if req.status == 'approved':
        _notify(req, to_branch=True, title='اعتُمد طلب الشراء',
                message=f'اعتمدت الإدارة طلبك {req.number}. سيصلك ما اعتُمد بشحنة تؤكد استلامها.')
    else:
        _notify(req, to_branch=True, title='رُفض طلب الشراء',
                message=f'رفضت الإدارة طلبك {req.number}.{(" السبب: " + req.review_notes) if req.review_notes else ""}')
    return req


@transaction.atomic
def cancel_requisition(req, user, *, by_head_office):
    req = PurchaseRequisition.objects.select_for_update().get(pk=req.pk)
    if by_head_office:
        allowed = ('submitted', 'approved')
    else:
        allowed = ('draft', 'submitted')
    if req.status not in allowed:
        raise ValueError('لا يمكن إلغاء الطلب في حالته الحالية.')
    if req.status == 'approved' and req.shipments.exclude(status='cancelled').exists():
        raise ValueError('لا يمكن إلغاء طلب أُنشئت له شحنات؛ ألغِ الشحنات أولاً.')
    req.status = 'cancelled'
    req.updated_by = user
    req.save(update_fields=['status', 'updated_by', 'updated_at'])
    if by_head_office:
        _notify(req, to_branch=True, title='أُلغي طلب الشراء', message=f'ألغت الإدارة طلبك {req.number}.')
    elif req.submitted_at:
        _notify(req, to_branch=False, title='أُلغي طلب شراء',
                message=f'ألغى فرع {req.branch.name} طلبه {req.number}.')
    return req


def refresh_status(req):
    """يضبط «معتمد/مكتمل التوريد» حسب الشحنات الخارجة فعلاً (يُستدعى بعد إرسال شحنة أو إلغائها)."""
    if req is None or req.status not in ('approved', 'fulfilled'):
        return req
    lines = list(req.lines.all())
    done = bool(lines) and all(
        (ln.approved_quantity or Decimal('0')) <= 0 or ln.fulfilled_quantity >= ln.approved_quantity
        for ln in lines)
    new_status = 'fulfilled' if done else 'approved'
    if new_status != req.status:
        req.status = new_status
        req.save(update_fields=['status', 'updated_at'])
        if new_status == 'fulfilled':
            _notify(req, to_branch=True, title='اكتمل توريد طلب الشراء',
                    message=f'خرجت كل كميات طلبك {req.number} من الإدارة. أكّد استلام الشحنات عند وصولها.')
    return req


def remaining_lines(req):
    """بنود الطلب المعتمد التي بقي منها كمية لم تُنشأ لها شحنة: [(line, remaining)]."""
    return [(ln, ln.remaining_quantity) for ln in req.lines.select_related('item') if ln.remaining_quantity > 0]
