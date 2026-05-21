"""
بوابة العميل — Customer Portal Views
المسار الأساسي: /portal/
المصادقة: session-based (portal_customer_id / portal_tenant_id)
"""

from functools import wraps

from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

# Session keys
_SESS_CUSTOMER = 'portal_customer_id'
_SESS_TENANT   = 'portal_tenant_id'


def _portal_required(view_fn):
    @wraps(view_fn)
    def _inner(request, *args, **kwargs):
        if not request.session.get(_SESS_CUSTOMER):
            return redirect('portal:login_error')
        return view_fn(request, *args, **kwargs)
    return _inner


def _get_portal_ctx(request):
    """Return (customer, tenant) from session, or (None, None)."""
    from apps.customers.models import Customer
    from apps.core.models import Tenant
    cid = request.session.get(_SESS_CUSTOMER)
    tid = request.session.get(_SESS_TENANT)
    if not cid or not tid:
        return None, None
    try:
        tenant   = Tenant.objects.get(pk=tid)
        customer = Customer.objects.get(pk=cid, tenant=tenant, is_active=True)
        return customer, tenant
    except Exception:
        return None, None


# ── Login / Logout ────────────────────────────────────────────────

def login_via_token(request, token):
    from apps.customers.models import Customer
    try:
        customer = (
            Customer.objects
            .select_related('tenant')
            .get(portal_token=token, is_active=True)
        )
    except Customer.DoesNotExist:
        return render(request, 'portal/error.html', {
            'title': 'رابط غير صالح',
            'message': 'الرابط الذي استخدمته غير صالح أو تم إلغاؤه.',
        })

    if not customer.portal_token_valid:
        return render(request, 'portal/error.html', {
            'title': 'انتهت صلاحية الرابط',
            'message': 'انتهت صلاحية هذا الرابط. تواصل مع المتجر للحصول على رابط جديد.',
        })

    request.session[_SESS_CUSTOMER] = customer.pk
    request.session[_SESS_TENANT]   = customer.tenant_id
    request.session.set_expiry(60 * 60 * 24 * 30)  # 30 days
    return redirect('portal:dashboard')


def portal_logout(request):
    request.session.pop(_SESS_CUSTOMER, None)
    request.session.pop(_SESS_TENANT,   None)
    return render(request, 'portal/error.html', {
        'title': 'تم تسجيل الخروج',
        'message': 'لقد خرجت من البوابة بنجاح.',
        'icon': 'fa-door-open',
        'logged_out': True,
    })


def portal_login_error(request):
    return render(request, 'portal/error.html', {
        'title': 'الوصول مرفوض',
        'message': 'يرجى استخدام الرابط الذي أرسله لك المتجر لتسجيل الدخول.',
    })


# ── Dashboard ─────────────────────────────────────────────────────

@_portal_required
def portal_dashboard(request):
    customer, tenant = _get_portal_ctx(request)
    if not customer:
        return redirect('portal:login_error')

    from apps.sales.models import CustomerLedger, SaleInvoice

    ledger_total = (
        CustomerLedger.objects
        .filter(tenant=tenant, customer=customer)
        .aggregate(s=Sum('amount'))['s'] or 0
    )
    current_balance = (customer.opening_balance or 0) + ledger_total

    qs_confirmed = SaleInvoice.objects.filter(
        tenant=tenant, customer=customer, status='confirmed'
    )
    total_invoices  = qs_confirmed.count()
    unpaid_invoices = qs_confirmed.filter(paid_amount__lt=F('grand_total')).count()
    recent_invoices = qs_confirmed.order_by('-invoice_date')[:5]

    return render(request, 'portal/dashboard.html', {
        'customer': customer,
        'tenant': tenant,
        'current_balance': current_balance,
        'total_invoices': total_invoices,
        'unpaid_invoices': unpaid_invoices,
        'recent_invoices': recent_invoices,
    })


# ── Invoices List ─────────────────────────────────────────────────

@_portal_required
def portal_invoices(request):
    customer, tenant = _get_portal_ctx(request)
    if not customer:
        return redirect('portal:login_error')

    from apps.sales.models import SaleInvoice

    invoices = (
        SaleInvoice.objects
        .filter(tenant=tenant, customer=customer, status='confirmed')
        .order_by('-invoice_date')
    )

    return render(request, 'portal/invoices.html', {
        'customer': customer,
        'tenant': tenant,
        'invoices': invoices,
    })


# ── Invoice Detail ────────────────────────────────────────────────

@_portal_required
def portal_invoice_detail(request, pk):
    customer, tenant = _get_portal_ctx(request)
    if not customer:
        return redirect('portal:login_error')

    from apps.sales.models import SaleInvoice

    invoice = get_object_or_404(
        SaleInvoice,
        pk=pk, tenant=tenant, customer=customer, status='confirmed',
    )
    lines = invoice.lines.select_related('item', 'variant', 'unit').all()

    return render(request, 'portal/invoice_detail.html', {
        'customer': customer,
        'tenant': tenant,
        'invoice': invoice,
        'lines': lines,
    })
