"""
Store Services
==============
place_order   — create OnlineOrder + lines, send notification
approve_order — create SaleInvoice (draft) from an OnlineOrder
reject_order  — mark order as rejected, notify if needed
cart helpers  — session-based cart CRUD
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone

from .models import OnlineOrder, OnlineOrderLine, StoreSettings


# ──────────────────────────────────────────────────────────────
# Cart helpers (session-based)
# ──────────────────────────────────────────────────────────────

def get_cart(request, slug: str) -> dict:
    """Return cart dict  {str(item_id): quantity_float}."""
    return request.session.get(f'cart_{slug}', {})


def save_cart(request, slug: str, cart: dict):
    request.session[f'cart_{slug}'] = cart
    request.session.modified = True


def cart_add(request, slug: str, item_id: int, quantity: float = 1):
    cart = get_cart(request, slug)
    key  = str(item_id)
    cart[key] = cart.get(key, 0) + quantity
    save_cart(request, slug, cart)


def cart_remove(request, slug: str, item_id: int):
    cart = get_cart(request, slug)
    cart.pop(str(item_id), None)
    save_cart(request, slug, cart)


def cart_update(request, slug: str, item_id: int, quantity: float):
    cart = get_cart(request, slug)
    key  = str(item_id)
    if quantity <= 0:
        cart.pop(key, None)
    else:
        cart[key] = quantity
    save_cart(request, slug, cart)


def cart_clear(request, slug: str):
    save_cart(request, slug, {})


def get_cart_items(slug: str, cart: dict) -> list:
    """
    Resolve item objects from cart dict.
    Returns list of dicts with item, qty, unit_price, line_total.
    """
    from apps.items.models import Item
    if not cart:
        return []
    ids   = [int(k) for k in cart]
    items = {i.id: i for i in Item.objects.filter(id__in=ids, is_active=True, is_sellable=True)}
    result = []
    for item_id_str, qty in cart.items():
        item = items.get(int(item_id_str))
        if not item:
            continue
        qty        = Decimal(str(qty))
        unit_price = item.selling_price
        result.append({
            'item':       item,
            'qty':        qty,
            'unit_price': unit_price,
            'line_total': (unit_price * qty).quantize(Decimal('0.01')),
        })
    return result


# ──────────────────────────────────────────────────────────────
# Place Order
# ──────────────────────────────────────────────────────────────

@transaction.atomic
def place_order(store: StoreSettings, cart_items: list, customer_data: dict) -> OnlineOrder:
    """
    Create OnlineOrder + OnlineOrderLines.
    Sends a notification to the tenant.
    Returns the new OnlineOrder.
    """
    subtotal = sum(r['line_total'] for r in cart_items)

    order = OnlineOrder.objects.create(
        tenant          = store.tenant,
        store           = store,
        customer_name   = customer_data['name'],
        customer_phone  = customer_data['phone'],
        customer_address= customer_data.get('address', ''),
        customer_notes  = customer_data.get('notes', ''),
        payment_method  = customer_data['payment_method'],
        subtotal        = subtotal,
        total_amount    = subtotal,
    )

    for row in cart_items:
        OnlineOrderLine.objects.create(
            tenant     = store.tenant,
            order      = order,
            item       = row['item'],
            item_name  = row['item'].name,
            unit_price = row['unit_price'],
            quantity   = row['qty'],
        )

    _notify_new_order(order)
    return order


def _notify_new_order(order: OnlineOrder):
    """Create an in-system notification for the tenant."""
    from apps.notifications.models import Notification
    Notification.objects.create(
        tenant            = order.tenant,
        notification_type = 'online_order',
        priority          = 'high',
        title             = f'طلب جديد من المتجر: {order.order_number}',
        message           = (
            f'العميل {order.customer_name} ({order.customer_phone}) '
            f'أرسل طلباً بقيمة {order.total_amount:,.2f} '
            f'— طريقة الدفع: {order.get_payment_method_display()}.'
        ),
        link = f'/store/manage/orders/{order.pk}/',
    )


# ──────────────────────────────────────────────────────────────
# Approve Order → create SaleInvoice (draft)
# ──────────────────────────────────────────────────────────────

@transaction.atomic
def approve_order(order: OnlineOrder) -> 'SaleInvoice':
    """
    1. Find or create a Customer by phone number.
    2. Create a SaleInvoice (draft) with all lines.
    3. Link OnlineOrder.sale_invoice and mark as approved.
    """
    from apps.customers.models import Customer
    from apps.sales.models import SaleInvoice, SaleInvoiceLine
    from apps.stocks.models import Stock

    tenant = order.tenant

    # ── Customer ─────────────────────────────────────────────
    customer, _ = Customer.objects.get_or_create(
        tenant=tenant,
        phone=order.customer_phone,
        defaults={'name': order.customer_name},
    )
    if customer.name != order.customer_name and not customer.name:
        customer.name = order.customer_name
        customer.save(update_fields=['name'])

    # ── Default stock ─────────────────────────────────────────
    stock = (
        Stock.objects.filter(tenant=tenant, is_default=True, is_active=True).first()
        or Stock.objects.filter(tenant=tenant, is_active=True).first()
    )

    # ── Invoice ───────────────────────────────────────────────
    invoice = SaleInvoice.objects.create(
        tenant         = tenant,
        customer       = customer,
        stock          = stock,
        invoice_date   = timezone.localdate(),
        payment_method = order.payment_method,
        status         = 'draft',
        notes          = (
            f'طلب أونلاين {order.order_number}'
            + (f' — {order.customer_notes}' if order.customer_notes else '')
        ),
    )

    # ── Lines ─────────────────────────────────────────────────
    from decimal import Decimal as D
    running_subtotal = D('0')
    for line in order.lines.select_related('item').all():
        sale_line = SaleInvoiceLine(
            tenant            = tenant,
            invoice           = invoice,
            item              = line.item,
            quantity          = line.quantity,
            unit_price        = line.unit_price,
            cost_price_snapshot = line.item.cost_price,
        )
        sale_line.calculate()
        sale_line.save()
        running_subtotal += sale_line.line_subtotal

    # Set totals directly from in-memory computed values (avoids re-query)
    invoice.subtotal              = running_subtotal
    invoice.invoice_discount_amount = D('0')
    invoice.tax_amount            = D('0')
    invoice.grand_total           = running_subtotal
    invoice.save()

    # ── Link ──────────────────────────────────────────────────
    order.sale_invoice = invoice
    order.status       = 'approved'
    order.save(update_fields=['sale_invoice', 'status', 'updated_at'])

    return invoice


# ──────────────────────────────────────────────────────────────
# Reject Order
# ──────────────────────────────────────────────────────────────

@transaction.atomic
def reject_order(order: OnlineOrder):
    order.status = 'rejected'
    order.save(update_fields=['status', 'updated_at'])
