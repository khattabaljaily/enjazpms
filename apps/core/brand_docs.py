"""
مستندات إنجاز المطبوعة (مطالبة السداد، سند القبض) بالهوية الرسمية.

القالب مطابق لقالب الليترهيد في ملفات الشركة (الرمز الذهبي + الشعار النصي + QR + شريط برتقالي + تذييل
الهاتف والبريد والموقع)، ويُطبع إلى PDF بـ Chrome بلا واجهة — كما تبني أداة `_build/build.py` عندكم.
الأصول (الشعار والخطوط وCSS) في static/brand.
"""
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

BRAND_DIR = Path(settings.BASE_DIR) / 'static' / 'brand'

# حساب استلام المدفوعات المطبوع في مطالبة السداد
BANK_DETAILS = [
    ('البنك', 'Bank', 'بنك الخرطوم'),
    ('اسم الحساب', 'Account Name', 'خطاب الجيلي احمد محمد'),
    ('رقم الحساب', 'Account No.', '1578628'),
]

CURRENCY_NAMES = {'USD': 'دولار أمريكي', 'SDG': 'جنيه'}


class PdfRenderError(RuntimeError):
    pass


def _chrome():
    for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
        path = shutil.which(name)
        if path:
            return path
    raise PdfRenderError('لم يُعثر على Chrome لتوليد ملف PDF')


def _money(amount, currency):
    amount = Decimal(amount)
    text = f'{amount:,.2f}' if amount % 1 else f'{int(amount):,}'
    return f'${text}' if currency == 'USD' else f'{text} جنيه'


def _html_to_pdf(html):
    with tempfile.TemporaryDirectory(prefix='enjaz-doc-') as tmp:
        tmp = Path(tmp)
        src, out = tmp / 'doc.html', tmp / 'doc.pdf'
        src.write_text(html, encoding='utf-8')
        cmd = [_chrome(), '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
               f'--user-data-dir={tmp / "profile"}', '--no-pdf-header-footer', '--allow-file-access-from-files',
               f'--print-to-pdf={out}', src.as_uri()]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise PdfRenderError(f'تعذّر توليد ملف PDF: {exc}') from exc
        if not out.exists():
            raise PdfRenderError('لم يُنتج Chrome ملف PDF')
        return out.read_bytes()


def _render(template, **ctx):
    ctx['brand'] = BRAND_DIR.as_uri() + '/'
    return _html_to_pdf(render_to_string(template, ctx))


def render_invoice_pdf(invoice):
    """مطالبة سداد (SubscriptionInvoice) تُطبع وتُسلَّم للمشترك."""
    tenant = invoice.tenant
    return _render(
        'core/docs/invoice_doc.html', invoice=invoice, tenant=tenant, bank=BANK_DETAILS,
        address='، '.join(x for x in (tenant.address, tenant.city, tenant.country) if x),
        amount_text=_money(invoice.amount, invoice.currency),
        currency_name=CURRENCY_NAMES.get(invoice.currency, invoice.currency))


def render_receipt_pdf(payment):
    """سند قبض لدفعة اشتراك (SubscriptionPayment)."""
    invoice = getattr(payment, 'invoice', None) if payment.pk else None
    return _render(
        'core/docs/receipt_doc.html', payment=payment, tenant=payment.tenant, invoice=invoice,
        amount_text=_money(payment.amount, payment.currency),
        currency_name=CURRENCY_NAMES.get(payment.currency, payment.currency))
