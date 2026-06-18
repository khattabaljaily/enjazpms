"""
Sales Email Service
إرسال فواتير المبيعات بالبريد الإلكتروني
"""
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_invoice_email(invoice, recipient_email: str, request=None) -> tuple[bool, str]:
    """
    Send a sale invoice as HTML email using the print invoice design.
    Returns (success: bool, error_message: str).
    """
    tenant = invoice.tenant

    lines = list(
        invoice.lines.select_related('item')
        .prefetch_related('item__item_units')
        .all()
    )
    for ln in lines:
        iu_list = list(ln.item.item_units.order_by('factor'))
        matched = next(
            (u for u in iu_list if abs(float(u.factor) - float(ln.unit_factor or 1)) < 0.0001),
            iu_list[0] if iu_list else None,
        )
        ln.unit_display = matched.name if matched else ln.item.base_unit_name

    from apps.core.models import Settings as TenantSettings
    settings_obj, _ = TenantSettings.objects.get_or_create(tenant=tenant)

    # Build absolute logo URL
    if request:
        if tenant.logo:
            logo_abs_url = request.build_absolute_uri(tenant.logo.url)
        else:
            logo_abs_url = request.build_absolute_uri(
                settings.STATIC_URL + 'img/logo/logo-161616.png'
            )
    else:
        logo_abs_url = ''

    brand_color = (settings_obj.invoice_color or '#6366f1').strip()
    context = {
        'invoice': invoice,
        'lines': lines,
        'tenant': tenant,
        'settings_obj': settings_obj,
        'logo_abs_url': logo_abs_url,
        'brand_color': brand_color,
        'brand_color_bg': brand_color + '18',
        'brand_color_border': brand_color + '50',
    }

    subject = f'فاتورة {invoice.invoice_number} — {tenant.name}'
    html_body = render_to_string('sales/email/invoice_email.html', context)

    try:
        msg = EmailMessage(
            subject=subject,
            body=html_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient_email],
        )
        msg.content_subtype = 'html'
        msg.send()
        logger.info('Invoice email sent: %s → %s', invoice.invoice_number, recipient_email)
        return True, ''
    except Exception as exc:
        logger.error('Invoice email failed: %s — %s', invoice.invoice_number, exc)
        return False, str(exc)
