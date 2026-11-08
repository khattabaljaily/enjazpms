"""
التحقق من مستندات إنجاز المطبوعة (مطالبة السداد وسند القبض) برمز QR.

الرابط يحمل توقيعاً (HMAC بمفتاح التطبيق السري) على نوع المستند ورقمه، فلا يمكن تخمين رابط مستند آخر مع أن الأرقام
متسلسلة، ولا حاجة لحقل جديد في قاعدة البيانات. الصفحة عامة (بلا تسجيل دخول) وتعرض ما هو مطبوع في المستند نفسه.
"""
import hashlib
import hmac
import logging

from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

INVOICE, RECEIPT = 'invoice', 'receipt'
KINDS = (INVOICE, RECEIPT)


def sign(kind, number):
    key = settings.SECRET_KEY.encode()
    return hmac.new(key, f'doc-verify:{kind}:{number}'.encode(), hashlib.sha256).hexdigest()[:20]


def is_valid(kind, number, sig):
    return kind in KINDS and hmac.compare_digest(sign(kind, number), str(sig))


def verify_url(kind, number):
    base = settings.PUBLIC_BASE_URL.rstrip('/')
    return base + reverse('core:verify_document', args=[kind, number, sign(kind, number)])


def qr_svg(url):
    """رمز QR كـ SVG مضمَّن، أو '' إن لم تتوفر المكتبة (لا يُمنع طبع المستند بسبب الرمز)."""
    try:
        import segno
    except ImportError:
        logger.warning('segno is not installed: documents are printed without a verification QR')
        return ''
    return segno.make(url, error='m').svg_inline(scale=3, border=0, dark='#222222', omitsize=True, svgclass='qr-svg')
