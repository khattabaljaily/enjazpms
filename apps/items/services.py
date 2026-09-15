"""
Shared item-creation helpers used by more than one entry point (manual "add
product" form, bulk product import, catalog-based onboarding) so opening-
stock bookkeeping stays in one place. Mirrors the exact pattern already used
inline in apps/data_import/product_importer.py.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.sales.models import StockMovement
from apps.stocks.models import StockQuantity


def apply_opening_stock(tenant, item, stock, quantity: Decimal, batch_number: str = '', expiry_date=None):
    """
    Records an opening balance for a newly created item: sets the
    StockQuantity row (auto-created by the Item post_save signal), logs an
    'opening_in' StockMovement, and creates an ItemBatch if batch/expiry info
    was given. No-op if quantity/stock is falsy or the item is a service.
    """
    if not (quantity and stock) or item.item_type == 'service':
        return

    with transaction.atomic():
        sq = StockQuantity.objects.select_for_update().get(tenant=tenant, stock=stock, item=item)
        sq.quantity = quantity
        sq.opening_quantity = quantity
        sq.save(update_fields=['quantity', 'opening_quantity'])

        StockMovement.objects.create(
            tenant=tenant, item=item, stock=stock,
            movement_type='opening_in', direction='in',
            quantity=quantity, unit_cost=item.cost_price or Decimal('0'),
            movement_date=timezone.localdate(),
            reference_type='opening_balance', reference_id=sq.id,
            balance_after=sq.quantity, notes='رصيد افتتاحي',
        )

        if batch_number or expiry_date:
            from .models import ItemBatch
            ItemBatch.objects.create(
                tenant=tenant, item=item, stock=stock,
                batch_number=batch_number or '', expiry_date=expiry_date,
                quantity_received=quantity, quantity_remaining=quantity,
                purchase_date=timezone.localdate(),
            )
