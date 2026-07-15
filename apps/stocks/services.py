from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from apps.sales.models import StockMovement
from .models import StockQuantity, StockTransfer, StockTransferLine, Stocktake, StocktakeLine, ManufacturingOrder


def _get_sq(tenant, stock, item):
    return StockQuantity.objects.select_for_update().get(
        tenant=tenant, stock=stock, item=item
    )


def _get_or_create_sq(tenant, stock, item):
    """Get or create a StockQuantity row (with SELECT FOR UPDATE on existing rows)."""
    sq, created = StockQuantity.objects.get_or_create(
        tenant=tenant, stock=stock, item=item,
        defaults={'quantity': Decimal('0'), 'reserved_quantity': Decimal('0')}
    )
    if not created:
        sq = StockQuantity.objects.select_for_update().get(
            tenant=tenant, stock=stock, item=item
        )
    return sq


@transaction.atomic
def confirm_stock_transfer(transfer):
    """
    Deduct qty from from_stock and add to to_stock.
    Creates paired transfer_out / transfer_in StockMovement records.
    """
    if transfer.status != 'draft':
        raise ValueError('يمكن تأكيد المسودات فقط')

    tenant = transfer.tenant
    lines = list(transfer.lines.select_related('item'))
    if not lines:
        raise ValueError('لا توجد بنود في التحويل')

    for line in lines:
        item = line.item
        if getattr(item, 'item_type', None) == 'service':
            continue

        sq_out = _get_sq(tenant, transfer.from_stock, item)
        available = sq_out.quantity - sq_out.reserved_quantity
        if available < line.quantity:
            raise ValueError(
                f'الكمية غير كافية للمنتج "{item.name}" '
                f'(متاح: {available:.2f}، مطلوب: {line.quantity:.2f})'
            )

        sq_out.quantity -= line.quantity
        sq_out.save(update_fields=['quantity', 'updated_at'])

        StockMovement.objects.create(
            tenant=tenant, item=item, stock=transfer.from_stock,
            movement_type='transfer_out', direction='out',
            quantity=line.quantity, unit_cost=Decimal('0'),
            movement_date=transfer.transfer_date,
            reference_type='stock_transfer', reference_id=transfer.id,
            balance_after=sq_out.quantity,
            notes=f'تحويل {transfer.transfer_number} — إلى {transfer.to_stock.name}',
        )

        sq_in = _get_sq(tenant, transfer.to_stock, item)
        sq_in.quantity += line.quantity
        sq_in.save(update_fields=['quantity', 'updated_at'])

        StockMovement.objects.create(
            tenant=tenant, item=item, stock=transfer.to_stock,
            movement_type='transfer_in', direction='in',
            quantity=line.quantity, unit_cost=Decimal('0'),
            movement_date=transfer.transfer_date,
            reference_type='stock_transfer', reference_id=transfer.id,
            balance_after=sq_in.quantity,
            notes=f'تحويل {transfer.transfer_number} — من {transfer.from_stock.name}',
        )

    transfer.status = 'confirmed'
    transfer.save(update_fields=['status', 'updated_at'])


@transaction.atomic
def cancel_stock_transfer(transfer):
    """Reverse a confirmed transfer by swapping stock quantities back."""
    if transfer.status != 'confirmed':
        raise ValueError('يمكن إلغاء المؤكدات فقط')

    tenant = transfer.tenant
    lines = list(transfer.lines.select_related('item'))

    for line in lines:
        item = line.item
        if getattr(item, 'item_type', None) == 'service':
            continue

        sq_in = _get_sq(tenant, transfer.to_stock, item)
        if sq_in.quantity < line.quantity:
            raise ValueError(
                f'لا يمكن الإلغاء: الكمية في المخزن المستلِم غير كافية للمنتج "{item.name}"'
            )
        sq_in.quantity -= line.quantity
        sq_in.save(update_fields=['quantity', 'updated_at'])

        sq_out = _get_sq(tenant, transfer.from_stock, item)
        sq_out.quantity += line.quantity
        sq_out.save(update_fields=['quantity', 'updated_at'])

        _TRANSFER_FLIP = {'transfer_in': 'transfer_out', 'transfer_out': 'transfer_in'}
        for mv in StockMovement.objects.filter(
            tenant=tenant,
            reference_type='stock_transfer',
            reference_id=transfer.id,
            item=item,
            is_reversal=False,
        ):
            flipped_dir = 'in' if mv.direction == 'out' else 'out'
            balance = sq_in.quantity if mv.stock_id == transfer.to_stock_id else sq_out.quantity
            StockMovement.objects.create(
                tenant=tenant,
                item=item,
                stock=mv.stock,
                movement_type=_TRANSFER_FLIP.get(mv.movement_type, 'adjustment_' + flipped_dir),
                direction=flipped_dir,
                quantity=mv.quantity,
                unit_cost=mv.unit_cost,
                balance_after=balance,
                reference_type=mv.reference_type,
                reference_id=mv.reference_id,
                movement_date=timezone.localdate(),
                notes=f'إلغاء تحويل مخزون: {mv.notes}' if mv.notes else 'إلغاء تحويل مخزون',
                is_reversal=True,
            )

    transfer.status = 'cancelled'
    transfer.save(update_fields=['status', 'updated_at'])


# ─────────────────────────────────────────────
# Stocktake services
# ─────────────────────────────────────────────

@transaction.atomic
def confirm_stocktake(stocktake):
    """
    Apply counted quantities vs system quantities:
    - positive diff → adjustment_in
    - negative diff → adjustment_out
    Skips items with zero difference.
    """
    if stocktake.status != 'draft':
        raise ValueError('يمكن تأكيد المسودات فقط')

    tenant = stocktake.tenant
    lines = list(stocktake.lines.select_related('item'))
    if not lines:
        raise ValueError('لا توجد بنود في الجرد')

    for line in lines:
        diff = line.counted_quantity - line.system_quantity
        if diff == 0:
            continue

        item = line.item
        if getattr(item, 'item_type', None) == 'service':
            continue

        sq = _get_sq(tenant, stocktake.stock, item)
        sq.quantity += diff
        sq.save(update_fields=['quantity', 'updated_at'])

        direction = 'in' if diff > 0 else 'out'
        mv_type   = 'adjustment_in' if diff > 0 else 'adjustment_out'
        diff_label = 'زيادة' if diff > 0 else 'نقص'

        StockMovement.objects.create(
            tenant=tenant, item=item, stock=stocktake.stock,
            movement_type=mv_type, direction=direction,
            quantity=abs(diff), unit_cost=Decimal('0'),
            movement_date=stocktake.stocktake_date,
            reference_type='stocktake', reference_id=stocktake.id,
            balance_after=sq.quantity,
            notes=f'جرد {stocktake.stocktake_number} — {diff_label} (فعلي {line.counted_quantity:g} / نظام {line.system_quantity:g})',
        )

    stocktake.status = 'confirmed'
    stocktake.save(update_fields=['status', 'updated_at'])


# ─────────────────────────────────────────────
# Manufacturing Order Services
# ─────────────────────────────────────────────

@transaction.atomic
def confirm_manufacturing_order(order):
    if order.status != 'draft':
        raise ValueError('الأمر ليس في حالة مسودة')

    recipe = order.recipe
    total_cost = Decimal('0')

    for bom_line in recipe.lines.select_related('component', 'unit').all():
        factor = Decimal('1')
        if bom_line.unit and bom_line.unit.factor:
            factor = bom_line.unit.factor
        needed = (bom_line.quantity * factor * order.quantity).quantize(Decimal('0.0001'))

        sq = _get_or_create_sq(order.tenant, order.stock, bom_line.component)
        if sq.quantity < needed:
            raise ValueError(
                f"رصيد «{bom_line.component.name}» غير كافٍ. "
                f"المتاح: {sq.quantity}، المطلوب: {needed}."
            )
        sq.quantity -= needed
        sq.save(update_fields=['quantity', 'updated_at'])

        StockMovement.objects.create(
            tenant=order.tenant,
            item=bom_line.component,
            stock=order.stock,
            movement_type='adjustment_out',
            direction='out',
            quantity=needed,
            unit_cost=bom_line.component.cost_price,
            balance_after=sq.quantity,
            reference_type='manufacturing_order',
            reference_id=order.id,
            movement_date=order.order_date,
            notes=f"استهلاك في تصنيع {recipe.item.name} — أمر {order.order_number}",
        )
        total_cost += (bom_line.component.cost_price * needed)

    # Add finished product
    finished_sq = _get_or_create_sq(order.tenant, order.stock, recipe.item)
    finished_sq.quantity += order.quantity
    finished_sq.save(update_fields=['quantity', 'updated_at'])

    unit_cost = (total_cost / order.quantity).quantize(Decimal('0.01')) if order.quantity else Decimal('0')
    StockMovement.objects.create(
        tenant=order.tenant,
        item=recipe.item,
        stock=order.stock,
        movement_type='adjustment_in',
        direction='in',
        quantity=order.quantity,
        unit_cost=unit_cost,
        balance_after=finished_sq.quantity,
        reference_type='manufacturing_order',
        reference_id=order.id,
        movement_date=order.order_date,
        notes=f"إنتاج {order.quantity:g} {recipe.item.name} — أمر {order.order_number}",
    )

    order.cost = total_cost
    order.status = 'confirmed'
    order.save(update_fields=['cost', 'status', 'updated_at'])


@transaction.atomic
def cancel_manufacturing_order(order):
    if order.status != 'confirmed':
        raise ValueError('لا يمكن إلغاء أمر غير مؤكد')

    movements = StockMovement.objects.filter(
        tenant=order.tenant,
        reference_type='manufacturing_order',
        reference_id=order.id,
        is_reversal=False,
    ).select_related('item').select_for_update()

    for mv in movements:
        sq = _get_or_create_sq(order.tenant, order.stock, mv.item)
        if mv.direction == 'out':
            # component was deducted → restore it
            sq.quantity += mv.quantity
        else:
            # finished product was added → remove it
            if sq.quantity < mv.quantity:
                raise ValueError(f"لا يمكن عكس الأمر: رصيد «{mv.item.name}» غير كافٍ.")
            sq.quantity -= mv.quantity
        sq.save(update_fields=['quantity', 'updated_at'])
        flipped_dir = 'in' if mv.direction == 'out' else 'out'
        StockMovement.objects.create(
            tenant=order.tenant,
            item=mv.item,
            stock=mv.stock,
            movement_type='adjustment_in' if flipped_dir == 'in' else 'adjustment_out',
            direction=flipped_dir,
            quantity=mv.quantity,
            unit_cost=mv.unit_cost,
            balance_after=sq.quantity,
            reference_type=mv.reference_type,
            reference_id=mv.reference_id,
            movement_date=timezone.localdate(),
            notes=f'إلغاء أمر تصنيع: {mv.notes}' if mv.notes else 'إلغاء أمر تصنيع',
            is_reversal=True,
        )
    order.status = 'cancelled'
    order.save(update_fields=['status', 'updated_at'])
