"""
Store Views
===========
Public  (no login):  storefront, cart ops, checkout, order_confirm
Manage  (login req): settings, orders list, order detail/approve/reject
"""
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.db.models import Q

from .models import OnlineOrder, StoreSettings
from .services import (
    approve_order, cart_add, cart_clear, cart_remove,
    cart_update, get_cart, get_cart_items, place_order, reject_order,
)


# ══════════════════════════════════════════════════════════════
# PUBLIC — helpers
# ══════════════════════════════════════════════════════════════

def _get_store(slug: str, require_enabled: bool = True):
    """Return store or render 'store unavailable' response."""
    store = get_object_or_404(StoreSettings, slug=slug)
    if require_enabled and not store.is_enabled:
        from django.template.loader import render_to_string
        from django.http import HttpResponse
        # Return a sentinel so callers can short-circuit
        store._disabled = True
    return store


def _store_disabled_response(store):
    from django.shortcuts import render as _render
    from django.http import HttpResponse
    html = f"""<!DOCTYPE html><html lang="ar" dir="rtl">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{store.display_name}</title>
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.rtl.min.css" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
:root{{--brand:{store.accent_color};}}
body{{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:2rem;}}
.box{{text-align:center;max-width:420px;}}
.icon{{font-size:3.5rem;margin-bottom:1.5rem;opacity:.7;}}
h2{{font-size:1.6rem;font-weight:800;margin-bottom:.75rem;}}
p{{color:#94a3b8;font-size:.95rem;line-height:1.7;}}
.badge-closed{{display:inline-flex;align-items:center;gap:.5rem;background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.3);
color:#fca5a5;border-radius:30px;padding:.4rem 1.1rem;font-size:.82rem;font-weight:700;margin-bottom:1.5rem;}}
</style></head><body>
<div class="box">
  <div class="icon">🏪</div>
  <div class="badge-closed"><i class="fas fa-lock"></i> المتجر غير متاح حالياً</div>
  <h2>{store.display_name}</h2>
  <p>هذا المتجر غير متاح للعرض في الوقت الحالي.<br>يرجى المحاولة لاحقاً.</p>
  {'<p><a href="https://wa.me/'+store.whatsapp+'" style="color:#25d366;font-weight:700;"><i class="fab fa-whatsapp"></i> تواصل معنا</a></p>' if store.whatsapp else ''}
</div></body></html>"""
    return HttpResponse(html, status=503)


def _get_products(store: StoreSettings):
    from apps.stocks.models import StockQuantity
    from apps.items.models import Item

    qs = Item.objects.filter(
        tenant=store.tenant,
        is_active=True,
        is_sellable=True,
        item_type__in=['product', 'semi_finished'],
    ).select_related('category', 'unit')

    if not store.show_out_of_stock:
        # keep items with at least one positive StockQuantity
        in_stock_ids = (
            StockQuantity.objects
            .filter(tenant=store.tenant, quantity__gt=0)
            .values_list('item_id', flat=True)
        )
        qs = qs.filter(id__in=in_stock_ids)

    return qs


# ══════════════════════════════════════════════════════════════
# PUBLIC — Storefront
# ══════════════════════════════════════════════════════════════

def storefront(request, slug):
    store = _get_store(slug)
    if getattr(store, '_disabled', False):
        return _store_disabled_response(store)
    products = _get_products(store)

    search = request.GET.get('q', '').strip()
    if search:
        products = products.filter(
            Q(name__icontains=search) | Q(barcode__icontains=search)
        )

    category_id = request.GET.get('cat')
    if category_id:
        products = products.filter(category_id=category_id)

    from apps.items.models import Category
    categories = Category.objects.filter(
        tenant=store.tenant, is_active=True
    ).order_by('display_order', 'name')

    cart      = get_cart(request, slug)
    cart_count = sum(cart.values()) if cart else 0

    # Build stock-quantity map if the store shows quantities
    stock_qty_map = {}
    if store.show_stock_quantity:
        from apps.stocks.models import StockQuantity
        from django.db.models import Sum
        qs = (
            StockQuantity.objects
            .filter(tenant=store.tenant, item__in=products)
            .values('item_id')
            .annotate(total=Sum('quantity'))
        )
        stock_qty_map = {r['item_id']: r['total'] for r in qs}

    return render(request, 'store/storefront.html', {
        'store':         store,
        'products':      products,
        'categories':    categories,
        'cart_count':    int(cart_count),
        'search':        search,
        'category_id':   category_id,
        'status':        store.get_status(),
        'stock_qty_map': stock_qty_map,
    })


# ══════════════════════════════════════════════════════════════
# PUBLIC — Cart (AJAX + page)
# ══════════════════════════════════════════════════════════════

@require_POST
def cart_add_view(request, slug):
    store = _get_store(slug)
    if getattr(store, '_disabled', False):
        return _store_disabled_response(store)
    item_id = request.POST.get('item_id')
    qty     = float(request.POST.get('qty', 1))
    cart_add(request, slug, int(item_id), qty)
    cart        = get_cart(request, slug)
    cart_count  = int(sum(cart.values()))
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'cart_count': cart_count})
    return redirect('store:cart', slug=slug)


@require_POST
def cart_remove_view(request, slug):
    item_id = int(request.POST.get('item_id'))
    cart_remove(request, slug, item_id)
    return redirect('store:cart', slug=slug)


@require_POST
def cart_update_view(request, slug):
    item_id = int(request.POST.get('item_id'))
    qty     = float(request.POST.get('qty', 0))
    cart_update(request, slug, item_id, qty)
    return redirect('store:cart', slug=slug)


def cart_view(request, slug):
    store = _get_store(slug)
    if getattr(store, '_disabled', False):
        return _store_disabled_response(store)
    cart       = get_cart(request, slug)
    cart_items = get_cart_items(slug, cart)
    subtotal   = sum(r['line_total'] for r in cart_items)
    return render(request, 'store/cart.html', {
        'store':      store,
        'cart_items': cart_items,
        'subtotal':   subtotal,
    })


# ══════════════════════════════════════════════════════════════
# PUBLIC — Checkout
# ══════════════════════════════════════════════════════════════

def checkout_view(request, slug):
    store = _get_store(slug)
    if getattr(store, '_disabled', False):
        return _store_disabled_response(store)
    cart       = get_cart(request, slug)
    cart_items = get_cart_items(slug, cart)

    if not cart_items:
        return redirect('store:storefront', slug=slug)

    subtotal = sum(r['line_total'] for r in cart_items)

    if request.method == 'POST':
        name   = request.POST.get('name', '').strip()
        phone  = request.POST.get('phone', '').strip()
        pm     = request.POST.get('payment_method', 'bank')
        addr   = request.POST.get('address', '').strip()
        notes  = request.POST.get('notes', '').strip()

        errors = {}
        if not name:
            errors['name'] = 'الاسم مطلوب'
        if not phone:
            errors['phone'] = 'رقم الهاتف مطلوب'
        if pm not in ('bank', 'credit'):
            errors['payment_method'] = 'طريقة دفع غير صالحة'
        if store.min_order_amount > 0 and subtotal < store.min_order_amount:
            errors['min_order'] = f'الحد الأدنى للطلب {store.min_order_amount:,.0f}'

        if not errors:
            order = place_order(
                store=store,
                cart_items=cart_items,
                customer_data={
                    'name':           name,
                    'phone':          phone,
                    'address':        addr,
                    'notes':          notes,
                    'payment_method': pm,
                },
            )
            cart_clear(request, slug)
            return redirect('store:order_confirm', slug=slug, token=order.token)

        return render(request, 'store/checkout.html', {
            'store':      store,
            'cart_items': cart_items,
            'subtotal':   subtotal,
            'errors':     errors,
            'post':       request.POST,
        })

    return render(request, 'store/checkout.html', {
        'store':      store,
        'cart_items': cart_items,
        'subtotal':   subtotal,
    })


# ══════════════════════════════════════════════════════════════
# PUBLIC — Order Confirmation
# ══════════════════════════════════════════════════════════════

def order_confirm_view(request, slug, token):
    store = _get_store(slug, require_enabled=False)  # allow even if disabled after order placed
    order = get_object_or_404(OnlineOrder, token=token, store=store)
    return render(request, 'store/order_confirm.html', {
        'store': store,
        'order': order,
    })


# ══════════════════════════════════════════════════════════════
# MANAGE — Store Settings
# ══════════════════════════════════════════════════════════════

@login_required
def manage_settings(request):
    from .models import DAYS_AR, DAYS_ORDER, DEFAULT_HOURS
    tenant = request.tenant
    if not tenant:
        return redirect('/')

    store, _ = StoreSettings.objects.get_or_create(
        tenant=tenant,
        defaults={'display_name': tenant.name},
    )

    if request.method == 'POST':
        store.is_enabled        = request.POST.get('is_enabled') == 'on'
        store.display_name      = request.POST.get('display_name', '').strip() or tenant.name
        store.description       = request.POST.get('description', '').strip()
        store.accent_color      = request.POST.get('accent_color', '#6366f1').strip()
        store.whatsapp          = request.POST.get('whatsapp', '').strip()
        store.show_out_of_stock   = request.POST.get('show_out_of_stock') == 'on'
        store.show_prices         = request.POST.get('show_prices') == 'on'
        store.show_stock_quantity = request.POST.get('show_stock_quantity') == 'on'
        store.min_order_amount    = request.POST.get('min_order_amount') or 0
        store.bank_details      = request.POST.get('bank_details', '').strip()
        store.delivery_message  = request.POST.get('delivery_message', '').strip()
        store.status_override   = request.POST.get('status_override', 'auto')
        store.open_message      = request.POST.get('open_message', '').strip()
        store.closed_message    = request.POST.get('closed_message', '').strip()

        # Working hours
        wh = {}
        for day in DAYS_ORDER:
            wh[day] = {
                'enabled': request.POST.get(f'day_{day}_enabled') == 'on',
                'open':    request.POST.get(f'day_{day}_open', '08:00'),
                'close':   request.POST.get(f'day_{day}_close', '22:00'),
            }
        store.working_hours = wh

        new_slug = request.POST.get('slug', '').strip()
        if new_slug and new_slug != store.slug:
            if not StoreSettings.objects.filter(slug=new_slug).exclude(pk=store.pk).exists():
                store.slug = new_slug

        if 'cover_image' in request.FILES:
            store.cover_image = request.FILES['cover_image']

        store.save()
        messages.success(request, 'تم حفظ إعدادات المتجر بنجاح.')
        return redirect('store:manage_settings')

    pending_count = OnlineOrder.objects.filter(tenant=tenant, status='pending').count()
    hours = store.working_hours or DEFAULT_HOURS

    return render(request, 'store/manage_settings.html', {
        'store':         store,
        'pending_count': pending_count,
        'status':        store.get_status(),
        'hours':         hours,
        'days':          [(d, DAYS_AR[d]) for d in DAYS_ORDER],
    })


# ══════════════════════════════════════════════════════════════
# MANAGE — Orders List
# ══════════════════════════════════════════════════════════════

@login_required
def manage_orders(request):
    tenant = request.tenant
    if not tenant:
        return redirect('/')

    status_filter = request.GET.get('status', 'pending')
    orders = OnlineOrder.objects.filter(tenant=tenant).select_related('store', 'sale_invoice')

    if status_filter in ('pending', 'approved', 'rejected'):
        orders = orders.filter(status=status_filter)

    counts = {
        'pending':  OnlineOrder.objects.filter(tenant=tenant, status='pending').count(),
        'approved': OnlineOrder.objects.filter(tenant=tenant, status='approved').count(),
        'rejected': OnlineOrder.objects.filter(tenant=tenant, status='rejected').count(),
    }

    return render(request, 'store/manage_orders.html', {
        'orders':        orders,
        'status_filter': status_filter,
        'counts':        counts,
    })


# ══════════════════════════════════════════════════════════════
# MANAGE — Order Detail
# ══════════════════════════════════════════════════════════════

@login_required
def manage_order_detail(request, pk):
    tenant = request.tenant
    order  = get_object_or_404(OnlineOrder, pk=pk, tenant=tenant)
    lines  = order.lines.select_related('item').all()
    return render(request, 'store/manage_order_detail.html', {
        'order': order,
        'lines': lines,
    })


# ══════════════════════════════════════════════════════════════
# MANAGE — Approve / Reject (AJAX POST)
# ══════════════════════════════════════════════════════════════

@login_required
@require_POST
def manage_order_approve(request, pk):
    tenant = request.tenant
    order  = get_object_or_404(OnlineOrder, pk=pk, tenant=tenant, status='pending')
    try:
        invoice = approve_order(order)
        return JsonResponse({
            'success':    True,
            'invoice_id': invoice.pk,
            'invoice_url': f'/sales/{invoice.pk}/',
            'message':   f'تم إنشاء الفاتورة {invoice.invoice_number} بنجاح.',
        })
    except Exception as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)


@login_required
@require_POST
def manage_order_reject(request, pk):
    tenant = request.tenant
    order  = get_object_or_404(OnlineOrder, pk=pk, tenant=tenant, status='pending')
    reject_order(order)
    return JsonResponse({'success': True})
