from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from apps.sales.models import StockMovement
from .models import StockQuantity, StockTransfer, StockTransferLine, Stocktake, StocktakeLine


def _get_sq(tenant, stock, item):
    return StockQuantity.objects.select_for_update().get(
        tenant=tenant, stock=stock, item=item
    )


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

        StockMovement.objects.filter(
            tenant=tenant,
            reference_type='stock_transfer',
            reference_id=transfer.id,
            item=item,
        ).delete()

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

        StockMovement.objects.create(
            tenant=tenant, item=item, stock=stocktake.stock,
            movement_type=mv_type, direction=direction,
            quantity=abs(diff), unit_cost=Decimal('0'),
            movement_date=stocktake.stocktake_date,
            reference_type='stocktake', reference_id=stocktake.id,
            balance_after=sq.quantity,
        )

    stocktake.status = 'confirmed'
    stocktake.save(update_fields=['status', 'updated_at'])
