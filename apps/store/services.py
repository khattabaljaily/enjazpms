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
# Prescription (pharmacy stores)
# ──────────────────────────────────────────────────────────────

PRESCRIPTION_MAX_BYTES = 5 * 1024 * 1024
# امتداد → بدايات المحتوى المقبولة (فحص توقيع الملف، لا نثق بالاسم ولا بالـ content-type)
_PRESCRIPTION_SIGNATURES = {
    '.jpg':  (b'\xff\xd8\xff',),
    '.jpeg': (b'\xff\xd8\xff',),
    '.png':  (b'\x89PNG\r\n\x1a\n',),
    '.webp': (b'RIFF',),
    '.pdf':  (b'%PDF',),
}


def validate_prescription_file(uploaded):
    """يرجع رسالة خطأ بالعربية، أو None إن كان الملف صالحاً (الحقل اختياري أصلاً)."""
    import os
    ext = os.path.splitext(uploaded.name or '')[1].lower()
    if ext not in _PRESCRIPTION_SIGNATURES:
        return 'صيغة الملف غير مدعومة. المسموح: صورة (JPG/PNG/WEBP) أو PDF.'
    if uploaded.size > PRESCRIPTION_MAX_BYTES:
        return 'حجم الملف أكبر من 5 ميجابايت.'
    head = uploaded.read(12)
    uploaded.seek(0)
    if not any(head.startswith(sig) for sig in _PRESCRIPTION_SIGNATURES[ext]) or \
            (ext == '.webp' and head[8:12] != b'WEBP'):
        return 'الملف تالف أو لا يطابق صيغته.'
    return None


# ──────────────────────────────────────────────────────────────
# Stock availability (المخزن الذي يخدم طلبات المتجر)
# ──────────────────────────────────────────────────────────────

def store_stock(tenant, branch=None):
    """
    المخزن الذي يخدم طلبات المتجر: المخزن الافتراضي للفرع المختار (أو أول مخازنه)؛
    بلا فروع: المخزن الافتراضي للمشترك. منه تُعرض الكميات ويُتحقق منها وتُصدر الفاتورة.
    """
    from apps.stocks.models import Stock
    stocks = Stock.objects.filter(tenant=tenant, is_active=True, is_central=False)
    if branch is not None:
        stocks = stocks.filter(branch=branch)
    return stocks.filter(is_default=True).first() or stocks.order_by('id').first()


def available_quantities(stock, item_ids):
    """{item_id: المتاح} = الرصيد − المحجوز في مخزن المتجر."""
    from apps.stocks.models import StockQuantity
    if stock is None:
        return {}
    rows = StockQuantity.objects.filter(stock=stock, item_id__in=list(item_ids)).values(
        'item_id', 'quantity', 'reserved_quantity')
    return {r['item_id']: (r['quantity'] or 0) - (r['reserved_quantity'] or 0) for r in rows}


def cart_shortages(stock, cart_items):
    """بنود السلة التي تتجاوز المتاح: [{'item', 'requested', 'available'}]."""
    available = available_quantities(stock, [r['item'].id for r in cart_items])
    shortages = []
    for row in cart_items:
        have = max(available.get(row['item'].id, Decimal('0')), Decimal('0'))
        if row['qty'] > have:
            shortages.append({'item': row['item'], 'requested': row['qty'], 'available': have})
    return shortages


def shortage_message(shortages):
    from apps.core.templatetags.number_format import qty
    parts = [f'«{s["item"].name}» (المتاح {qty(s["available"])})' for s in shortages]
    return 'الكمية المطلوبة غير متوفرة حالياً: ' + '، '.join(parts)


# ──────────────────────────────────────────────────────────────
# Place Order
# ──────────────────────────────────────────────────────────────

@transaction.atomic
def place_order(store: StoreSettings, cart_items: list, customer_data: dict, branch=None) -> OnlineOrder:
    """
    Create OnlineOrder + OnlineOrderLines.
    Sends a notification to the order's branch (or the tenant without branches).
    Returns the new OnlineOrder.
    """
    if store.tenant.is_enterprise() and branch is None:
        raise ValueError('اختر الفرع أولاً.')
    shortages = cart_shortages(store_stock(store.tenant, branch), cart_items)
    if shortages:
        raise ValueError(shortage_message(shortages))
    subtotal = sum(r['line_total'] for r in cart_items)

    order = OnlineOrder.objects.create(
        tenant          = store.tenant,
        store           = store,
        branch          = branch,
        customer_name   = customer_data['name'],
        customer_phone  = customer_data['phone'],
        customer_address= customer_data.get('address', ''),
        customer_notes  = customer_data.get('notes', ''),
        payment_method  = customer_data['payment_method'],
        prescription    = customer_data.get('prescription') or None,
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
            requires_prescription = bool(store.is_pharmacy and row['item'].requires_prescription),
        )

    _notify_new_order(order)
    return order


def _notify_new_order(order: OnlineOrder):
    """Create an in-system notification for the tenant."""
    from apps.notifications.models import Notification
    Notification.objects.create(
        tenant            = order.tenant,
        branch            = order.branch,   # الطلب يخص فرعه: هو من يقبله ويُصدر فاتورته
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
def approve_order(order: OnlineOrder, user=None) -> 'SaleInvoice':
    """
    1. Verify the branch store stock still covers every line.
    2. Find or create a Customer by phone number.
    3. Create a SaleInvoice (draft) with all lines, from the order's branch.
    4. Link OnlineOrder.sale_invoice and mark as approved.
    """
    from apps.customers.models import Customer
    from apps.sales.models import SaleInvoice, SaleInvoiceLine

    tenant = order.tenant

    # ── Store stock ───────────────────────────────────────────
    # طلب فرع محدد يُخدَّم من مخزن ذلك الفرع فقط، وبنفس المخزن الذي عرض الكميات للزبون.
    stock = store_stock(tenant, order.branch)
    if stock is None:
        raise ValueError('لا يوجد مخزن فعّال لفرع الطلب')
    # بلا حجز: الكمية قد تُباع في الفرع بعد الطلب، فنتحقق مجدداً عند القبول.
    lines = list(order.lines.select_related('item'))
    shortages = cart_shortages(stock, [{'item': ln.item, 'qty': ln.quantity} for ln in lines])
    if shortages:
        raise ValueError(shortage_message(shortages) + ' — عدّل الطلب مع الزبون أو ارفضه.')

    # ── Customer ─────────────────────────────────────────────
    # في نسخة المؤسسات يتبع زبون المتجر فرعَ المخزن الذي يخدم الطلب.
    # البحث داخل فرع المخزن فقط (لا نربط الطلب بعميل فرع آخر)، والهاتف ليس
    # فريداً، فنأخذ أقدم تطابق بدل get_or_create الذي يفشل عند التكرار.
    branch_id = stock.branch_id if stock else None
    customer = (Customer.objects.filter(tenant=tenant, phone=order.customer_phone, branch_id=branch_id)
                .order_by('id').first())
    if customer is None:
        customer = Customer.objects.create(
            tenant=tenant, phone=order.customer_phone, name=order.customer_name, branch_id=branch_id,
        )
    if customer.name != order.customer_name and not customer.name:
        customer.name = order.customer_name
        customer.save(update_fields=['name'])

    # ── Invoice ───────────────────────────────────────────────
    invoice = SaleInvoice.objects.create(
        tenant         = tenant,
        customer       = customer,
        stock          = stock,
        invoice_date   = timezone.localdate(),
        payment_method = order.payment_method,
        status         = 'draft',
        created_by     = user,
        updated_by     = user,
        notes          = (
            f'طلب أونلاين {order.order_number}'
            + (f' — {order.customer_notes}' if order.customer_notes else '')
        ),
    )

    # ── Lines ─────────────────────────────────────────────────
    from decimal import Decimal as D
    running_subtotal = D('0')
    for line in lines:
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
