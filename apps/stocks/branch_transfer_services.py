"""
خدمات التحويل بين الفروع.

الدورة: مسودة ← (إرسال من الفرع المحوِّل) في الطريق ← (اعتماد الفرع المستلِم) تم الاستلام.
  - الإرسال: تخرج الكمية من مخزن الفرع المحوِّل فوراً، وتُخصم الدفعات FEFO.
  - الاستلام: يعتمد الفرع المستلِم الكمية الفعلية لكل بند، فتدخل مخزنه بدفعاتها.
    الفرق يتحمّله الفرع المحوِّل:
      · الزائد: يُخصم من مخزن الفرع المحوِّل (خرج منه فعلاً دون تسجيل).
      · الناقص: يبقى معلّقاً (لا في هذا المخزن ولا ذاك) حتى تسويته.
  - تسوية الناقص (الفرع المحوِّل):
      · restock: البضاعة لم تخرج أصلاً، فتعود لمخزن الفرع المحوِّل بدفعاتها.
      · write_off: ضاعت أو تلفت، فتُسجَّل تالفاً على الفرع المحوِّل بسجل إتلاف مؤكد.
  - الإلغاء: للمسودة، أو للتحويل في الطريق فتعود الكمية والدفعات للفرع المحوِّل.
  - التكلفة: تكلفة الصنف وقت الإرسال، تُسجَّل على حركات المخزون.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.sales.batch_allocation import is_batch_tracked
from apps.sales.models import StockMovement

from .models import StockDestruction, StockDestructionLine, StockTransfer, StockTransferLineBatch
from .services import confirm_stock_destruction
from .shipment_services import _receive_batch, _return_batches_to_central, _sq


def is_inter_branch(from_stock, to_stock):
    """تحويل بين مخزنين تابعين لفرعين مختلفين."""
    return bool(from_stock.branch_id and to_stock.branch_id and from_stock.branch_id != to_stock.branch_id)


def _movement(transfer, item, stock, direction, qty, unit_cost, ref_type, balance, notes, date=None):
    return StockMovement.objects.create(
        tenant=transfer.tenant, item=item, stock=stock,
        movement_type='transfer_out' if direction == 'out' else 'transfer_in',
        direction=direction, quantity=qty, unit_cost=unit_cost,
        movement_date=date or timezone.localdate(), reference_type=ref_type, reference_id=transfer.id,
        balance_after=balance, notes=notes,
    )


def _lock(transfer, *statuses):
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if not transfer.is_inter_branch:
        raise ValueError('هذه العملية للتحويل بين الفروع فقط.')
    if transfer.status not in statuses:
        raise ValueError('حالة التحويل لا تسمح بهذه العملية.')
    return transfer


def _take_out(transfer, line, qty):
    """يخصم qty من مخزن الفرع المحوِّل بعد التحقق من المتاح، ويسجل دفعاتها FEFO."""
    item = line.item
    sq = _sq(transfer.tenant, transfer.from_stock, item)
    available = sq.quantity - sq.reserved_quantity
    if available < qty:
        raise ValueError(
            f'الكمية غير كافية للمنتج «{item.name}» في {transfer.from_stock.name} '
            f'(متاح: {available:.2f}، مطلوب: {qty:.2f}).')
    sq.quantity -= qty
    sq.save(update_fields=['quantity', 'updated_at'])
    _consume_batches(transfer, line, qty)
    return sq


def _consume_batches(transfer, line, qty):
    from apps.items.models import ItemBatch
    if not is_batch_tracked(line.item):
        return
    remaining = Decimal(qty)
    batches = (
        ItemBatch.objects.select_for_update()
        .filter(tenant=transfer.tenant, item=line.item, stock=transfer.from_stock, quantity_remaining__gt=0)
        .order_by(F('expiry_date').asc(nulls_last=True), 'batch_number', 'id'))
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        batch.save(update_fields=['quantity_remaining'])
        StockTransferLineBatch.objects.create(
            tenant=transfer.tenant, line=line, source_batch=batch, batch_number=batch.batch_number,
            expiry_date=batch.expiry_date, quantity_sent=take)
        remaining -= take
    if remaining > 0:  # كمية بلا دفعات موثّقة (رصيد افتتاحي مثلاً)
        StockTransferLineBatch.objects.create(
            tenant=transfer.tenant, line=line, batch_number='', expiry_date=None, quantity_sent=remaining)


@transaction.atomic
def send_branch_transfer(transfer, user=None):
    """يُخرج الكميات من مخزن الفرع المحوِّل ويجعل التحويل «في الطريق»."""
    transfer = _lock(transfer, 'draft')
    lines = [ln for ln in transfer.lines.select_related('item')
             if getattr(ln.item, 'item_type', None) != 'service']
    if not lines:
        raise ValueError('لا توجد بنود في التحويل.')
    for line in lines:
        line.unit_cost = line.item.cost_price or Decimal('0')
        line.save(update_fields=['unit_cost', 'updated_at'])
        sq = _take_out(transfer, line, line.quantity)
        _movement(transfer, line.item, transfer.from_stock, 'out', line.quantity, line.unit_cost,
                  'stock_transfer', sq.quantity,
                  f'تحويل {transfer.transfer_number} — إلى {transfer.to_stock.name} (في الطريق)',
                  date=transfer.transfer_date)
    transfer.status = 'in_transit'
    transfer.sent_at = timezone.now()
    transfer.updated_by = user
    transfer.save(update_fields=['status', 'sent_at', 'updated_by', 'updated_at'])
    return transfer


@transaction.atomic
def receive_branch_transfer(transfer, received_quantities, user=None):
    """
    اعتماد الفرع المستلِم. received_quantities: {line_id: Decimal} — الكمية الفعلية لكل بند
    (≥ 0، وقد تزيد على المرسلة). ما لم يُذكر بند يُعدّ مستلَماً كما أُرسل.
    """
    transfer = _lock(transfer, 'in_transit')
    tenant = transfer.tenant
    lines = [ln for ln in transfer.lines.select_related('item')
             if getattr(ln.item, 'item_type', None) != 'service']
    today = timezone.localdate()

    actual = {}
    for line in lines:
        raw = received_quantities.get(line.id, line.quantity)
        try:
            qty = Decimal(str(raw))
        except Exception:
            raise ValueError(f'كمية غير صالحة للمنتج «{line.item.name}».')
        if qty < 0:
            raise ValueError(f'الكمية المستلَمة للمنتج «{line.item.name}» لا يمكن أن تكون سالبة.')
        actual[line.id] = qty

    has_shortage = has_surplus = False
    for line in lines:
        item = line.item
        received = actual[line.id]
        surplus = received - line.quantity
        line.quantity_received = received
        line.save(update_fields=['quantity_received', 'updated_at'])

        if surplus > 0:
            # الزائد خرج فعلاً من الفرع المحوِّل دون تسجيل: يُخصم من مخزنه الآن.
            has_surplus = True
            sq_out = _take_out(transfer, line, surplus)
            _movement(transfer, item, transfer.from_stock, 'out', surplus, line.unit_cost,
                      'stock_transfer_surplus', sq_out.quantity,
                      f'زيادة استلام تحويل {transfer.transfer_number} — خُصمت على الفرع المحوِّل')
        elif surplus < 0:
            has_shortage = True

        if received > 0:
            sq_in = _sq(tenant, transfer.to_stock, item)
            sq_in.quantity += received
            sq_in.save(update_fields=['quantity', 'updated_at'])
            _movement(transfer, item, transfer.to_stock, 'in', received, line.unit_cost,
                      'stock_transfer', sq_in.quantity,
                      f'تحويل {transfer.transfer_number} — من {transfer.from_stock.name}')

        # توزيع المستلَم على الدفعات بترتيب الإرسال (الأقرب انتهاءً أولاً)؛ ما لم يُوزَّع ناقص معلّق.
        remaining = received
        for row in line.batches.select_for_update():
            take = min(row.quantity_sent, remaining)
            remaining -= take
            row.quantity_received = take
            row.save(update_fields=['quantity_received'])
            if take > 0 and (row.batch_number or row.expiry_date):
                _receive_batch(tenant, transfer.to_stock, item, row, take, today)

    transfer.status = 'received'
    transfer.has_difference = has_shortage or has_surplus
    transfer.shortage_status = 'pending' if has_shortage else 'none'
    transfer.received_at = timezone.now()
    transfer.received_by = user
    transfer.updated_by = user
    transfer.save(update_fields=['status', 'has_difference', 'shortage_status', 'received_at',
                                 'received_by', 'updated_by', 'updated_at'])
    return transfer


@transaction.atomic
def settle_branch_transfer_shortage(transfer, action, user=None):
    """تسوية الناقص المعلّق: restock (إرجاع لمخزن المحوِّل) أو write_off (تالف على المحوِّل)."""
    if action not in ('restock', 'write_off'):
        raise ValueError('إجراء تسوية غير صالح.')
    transfer = _lock(transfer, 'received')
    if transfer.shortage_status != 'pending':
        raise ValueError('لا يوجد ناقص بانتظار التسوية في هذا التحويل.')
    tenant = transfer.tenant
    lines = [ln for ln in transfer.lines.select_related('item') if ln.shortage > 0]

    # في الحالتين يعود الناقص أولاً لرصيد الفرع المحوِّل؛ الشطب يُخرجه بعدها بسجل إتلاف،
    # فيبقى الأثر في تقارير الإتلاف على الفرع المحوِّل.
    for line in lines:
        sq = _sq(tenant, transfer.from_stock, line.item)
        sq.quantity += line.shortage
        sq.save(update_fields=['quantity', 'updated_at'])
        _movement(transfer, line.item, transfer.from_stock, 'in', line.shortage, line.unit_cost,
                  'stock_transfer_shortage', sq.quantity,
                  f'ناقص تحويل {transfer.transfer_number} — عاد لمخزن الفرع المحوِّل')

    if action == 'restock':
        for line in lines:
            _return_batches_to_central(
                tenant, transfer.from_stock, line,
                {row.id: row.shortage for row in line.batches.all()})
        transfer.shortage_status = 'restocked'
    else:
        # الدفعات خُصمت عند الإرسال ولا تُعاد: البضاعة الضائعة لم تعد في أي دفعة.
        destruction = StockDestruction.objects.create(
            tenant=tenant, stock=transfer.from_stock, destruction_date=timezone.localdate(),
            status='draft', reason='damaged', reference_number=transfer.transfer_number,
            notes=f'ناقص استلام التحويل {transfer.transfer_number} إلى {transfer.to_stock.name} — '
                  f'يتحمّله الفرع المحوِّل.',
            created_by=user, updated_by=user)
        for line in lines:
            rows = [r for r in line.batches.all() if r.shortage > 0]
            parts = [(r.batch_number, r.expiry_date, r.shortage) for r in rows] or [('', None, line.shortage)]
            for batch_number, expiry_date, qty in parts:
                StockDestructionLine.objects.create(
                    tenant=tenant, destruction=destruction, item=line.item,
                    batch_number_snapshot=batch_number, expiry_date_snapshot=expiry_date,
                    quantity=qty, unit_cost_snapshot=line.unit_cost)
        confirm_stock_destruction(destruction, user)
        transfer.shortage_destruction = destruction
        transfer.shortage_status = 'written_off'

    transfer.settled_at = timezone.now()
    transfer.settled_by = user
    transfer.updated_by = user
    transfer.save(update_fields=['shortage_status', 'shortage_destruction', 'settled_at',
                                 'settled_by', 'updated_by', 'updated_at'])
    return transfer


@transaction.atomic
def cancel_branch_transfer(transfer, user=None):
    """إلغاء مسودة، أو تحويل في الطريق (فتعود كميته ودفعاته لمخزن الفرع المحوِّل)."""
    transfer = _lock(transfer, 'draft', 'in_transit')
    tenant = transfer.tenant
    if transfer.status == 'in_transit':
        for line in transfer.lines.select_related('item'):
            if getattr(line.item, 'item_type', None) == 'service':
                continue
            sq = _sq(tenant, transfer.from_stock, line.item)
            sq.quantity += line.quantity
            sq.save(update_fields=['quantity', 'updated_at'])
            _movement(transfer, line.item, transfer.from_stock, 'in', line.quantity, line.unit_cost,
                      'stock_transfer_cancel', sq.quantity, f'إلغاء تحويل {transfer.transfer_number}')
            _return_batches_to_central(
                tenant, transfer.from_stock, line,
                {row.id: row.quantity_sent for row in line.batches.all()})
    transfer.status = 'cancelled'
    transfer.updated_by = user
    transfer.save(update_fields=['status', 'updated_by', 'updated_at'])
    return transfer
