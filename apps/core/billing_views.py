"""
لوحة إدارة المنصة — المدفوعات اليدوية وإيصالاتها، وإشعارات المنصة للمشتركين.

- الدفع يُسجَّل يدوياً من مدير المنصة بعد استلام المبلغ خارج النظام، ويمدّد اشتراك المشترك.
- الإيصال PDF بترويسة إنجاز؛ إرساله للمشترك بالبريد يدوي أيضاً (زر «إرسال بالبريد»).
- إشعار المنصة يُنشأ كإشعار `platform` لكل مشترك مستهدف فيظهر في جرس كل مستخدميه.
"""
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import branch_scope_exempt

from . import doc_verify
from .models import PlatformBroadcast, SubscriptionInvoice, SubscriptionPayment, Tenant
from .brand_docs import PdfRenderError, render_invoice_pdf, render_receipt_pdf

_EXEMPT = 'لوحة إدارة المنصة (platform admin) — عبر كل المشتركين، لا مفهوم فرع هنا أصلاً'


def _forbidden():
    return JsonResponse({'success': False, 'message': 'غير مصرح'}, status=403)


def _body(request):
    try:
        return json.loads(request.body or '{}')
    except (json.JSONDecodeError, ValueError):
        return None


def _extend_subscription(tenant, plan, days, today):
    """يمدّد الاشتراك كما يفعل تجديد المشترك: من نهايته الحالية إن كان ساريًا وإلا من اليوم."""
    start = tenant.subscription_expires if (
        tenant.subscription_expires and tenant.subscription_expires >= today) else today
    end = start + timedelta(days=days)
    tenant.subscription_expires = end
    tenant.subscription_plan = plan
    fields = ['subscription_expires', 'subscription_plan', 'updated_at']
    if not tenant.is_active:
        tenant.is_active = True
        fields.append('is_active')
    tenant.save(update_fields=fields)
    return start, end


# ══════════════════════════ المدفوعات ══════════════════════════

@login_required
@branch_scope_exempt(_EXEMPT)
def admin_payments(request):
    if not request.user.has_platform_perm('manage_billing'):
        return redirect('core:no_permission')

    qs = SubscriptionPayment.objects.select_related('tenant', 'created_by')
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(tenant__name__icontains=q) | Q(receipt_number__icontains=q) | Q(reference__icontains=q))
    tenant_id = request.GET.get('tenant')
    if tenant_id and tenant_id.isdigit():
        qs = qs.filter(tenant_id=int(tenant_id))

    today = dj_timezone.localdate()
    month_start = today.replace(day=1)
    valid = SubscriptionPayment.objects.filter(voided_at__isnull=True)

    def _sum(qs_, cur):
        return qs_.filter(currency=cur).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    prefill = None
    if (request.GET.get('invoice') or '').isdigit():
        inv = SubscriptionInvoice.objects.filter(
            pk=int(request.GET['invoice']), voided_at__isnull=True, payment__isnull=True).first()
        if inv:
            prefill = {'invoice_id': inv.pk, 'number': inv.invoice_number, 'tenant_id': inv.tenant_id,
                       'plan': inv.plan, 'amount': str(inv.amount), 'currency': inv.currency,
                       'days': inv.period_days}
    return render(request, 'core/admin_payments.html', {
        'prefill': prefill,
        'payments': qs[:200],
        'q': q,
        'tenants': Tenant.objects.order_by('name').only('id', 'name', 'subscription_plan', 'email'),
        'plans': Tenant.SUBSCRIPTION_PLANS,
        'methods': SubscriptionPayment.METHOD_CHOICES,
        'currencies': SubscriptionPayment.CURRENCY_CHOICES,
        'today': today,
        'month_usd': _sum(valid.filter(paid_at__gte=month_start), 'USD'),
        'month_sdg': _sum(valid.filter(paid_at__gte=month_start), 'SDG'),
        'total_usd': _sum(valid, 'USD'),
        'total_sdg': _sum(valid, 'SDG'),
        'payments_count': valid.count(),
    })


@login_required
@require_POST
def admin_payment_create_api(request):
    if not request.user.has_platform_perm('manage_billing'):
        return _forbidden()
    data = _body(request)
    if data is None:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    tenant = Tenant.objects.filter(pk=data.get('tenant_id') or 0).first()
    if not tenant:
        return JsonResponse({'success': False, 'message': 'اختر المشترك'}, status=400)
    try:
        amount = Decimal(str(data.get('amount')))
    except (InvalidOperation, TypeError):
        amount = Decimal('0')
    if amount <= 0 or amount >= Decimal('1000000000'):
        return JsonResponse({'success': False, 'message': 'المبلغ غير صالح'}, status=400)
    try:
        days = int(data.get('days'))
    except (TypeError, ValueError):
        days = 0
    if not 0 < days <= 3650:
        return JsonResponse({'success': False, 'message': 'مدة الاشتراك غير صالحة'}, status=400)
    plan = data.get('plan')
    if plan not in dict(Tenant.SUBSCRIPTION_PLANS):
        return JsonResponse({'success': False, 'message': 'اختر الباقة'}, status=400)
    currency = data.get('currency') or 'USD'
    method = data.get('method') or 'bank'
    if currency not in dict(SubscriptionPayment.CURRENCY_CHOICES) or method not in dict(SubscriptionPayment.METHOD_CHOICES):
        return JsonResponse({'success': False, 'message': 'العملة أو طريقة الدفع غير صالحة'}, status=400)

    invoice = None
    if data.get('invoice_id'):
        invoice = SubscriptionInvoice.objects.filter(pk=data.get('invoice_id'), tenant=tenant).first()
        if not invoice or invoice.is_void or invoice.is_paid:
            return JsonResponse({'success': False, 'message': 'المطالبة غير موجودة أو مسدَّدة/ملغاة'}, status=400)

    today = dj_timezone.localdate()
    paid_at = parse_date(str(data.get('paid_at') or '')) or today
    if paid_at > today:
        return JsonResponse({'success': False, 'message': 'تاريخ الدفع في المستقبل'}, status=400)

    with transaction.atomic():
        tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
        start, end = _extend_subscription(tenant, plan, days, today)
        payment = SubscriptionPayment.objects.create(
            tenant=tenant,
            receipt_number=SubscriptionPayment.next_receipt_number(today.year),
            amount=amount, currency=currency, method=method,
            reference=(data.get('reference') or '').strip()[:120],
            paid_at=paid_at, plan=plan, period_days=days,
            period_start=start, period_end=end,
            notes=(data.get('notes') or '').strip(),
            created_by=request.user,
        )
        if invoice:
            invoice.payment = payment
            invoice.save(update_fields=['payment'])
    return JsonResponse({
        'success': True,
        'message': f'سُجّلت الدفعة وصدر الإيصال {payment.receipt_number}؛ الاشتراك ممدّد حتى {end:%Y-%m-%d}',
        'id': payment.pk,
    })


@login_required
@branch_scope_exempt(_EXEMPT)
def admin_payment_receipt(request, pk):
    if not request.user.has_platform_perm('manage_billing'):
        return redirect('core:no_permission')
    payment = get_object_or_404(SubscriptionPayment.objects.select_related('tenant'), pk=pk)
    try:
        pdf = render_receipt_pdf(payment)
    except PdfRenderError as exc:
        return HttpResponse(str(exc), status=500, content_type='text/plain; charset=utf-8')
    resp = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    resp['Content-Disposition'] = f'{disposition}; filename="{payment.receipt_number}.pdf"'
    return resp


@login_required
@require_POST
def admin_payment_email_api(request, pk):
    """إرسال الإيصال بالبريد يدوياً (إلى بريد المشترك أو بريد يحدده المدير)."""
    if not request.user.has_platform_perm('manage_billing'):
        return _forbidden()
    payment = get_object_or_404(SubscriptionPayment.objects.select_related('tenant'), pk=pk)
    data = _body(request) or {}
    to = (data.get('email') or payment.tenant.email or '').strip()
    if not to or '@' not in to:
        return JsonResponse({'success': False, 'message': 'لا يوجد بريد للمشترك — اكتب بريداً صالحاً'}, status=400)
    if payment.is_void:
        return JsonResponse({'success': False, 'message': 'الإيصال ملغى'}, status=400)

    mail = EmailMessage(
        subject=f'إيصال استلام اشتراك {payment.receipt_number} — إنجاز',
        body=(f'مرحباً،\n\nنشكركم على ثقتكم بإنجاز. نرفق إيصال استلام اشتراككم ({payment.get_plan_display()}) '
              f'للفترة من {payment.period_start:%Y/%m/%d} إلى {payment.period_end:%Y/%m/%d}.\n\n'
              'فريق إنجاز\ninfo@enjaztechnology.com'),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    try:
        mail.attach(f'{payment.receipt_number}.pdf', render_receipt_pdf(payment), 'application/pdf')
        mail.send(fail_silently=False)
    except Exception as exc:  # SMTP غير مضبوط أو رُفض — يُعرض السبب للمدير ولا يُسجَّل كمُرسَل
        return JsonResponse({'success': False, 'message': f'تعذّر الإرسال: {exc}'}, status=502)
    payment.emailed_at = dj_timezone.now()
    payment.emailed_to = to
    payment.save(update_fields=['emailed_at', 'emailed_to'])
    return JsonResponse({'success': True, 'message': f'أُرسل الإيصال إلى {to}'})


@login_required
@require_POST
def admin_payment_void_api(request, pk):
    """إلغاء إيصال (للتصحيح). لا يُعدَّل اشتراك المشترك تلقائياً — يُعدَّل يدوياً من إدارة المشتركين إن لزم."""
    if not request.user.has_platform_perm('manage_billing'):
        return _forbidden()
    payment = get_object_or_404(SubscriptionPayment, pk=pk)
    if not payment.is_void:
        payment.voided_at = dj_timezone.now()
        payment.save(update_fields=['voided_at'])
    return JsonResponse({'success': True, 'message': f'أُلغي الإيصال {payment.receipt_number}'})


# ══════════════════════════ مطالبات السداد ══════════════════════════

@login_required
@branch_scope_exempt(_EXEMPT)
def admin_invoices(request):
    if not request.user.has_platform_perm('manage_billing'):
        return redirect('core:no_permission')
    qs = SubscriptionInvoice.objects.select_related('tenant', 'payment')
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(tenant__name__icontains=q) | Q(invoice_number__icontains=q))
    today = dj_timezone.localdate()
    open_qs = SubscriptionInvoice.objects.filter(voided_at__isnull=True, payment__isnull=True)
    return render(request, 'core/admin_invoices.html', {
        'invoices': qs[:200],
        'q': q,
        'tenants': Tenant.objects.order_by('name').only('id', 'name', 'subscription_plan'),
        'plans': Tenant.SUBSCRIPTION_PLANS,
        'currencies': SubscriptionPayment.CURRENCY_CHOICES,
        'today': today,
        'open_count': open_qs.filter(due_date__gte=today).count(),
        'overdue_count': open_qs.filter(due_date__lt=today).count(),
        'open_usd': open_qs.filter(currency='USD').aggregate(s=Sum('amount'))['s'] or Decimal('0'),
        'open_sdg': open_qs.filter(currency='SDG').aggregate(s=Sum('amount'))['s'] or Decimal('0'),
    })


@login_required
@require_POST
def admin_invoice_create_api(request):
    if not request.user.has_platform_perm('manage_billing'):
        return _forbidden()
    data = _body(request)
    if data is None:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)
    tenant = Tenant.objects.filter(pk=data.get('tenant_id') or 0).first()
    if not tenant:
        return JsonResponse({'success': False, 'message': 'اختر المشترك'}, status=400)
    try:
        amount = Decimal(str(data.get('amount')))
    except (InvalidOperation, TypeError):
        amount = Decimal('0')
    if amount <= 0 or amount >= Decimal('1000000000'):
        return JsonResponse({'success': False, 'message': 'المبلغ غير صالح'}, status=400)
    try:
        days = int(data.get('days'))
    except (TypeError, ValueError):
        days = 0
    if not 0 < days <= 3650:
        return JsonResponse({'success': False, 'message': 'مدة الاشتراك غير صالحة'}, status=400)
    plan = data.get('plan')
    if plan not in dict(Tenant.SUBSCRIPTION_PLANS):
        return JsonResponse({'success': False, 'message': 'اختر الباقة'}, status=400)
    currency = data.get('currency') or 'USD'
    if currency not in dict(SubscriptionPayment.CURRENCY_CHOICES):
        return JsonResponse({'success': False, 'message': 'العملة غير صالحة'}, status=400)
    today = dj_timezone.localdate()
    due = parse_date(str(data.get('due_date') or ''))
    if not due or due < today:
        return JsonResponse({'success': False, 'message': 'حدّد آخر موعد للسداد (اليوم أو بعده)'}, status=400)

    with transaction.atomic():
        invoice = SubscriptionInvoice.objects.create(
            tenant=tenant, invoice_number=SubscriptionInvoice.next_invoice_number(today.year),
            amount=amount, currency=currency, plan=plan, period_days=days,
            issue_date=today, due_date=due, notes=(data.get('notes') or '').strip(),
            created_by=request.user)
    return JsonResponse({'success': True, 'id': invoice.pk,
                         'message': f'صدرت المطالبة {invoice.invoice_number}',
                         'pdf_url': reverse('core:admin_invoice_pdf', args=[invoice.pk])})


@login_required
@branch_scope_exempt(_EXEMPT)
def admin_invoice_pdf(request, pk):
    if not request.user.has_platform_perm('manage_billing'):
        return redirect('core:no_permission')
    invoice = get_object_or_404(SubscriptionInvoice.objects.select_related('tenant'), pk=pk)
    try:
        pdf = render_invoice_pdf(invoice)
    except PdfRenderError as exc:
        return HttpResponse(str(exc), status=500, content_type='text/plain; charset=utf-8')
    resp = HttpResponse(pdf, content_type='application/pdf')
    disposition = 'attachment' if request.GET.get('download') else 'inline'
    resp['Content-Disposition'] = f'{disposition}; filename="{invoice.invoice_number}.pdf"'
    return resp


@login_required
@require_POST
def admin_invoice_void_api(request, pk):
    if not request.user.has_platform_perm('manage_billing'):
        return _forbidden()
    invoice = get_object_or_404(SubscriptionInvoice, pk=pk)
    if invoice.is_paid:
        return JsonResponse({'success': False, 'message': 'المطالبة مسدَّدة ولا يمكن إلغاؤها'}, status=400)
    if not invoice.is_void:
        invoice.voided_at = dj_timezone.now()
        invoice.save(update_fields=['voided_at'])
    return JsonResponse({'success': True, 'message': f'أُلغيت المطالبة {invoice.invoice_number}'})


# ══════════════════════════ إشعارات المنصة ══════════════════════════

def _broadcast_tenants(audience, plan='', tenant_ids=()):
    qs = Tenant.objects.filter(is_active=True)
    today = dj_timezone.localdate()
    if audience == 'plan':
        qs = qs.filter(subscription_plan=plan)
    elif audience == 'expiring':
        qs = qs.filter(subscription_expires__gte=today, subscription_expires__lte=today + timedelta(days=7))
    elif audience == 'selected':
        qs = qs.filter(pk__in=list(tenant_ids))
    elif audience != 'all':
        return qs.none()
    return qs


@login_required
@branch_scope_exempt(_EXEMPT)
def admin_broadcasts(request):
    if not request.user.has_platform_perm('send_broadcasts'):
        return redirect('core:no_permission')
    return render(request, 'core/admin_broadcasts.html', {
        'broadcasts': PlatformBroadcast.objects.select_related('created_by')[:50],
        'audiences': PlatformBroadcast.AUDIENCE_CHOICES,
        'plans': Tenant.SUBSCRIPTION_PLANS,
        'tenants': Tenant.objects.filter(is_active=True).order_by('name').only('id', 'name'),
    })


@login_required
@require_POST
def admin_broadcast_send_api(request):
    if not request.user.has_platform_perm('send_broadcasts'):
        return _forbidden()
    data = _body(request)
    if data is None:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    title = (data.get('title') or '').strip()
    message = (data.get('message') or '').strip()
    if not title or not message:
        return JsonResponse({'success': False, 'message': 'العنوان والرسالة مطلوبان'}, status=400)
    if len(title) > 200:
        return JsonResponse({'success': False, 'message': 'العنوان أطول من 200 حرف'}, status=400)
    audience = data.get('audience') or 'all'
    plan = data.get('plan') or ''
    if audience not in dict(PlatformBroadcast.AUDIENCE_CHOICES):
        return JsonResponse({'success': False, 'message': 'الفئة المستهدفة غير صالحة'}, status=400)
    if audience == 'plan' and plan not in dict(Tenant.SUBSCRIPTION_PLANS):
        return JsonResponse({'success': False, 'message': 'اختر الباقة'}, status=400)
    tenant_ids = [int(i) for i in (data.get('tenant_ids') or []) if str(i).isdigit()]
    if audience == 'selected' and not tenant_ids:
        return JsonResponse({'success': False, 'message': 'اختر مشتركاً واحداً على الأقل'}, status=400)
    priority = data.get('priority') if data.get('priority') in ('low', 'medium', 'high') else 'medium'
    link = (data.get('link') or '').strip()
    if link and not link.startswith('/'):
        return JsonResponse({'success': False, 'message': 'الرابط يجب أن يبدأ بـ / (مسار داخل النظام)'}, status=400)

    tenants = list(_broadcast_tenants(audience, plan, tenant_ids))
    if data.get('dry_run'):
        return JsonResponse({'success': True, 'count': len(tenants)})
    if not tenants:
        return JsonResponse({'success': False, 'message': 'لا يوجد مشتركون ضمن هذه الفئة'}, status=400)

    from apps.notifications.models import Notification
    with transaction.atomic():
        broadcast = PlatformBroadcast.objects.create(
            title=title, message=message, link=link, priority=priority, audience=audience,
            audience_plan=plan if audience == 'plan' else '', recipients_count=len(tenants),
            created_by=request.user)
        Notification.objects.bulk_create([
            Notification(tenant=t, notification_type='platform', priority=priority,
                         title=title, message=message, link=link, created_by=request.user)
            for t in tenants
        ])
    return JsonResponse({'success': True, 'message': f'أُرسل الإشعار إلى {len(tenants)} مشترك', 'id': broadcast.pk})


# ───────────────────────── التحقق العام من المستند (رمز QR) ─────────────────────────

def _verify_context(kind, number):
    """بيانات صفحة التحقق، أو None إن لم يوجد المستند. تُعرض كما هي مطبوعة في المستند."""
    from .brand_docs import CURRENCY_NAMES, _money
    if kind == doc_verify.INVOICE:
        doc = SubscriptionInvoice.objects.select_related('tenant').filter(invoice_number=number).first()
        if doc is None:
            return None
        rows = [('المستند', 'مطالبة سداد', False), ('الرقم', doc.invoice_number, True),
                ('تاريخ الإصدار', doc.issue_date.strftime('%Y/%m/%d'), True),
                ('آخر موعد للسداد', doc.due_date.strftime('%Y/%m/%d'), True),
                ('المشترك', doc.tenant.name, False),
                ('المبلغ', f'{_money(doc.amount, doc.currency)} ({CURRENCY_NAMES.get(doc.currency, doc.currency)})', False)]
        return {'is_void': doc.is_void, 'state': doc.status, 'rows': rows}
    doc = SubscriptionPayment.objects.select_related('tenant').filter(receipt_number=number).first()
    if doc is None:
        return None
    rows = [('المستند', 'سند قبض', False), ('الرقم', doc.receipt_number, True),
            ('تاريخ الدفع', doc.paid_at.strftime('%Y/%m/%d'), True),
            ('المشترك', doc.tenant.name, False),
            ('المبلغ', f'{_money(doc.amount, doc.currency)} ({CURRENCY_NAMES.get(doc.currency, doc.currency)})', False),
            ('تم تجديد الاشتراك حتى', f'{doc.period_end:%Y/%m/%d}', True)]
    return {'is_void': doc.is_void, 'state': 'receipt', 'rows': rows}


@never_cache
@require_GET
def verify_document(request, kind, number, sig):
    """صفحة عامة بلا تسجيل دخول يفتحها رمز QR المطبوع. التوقيع في الرابط يمنع تخمين مستندات أخرى."""
    context = _verify_context(kind, number) if doc_verify.is_valid(kind, number, sig) else None
    if context is None:
        return HttpResponse('المستند غير موجود أو الرابط غير صحيح', status=404, content_type='text/plain; charset=utf-8')
    resp = render(request, 'core/verify_document.html', {'doc': context})
    resp['X-Robots-Tag'] = 'noindex, nofollow'
    resp['Referrer-Policy'] = 'no-referrer'
    return resp
