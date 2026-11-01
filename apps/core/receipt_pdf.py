"""
إيصال استلام اشتراك (PDF) بترويسة إنجاز.

يعتمد على PyMuPDF مثل `survey_pdf` لأنه يشكّل الحروف العربية ويدعم RTL دون مكتبات إضافية.
صفحة A4 واحدة: ترويسة (الشعار + اسم إنجاز + عنوان الإيصال)، بيانات الدفعة، المبلغ، تذييل.
"""
from decimal import Decimal
from html import escape

import pymupdf
from django.conf import settings

NAVY = '#1b2236'
GOLD = '#c9840f'
GOLD_SOFT = '#fdf3e1'
MUTED = '#6b7280'
LINE = '#e3e7ee'
FILL_BG = '#f4f6fa'

PAGE_W, PAGE_H = 595, 842
MARGIN = 42

FONT_DIR = settings.BASE_DIR / 'static' / 'fonts' / 'cairo'
LOGO = settings.BASE_DIR / 'branding' / 'logo' / 'logo-dark.png'

CONTACT_EMAIL = 'info@enjaztechnology.com'
CONTACT_SITE = 'enjaztechnology.com'

# حساب استلام المدفوعات المطبوع في المطالبة
BANK_DETAILS = [
    ('البنك', 'بنك الخرطوم'),
    ('اسم صاحب الحساب', 'خطاب الجيلي احمد محمد'),
    ('رقم الحساب', '1578628'),
]

CSS = f"""
@font-face {{ font-family: Cairo; src: url(Cairo-Regular.ttf); }}
@font-face {{ font-family: Cairo; font-weight: bold; src: url(Cairo-Bold.ttf); }}
body {{ font-family: Cairo; color: {NAVY}; font-size: 10pt; }}
p {{ margin: 0; line-height: 1.6; }}
td {{ vertical-align: middle; padding: 5pt 8pt; border-bottom: 0.6pt solid {LINE}; }}
.k {{ color: {MUTED}; font-size: 9pt; }}
.v {{ font-weight: bold; font-size: 10.5pt; }}
"""


def _rtl(html):
    return html.replace('<p', '<p dir="rtl"').replace('<div', '<div dir="rtl"')


def _rgb(hexcolor):
    h = hexcolor.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _money(amount, currency):
    amount = Decimal(amount)
    text = f'{amount:,.2f}' if amount % 1 else f'{int(amount):,}'
    return f'${text}' if currency == 'USD' else f'{text} جنيه'


class _Sheet:
    """صفحة A4 بترويسة إنجاز وتذييلها؛ المستندات (إيصال، مطالبة) تشترك فيها."""

    def __init__(self, title_ar, title_en, footer_text):
        self.arch = pymupdf.Archive(str(FONT_DIR))
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        page = self.page
        page.insert_image(pymupdf.Rect(PAGE_W - MARGIN - 58, 30, PAGE_W - MARGIN, 88), filename=str(LOGO))
        self.box((PAGE_W - MARGIN - 220, 36, PAGE_W - MARGIN - 66, 90),
                 f'<p style="font-size:22pt;font-weight:bold;color:{NAVY};line-height:1.1">إنجاز</p>'
                 f'<p style="font-size:9pt;color:{GOLD};font-weight:bold;letter-spacing:2pt">ENJAZ TECHNOLOGY</p>')
        self.box((MARGIN, 40, MARGIN + 190, 90),
                 f'<div style="text-align:left"><p style="font-size:17pt;font-weight:bold;color:{GOLD}">{escape(title_ar)}</p>'
                 f'<p style="font-size:9pt;color:{MUTED}">{escape(title_en)}</p></div>')
        self.rect((MARGIN, 100, PAGE_W - MARGIN, 103), GOLD)
        self.rect((MARGIN, PAGE_H - 78, PAGE_W - MARGIN, PAGE_H - 77), LINE)
        self.box((MARGIN, PAGE_H - 70, PAGE_W - MARGIN, PAGE_H - 24),
                 f'<p style="text-align:center;font-size:9pt;color:{MUTED}">{escape(footer_text)}</p>'
                 f'<p style="text-align:center;font-size:8.5pt;color:{MUTED}">{CONTACT_EMAIL} · {CONTACT_SITE}</p>')

    def box(self, rect, html):
        self.page.insert_htmlbox(pymupdf.Rect(*rect), _rtl(html), css=CSS, archive=self.arch)

    def rect(self, rect, color):
        shape = self.page.new_shape()
        shape.draw_rect(pymupdf.Rect(*rect))
        shape.finish(color=None, fill=_rgb(color))
        shape.commit()

    def rows(self, rows, y):
        """صفوف «تسمية / قيمة»؛ تعيد الإحداثي التالي."""
        for k, v in rows:
            lines = max(1, -(-len(v) // 52))
            h = 22 + 14 * (lines - 1)
            self.box((PAGE_W - MARGIN - 130, y, PAGE_W - MARGIN, y + h), f'<p class="k">{escape(k)}</p>')
            self.box((MARGIN, y, PAGE_W - MARGIN - 138, y + h),
                     f'<p class="v">{escape(v).replace(chr(10), "<br>")}</p>')
            y += h + 4
            self.rect((MARGIN, y - 2, PAGE_W - MARGIN, y - 1.4), LINE)
        return y

    def amount_band(self, label, text, y=162):
        self.rect((MARGIN, y, PAGE_W - MARGIN, y + 70), GOLD_SOFT)
        self.rect((PAGE_W - MARGIN - 4, y, PAGE_W - MARGIN, y + 70), GOLD)
        self.box((MARGIN + 12, y + 8, PAGE_W - MARGIN - 16, y + 66),
                 f'<p class="k">{escape(label)}</p>'
                 f'<p style="font-size:26pt;font-weight:bold;color:{NAVY};line-height:1.15">{escape(text)}</p>')

    def watermark(self, text):
        self.page.insert_htmlbox(
            pymupdf.Rect(MARGIN, 380, PAGE_W - MARGIN, 460),
            f'<div dir="rtl" style="text-align:center;font-size:54pt;font-weight:bold;color:#e5b8b8">{escape(text)}</div>',
            css=CSS, archive=self.arch)

    def finish(self, title):
        self.doc.set_metadata({'title': title, 'author': 'ENJAZ'})
        out = self.doc.tobytes(garbage=3, deflate=True)
        self.doc.close()
        return out


def render_receipt_pdf(payment):
    """يعيد bytes لإيصال الدفعة (SubscriptionPayment)."""
    sh = _Sheet('إيصال استلام', 'Payment Receipt', 'شكراً لثقتكم بإنجاز. هذا الإيصال صادر إلكترونياً من منصة إنجاز.')
    sh.box((PAGE_W - MARGIN - 200, 112, PAGE_W - MARGIN, 150),
           f'<p class="k">رقم الإيصال</p><p class="v" style="color:{GOLD}">{escape(payment.receipt_number)}</p>')
    sh.box((MARGIN, 112, MARGIN + 200, 150),
           f'<p class="k" style="text-align:left">تاريخ الدفع</p><p class="v" style="text-align:left">{payment.paid_at:%Y/%m/%d}</p>')
    sh.amount_band('المبلغ المستلم', _money(payment.amount, payment.currency))
    rows = [
        ('استُلم من', payment.tenant.name),
        ('الباقة', payment.get_plan_display()),
        ('بداية الاشتراك', f'{payment.period_start:%Y/%m/%d}'),
        ('نهاية الاشتراك', f'{payment.period_end:%Y/%m/%d}'),
        ('مدة الاشتراك', f'{payment.period_days} يوماً'),
        ('طريقة الدفع', payment.get_method_display()),
    ]
    if payment.reference:
        rows.append(('المرجع', payment.reference))
    if payment.notes:
        rows.append(('ملاحظات', payment.notes))
    sh.rows(rows, 252)
    if payment.is_void:
        sh.watermark('ملغى')
    return sh.finish(f'إيصال {payment.receipt_number}')


def render_invoice_pdf(invoice):
    """يعيد bytes لمطالبة سداد (SubscriptionInvoice) تُطبع وتُسلَّم للمشترك."""
    tenant = invoice.tenant
    sh = _Sheet('مطالبة سداد', 'Payment Request',
                'يُرجى السداد قبل تاريخ الاستحقاق وإبلاغنا بالمرجع بعد التحويل. صادر من منصة إنجاز.')
    sh.box((PAGE_W - MARGIN - 200, 112, PAGE_W - MARGIN, 150),
           f'<p class="k">رقم المطالبة</p><p class="v" style="color:{GOLD}">{escape(invoice.invoice_number)}</p>')
    sh.box((MARGIN + 210, 112, MARGIN + 340, 150),
           f'<p class="k" style="text-align:center">تاريخ الإصدار</p>'
           f'<p class="v" style="text-align:center">{invoice.issue_date:%Y/%m/%d}</p>')
    sh.box((MARGIN, 112, MARGIN + 200, 150),
           f'<p class="k" style="text-align:left">آخر موعد للسداد</p>'
           f'<p class="v" style="text-align:left;color:#b91c1c">{invoice.due_date:%Y/%m/%d}</p>')
    sh.amount_band('المبلغ المطلوب سداده', _money(invoice.amount, invoice.currency))

    client = [('المشترك', tenant.name)]
    for label, value in (('العنوان', ', '.join(x for x in (tenant.address, tenant.city, tenant.country) if x)),
                         ('الهاتف', tenant.phone), ('البريد الإلكتروني', tenant.email)):
        if value:
            client.append((label, value))
    y = sh.rows(client, 252)
    sh.box((PAGE_W - MARGIN - 200, y + 8, PAGE_W - MARGIN, y + 30),
           f'<p style="font-size:11pt;font-weight:bold;color:{GOLD}">تفاصيل المطالبة</p>')
    detail = [
        ('البند', f'اشتراك باقة {invoice.get_plan_display()} في منصة إنجاز'),
        ('مدة الاشتراك', f'{invoice.period_days} يوماً'),
    ]
    if invoice.notes:
        detail.append(('ملاحظات', invoice.notes))
    y = sh.rows(detail, y + 32)
    sh.box((PAGE_W - MARGIN - 200, y + 8, PAGE_W - MARGIN, y + 30),
           f'<p style="font-size:11pt;font-weight:bold;color:{GOLD}">بيانات السداد</p>')
    sh.rows(BANK_DETAILS, y + 32)
    if invoice.is_void:
        sh.watermark('ملغاة')
    elif invoice.is_paid:
        sh.watermark('مسدَّدة')
    return sh.finish(f'مطالبة {invoice.invoice_number}')
