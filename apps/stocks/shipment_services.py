"""
خدمات شحنات المخزن المركزي (النمط الهجين).

الدورة: مسودة ← (إرسال) في الطريق ← (تأكيد الفرع للكمية الفعلية) تم الاستلام.
  - الإرسال: تخرج الكمية من المخزن المركزي فوراً، وتُخصم الدفعات بترتيب الأقرب
    انتهاءً أولاً (FEFO) للأصناف التي تتبع الدُفعات/الصلاحية.
  - الاستلام: يؤكد الفرع الكمية الفعلية لكل بند (≤ المرسلة)؛ تدخل مخزن الفرع بدفعاتها
    وتواريخ صلاحيتها، ويعود الفرق تلقائياً للمخزن المركزي مع تعليم الشحنة.
  - الإلغاء (من الإدارة): للمسودة أو للشحنة في الطريق فقط، فتعود الكمية والدفعات للمركزي.
  - التكلفة: تكلفة الشراء المركزي للصنف وقت الإرسال، تُسجَّل على حركات المخزون.
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.sales.batch_allocation import is_batch_tracked
from apps.sales.models import StockMovement

from .models import Shipment, ShipmentLine, ShipmentLineBatch, Stock, StockQuantity


def _sq(tenant, stock, item):
    sq, created = StockQuantity.objects.get_or_create(
        tenant=tenant, stock=stock, item=item,
        defaults={'quantity': Decimal('0'), 'reserved_quantity': Decimal('0')})
    if not created:
        sq = StockQuantity.objects.select_for_update().get(pk=sq.pk)
    return sq


def _movement(tenant, item, stock, direction, qty, unit_cost, date, ref_type, ref_id, balance, notes):
    return StockMovement.objects.create(
        tenant=tenant, item=item, stock=stock,
        movement_type='transfer_out' if direction == 'out' else 'transfer_in',
        direction=direction, quantity=qty, unit_cost=unit_cost, movement_date=date,
        reference_type=ref_type, reference_id=ref_id, balance_after=balance, notes=notes,
    )


def _is_service(item):
    return getattr(item, 'item_type', None) == 'service'


@transaction.atomic
def create_shipment(tenant, from_stock, to_stock, shipment_date, notes, lines, user=None, direction='to_branch'):
    """
    ينشئ شحنة مسودة. lines: [{'item': Item, 'quantity': Decimal}].
      - to_branch: من المخزن المركزي إلى مخزن فرع.
      - to_central: مرتجع من مخزن فرع إلى المخزن المركزي.
    """
    if not tenant.is_hybrid_purchasing():
        raise ValueError('الشحنات متاحة في نمط المشتريات الهجين فقط.')
    if direction == 'to_branch':
        if not from_stock.is_central:
            raise ValueError('الشحنة يجب أن تخرج من المخزن المركزي.')
        branch_stock, label = to_stock, 'تصل إلى'
    elif direction == 'to_central':
        if not to_stock.is_central or not to_stock.is_active:
            raise ValueError('المرتجع يجب أن يصل إلى المخزن المركزي.')
        branch_stock, label = from_stock, 'تخرج من'
    else:
        raise ValueError('اتجاه شحنة غير صالح.')
    if branch_stock.is_central or branch_stock.branch_id is None:
        raise ValueError(f'الشحنة يجب أن {label} مخزن تابع لفرع.')
    if not branch_stock.is_active:
        raise ValueError('مخزن الفرع المختار غير نشط.')
    clean = [(ln['item'], Decimal(str(ln['quantity']))) for ln in lines if Decimal(str(ln['quantity'])) > 0]
    if not clean:
        raise ValueError('أضف بنداً واحداً على الأقل بكمية أكبر من صفر.')
    seen = set()
    for item, _qty in clean:
        if item.pk in seen:
            raise ValueError(f'الصنف «{item.name}» مكرر في الشحنة.')
        seen.add(item.pk)

    shipment = Shipment.objects.create(
        tenant=tenant, direction=direction, from_stock=from_stock, to_stock=to_stock,
        branch_id=branch_stock.branch_id, shipment_date=shipment_date, notes=notes or '',
        created_by=user, updated_by=user)
    for item, qty in clean:
        ShipmentLine.objects.create(
            tenant=tenant, shipment=shipment, item=item, quantity_sent=qty,
            unit_cost=item.cost_price or Decimal('0'), created_by=user, updated_by=user)
    return shipment


@transaction.atomic
def send_shipment(shipment, user=None):
    """يُخرج الكميات من المخزن المُرسِل ويجعل الشحنة «في الطريق»."""
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status != 'draft':
        raise ValueError('يمكن إرسال المسودات فقط.')
    tenant = shipment.tenant
    lines = list(shipment.lines.select_related('item'))
    if not lines:
        raise ValueError('لا توجد بنود في الشحنة.')

    for line in lines:
        item = line.item
        if _is_service(item):
            raise ValueError(f'«{item.name}» خدمة ولا تُشحن.')
        sq = _sq(tenant, shipment.from_stock, item)
        available = sq.quantity - sq.reserved_quantity
        if available < line.quantity_sent:
            raise ValueError(
                f'الكمية غير كافية للمنتج «{item.name}» في {shipment.from_stock.name} '
                f'(متاح: {available:.2f}، مطلوب: {line.quantity_sent:.2f}).')
        sq.quantity -= line.quantity_sent
        sq.save(update_fields=['quantity', 'updated_at'])
        line.unit_cost = item.cost_price or Decimal('0')
        line.save(update_fields=['unit_cost', 'updated_at'])
        _movement(tenant, item, shipment.from_stock, 'out', line.quantity_sent, line.unit_cost,
                  shipment.shipment_date, 'central_shipment', shipment.id, sq.quantity,
                  f'شحنة {shipment.shipment_number} — إلى {shipment.to_stock.name}')
        _consume_batches(tenant, shipment.from_stock, line)

    shipment.status = 'in_transit'
    shipment.sent_at = timezone.now()
    shipment.updated_by = user
    shipment.save(update_fields=['status', 'sent_at', 'updated_by', 'updated_at'])
    return shipment


def _consume_batches(tenant, stock, line):
    """FEFO: يخصم من دفعات المخزن المركزي ويسجّل ما خرج لكل دفعة (الباقي بلا دفعة)."""
    from apps.items.models import ItemBatch
    if not is_batch_tracked(line.item):
        return
    remaining = Decimal(line.quantity_sent)
    batches = (
        ItemBatch.objects.select_for_update()
        .filter(tenant=tenant, item=line.item, stock=stock, quantity_remaining__gt=0)
        .order_by(F('expiry_date').asc(nulls_last=True), 'batch_number', 'id'))
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        batch.quantity_remaining -= take
        batch.save(update_fields=['quantity_remaining'])
        ShipmentLineBatch.objects.create(
            tenant=tenant, line=line, source_batch=batch, batch_number=batch.batch_number,
            expiry_date=batch.expiry_date, quantity_sent=take)
        remaining -= take
    if remaining > 0:  # كمية بلا دفعات موثّقة (رصيد افتتاحي مثلاً)
        ShipmentLineBatch.objects.create(
            tenant=tenant, line=line, source_batch=None, batch_number='', expiry_date=None,
            quantity_sent=remaining)


def _return_batches_to_central(tenant, stock, line, quantities):
    """يعيد كميات الدفعات للمخزن المركزي. quantities: {batch_row_id: qty}."""
    from apps.items.models import ItemBatch
    for row in line.batches.select_for_update():
        qty = quantities.get(row.id, Decimal('0'))
        if qty <= 0 or (not row.source_batch_id and not row.batch_number):
            continue
        batch = ItemBatch.objects.select_for_update().filter(pk=row.source_batch_id).first() \
            if row.source_batch_id else None
        if batch is None:
            batch = ItemBatch.objects.create(
                tenant=tenant, item=line.item, stock=stock, batch_number=row.batch_number,
                expiry_date=row.expiry_date, quantity_received=qty, quantity_remaining=0,
                purchase_date=timezone.localdate())
        batch.quantity_remaining += qty
        batch.save(update_fields=['quantity_remaining'])


@transaction.atomic
def receive_shipment(shipment, received_quantities, user=None):
    """
    تأكيد الفرع للاستلام. received_quantities: {line_id: Decimal} — الكمية الفعلية
    لكل بند (0 ≤ كمية ≤ المرسلة). ما لم يُذكر بند يُعدّ مستلَماً بالكامل.
    """
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status != 'in_transit':
        raise ValueError('يمكن تأكيد استلام الشحنات التي في الطريق فقط.')
    tenant = shipment.tenant
    lines = list(shipment.lines.select_related('item'))
    today = timezone.localdate()

    # تحقق أولاً من كل الكميات قبل أي تعديل.
    actual = {}
    for line in lines:
        raw = received_quantities.get(line.id, line.quantity_sent)
        try:
            qty = Decimal(str(raw))
        except Exception:
            raise ValueError(f'كمية غير صالحة للمنتج «{line.item.name}».')
        if qty < 0 or qty > line.quantity_sent:
            raise ValueError(
                f'الكمية المستلَمة للمنتج «{line.item.name}» يجب أن تكون بين 0 و {line.quantity_sent:.2f}.')
        actual[line.id] = qty

    has_difference = False
    for line in lines:
        item = line.item
        received = actual[line.id]
        diff = line.quantity_sent - received
        line.quantity_received = received
        line.save(update_fields=['quantity_received', 'updated_at'])

        if received > 0:
            sq_in = _sq(tenant, shipment.to_stock, item)
            sq_in.quantity += received
            sq_in.save(update_fields=['quantity', 'updated_at'])
            _movement(tenant, item, shipment.to_stock, 'in', received, line.unit_cost, today,
                      'central_shipment', shipment.id, sq_in.quantity,
                      f'شحنة {shipment.shipment_number} — من {shipment.from_stock.name}')

        returned_by_row = {}
        if diff > 0:
            has_difference = True
            sq_c = _sq(tenant, shipment.from_stock, item)
            sq_c.quantity += diff
            sq_c.save(update_fields=['quantity', 'updated_at'])
            _movement(tenant, item, shipment.from_stock, 'in', diff, line.unit_cost, today,
                      'central_shipment_difference', shipment.id, sq_c.quantity,
                      f'فرق استلام شحنة {shipment.shipment_number} (عاد للمخزن المركزي)')

        # توزيع المستلَم على الدفعات بترتيب الإرسال (الأقدم صلاحية أولاً)
        remaining = received
        for row in line.batches.select_for_update():
            take = min(row.quantity_sent, remaining)
            remaining -= take
            row.quantity_received = take
            row.save(update_fields=['quantity_received'])
            row_diff = row.quantity_sent - take
            if row_diff > 0:
                returned_by_row[row.id] = row_diff
            if take > 0 and (row.batch_number or row.expiry_date):
                _receive_batch(tenant, shipment.to_stock, item, row, take, today)
        if returned_by_row:
            _return_batches_to_central(tenant, shipment.from_stock, line, returned_by_row)

    shipment.status = 'received'
    shipment.has_difference = has_difference
    shipment.received_at = timezone.now()
    shipment.received_by = user
    shipment.updated_by = user
    shipment.save(update_fields=['status', 'has_difference', 'received_at', 'received_by', 'updated_by', 'updated_at'])
    return shipment


def _receive_batch(tenant, stock, item, row, qty, today):
    from apps.items.models import ItemBatch
    batch = (
        ItemBatch.objects.select_for_update()
        .filter(tenant=tenant, item=item, stock=stock, batch_number=row.batch_number,
                expiry_date=row.expiry_date).first())
    if batch is None:
        ItemBatch.objects.create(
            tenant=tenant, item=item, stock=stock, batch_number=row.batch_number,
            expiry_date=row.expiry_date, quantity_received=qty, quantity_remaining=qty,
            purchase_date=today)
    else:
        batch.quantity_received += qty
        batch.quantity_remaining += qty
        batch.save(update_fields=['quantity_received', 'quantity_remaining'])


@transaction.atomic
def cancel_shipment(shipment, user=None):
    """إلغاء مسودة، أو شحنة في الطريق (فتعود كميتها ودفعاتها للمخزن المركزي)."""
    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status not in ('draft', 'in_transit'):
        raise ValueError('لا يمكن إلغاء شحنة تم استلامها أو ملغاة.')
    tenant = shipment.tenant
    if shipment.status == 'in_transit':
        today = timezone.localdate()
        for line in shipment.lines.select_related('item'):
            sq = _sq(tenant, shipment.from_stock, line.item)
            sq.quantity += line.quantity_sent
            sq.save(update_fields=['quantity', 'updated_at'])
            _movement(tenant, line.item, shipment.from_stock, 'in', line.quantity_sent, line.unit_cost,
                      today, 'central_shipment_cancel', shipment.id, sq.quantity,
                      f'إلغاء شحنة {shipment.shipment_number}')
            _return_batches_to_central(
                tenant, shipment.from_stock, line,
                {row.id: row.quantity_sent for row in line.batches.all()})
    shipment.status = 'cancelled'
    shipment.updated_by = user
    shipment.save(update_fields=['status', 'updated_by', 'updated_at'])
    return shipment
