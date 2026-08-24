"""
Store Models
============
StoreSettings  — per-tenant online storefront configuration
OnlineOrder    — customer order placed via the public store
OnlineOrderLine — individual line items for each order
"""
import uuid
import random
from decimal import Decimal
import pytz
from django.db import models
from django.utils.text import slugify
from django.utils import timezone
from apps.core.models import TenantMixin


DAYS_AR = {
    'saturday':  'السبت',
    'sunday':    'الأحد',
    'monday':    'الاثنين',
    'tuesday':   'الثلاثاء',
    'wednesday': 'الأربعاء',
    'thursday':  'الخميس',
    'friday':    'الجمعة',
}
DAYS_ORDER = ['saturday', 'sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday']

DEFAULT_HOURS = {
    'saturday':  {'enabled': True,  'open': '08:00', 'close': '22:00'},
    'sunday':    {'enabled': True,  'open': '08:00', 'close': '22:00'},
    'monday':    {'enabled': True,  'open': '08:00', 'close': '22:00'},
    'tuesday':   {'enabled': True,  'open': '08:00', 'close': '22:00'},
    'wednesday': {'enabled': True,  'open': '08:00', 'close': '22:00'},
    'thursday':  {'enabled': True,  'open': '08:00', 'close': '22:00'},
    'friday':    {'enabled': False, 'open': '14:00', 'close': '22:00'},
}

# Python weekday() → day name mapping (0=Monday … 6=Sunday)
_WEEKDAY_TO_NAME = {
    0: 'monday', 1: 'tuesday', 2: 'wednesday',
    3: 'thursday', 4: 'friday', 5: 'saturday', 6: 'sunday',
}


# ──────────────────────────────────────────────────────────────
# Store Settings
# ──────────────────────────────────────────────────────────────

class StoreSettings(TenantMixin):
    """
    One record per tenant.  Created lazily the first time a tenant
    visits the store management page.
    """

    slug = models.SlugField(
        'رابط المتجر', max_length=120, unique=True,
        help_text='يستخدم في عنوان المتجر مثال: my-shop'
    )
    is_enabled = models.BooleanField('المتجر مفعّل', default=False)

    # Branding
    display_name  = models.CharField('اسم المتجر', max_length=200, blank=True)
    description   = models.TextField('وصف المتجر', blank=True)
    cover_image   = models.ImageField('صورة الغلاف', upload_to='store/covers/', blank=True, null=True)
    accent_color  = models.CharField('اللون الرئيسي', max_length=7, default='#6366f1')
    whatsapp      = models.CharField('واتساب (اختياري)', max_length=30, blank=True,
                                     help_text='للتواصل السريع مع العملاء')

    # Catalog
    show_out_of_stock    = models.BooleanField('عرض المنتجات النافدة', default=False)
    show_prices          = models.BooleanField('عرض الأسعار', default=True)
    show_stock_quantity  = models.BooleanField('عرض الكمية المتوفرة', default=False)
    show_price_list      = models.BooleanField(
        'تفعيل قائمة الأسعار', default=False,
        help_text='صفحة عامة منفصلة تعرض كل المنتجات وأسعارها مع بحث وفلترة، بدون طلب أو سلة'
    )
    min_order_amount     = models.DecimalField(
        'الحد الأدنى للطلب', max_digits=12, decimal_places=2, default=0
    )

    # Payment & delivery
    bank_details      = models.TextField(
        'بيانات التحويل البنكي', blank=True,
        help_text='تظهر للعميل عند اختيار التحويل البنكي'
    )
    delivery_message  = models.TextField(
        'رسالة الشحن/التسليم', blank=True,
        help_text='تظهر في صفحة تأكيد الطلب'
    )

    # ── Working hours & status ─────────────────────────────────
    OVERRIDE_CHOICES = (
        ('auto',   'تلقائي (حسب الجدول)'),
        ('open',   'مفتوح دائماً'),
        ('closed', 'مغلق دائماً'),
    )
    status_override = models.CharField(
        'حالة المتجر', max_length=10, choices=OVERRIDE_CHOICES, default='auto'
    )
    open_message   = models.CharField(
        'رسالة المفتوح', max_length=200, blank=True,
        default='مرحباً بك .. المتجر مفتوح الآن'
    )
    closed_message = models.CharField(
        'رسالة المغلق', max_length=200, blank=True,
        default='المتجر مغلق حالياً.'
    )
    working_hours  = models.JSONField(
        'ساعات العمل', default=dict, blank=True
    )

    class Meta:
        db_table      = 'store_settings'
        verbose_name  = 'إعدادات المتجر'

    def __str__(self):
        return f"متجر {self.display_name or self.tenant.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._generate_slug()
        if not self.display_name:
            self.display_name = self.tenant.name
        if not self.working_hours:
            self.working_hours = DEFAULT_HOURS.copy()
        super().save(*args, **kwargs)

    def _generate_slug(self):
        """5-digit random unique slug."""
        for _ in range(20):
            candidate = str(random.randint(10000, 99999))
            if not StoreSettings.objects.filter(slug=candidate).exists():
                return candidate
        # fallback to tenant-based slug
        base = slugify(self.display_name or str(self.tenant_id)) or f's{self.tenant_id}'
        n, slug = 1, base
        while StoreSettings.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f'{base}-{n}'; n += 1
        return slug

    def get_hours_for_day(self, day_name: str) -> dict:
        hours = self.working_hours or DEFAULT_HOURS
        return hours.get(day_name, DEFAULT_HOURS.get(day_name, {'enabled': False, 'open': '08:00', 'close': '22:00'}))

    def get_status(self) -> dict:
        """
        Returns dict: {is_open, label, message, next_open, today_hours}
        """
        if self.status_override == 'open':
            return {
                'is_open':     True,
                'label':       'مفتوح',
                'mode':        'open',
                'message':     self.open_message,
                'next_open':   None,
                'today_hours': None,
                'override':    True,
            }
        if self.status_override == 'closed':
            return {
                'is_open':     False,
                'label':       'مغلق',
                'mode':        'closed',
                'message':     self.closed_message,
                'next_open':   None,
                'today_hours': None,
                'override':    True,
            }

        # Auto mode — check working hours schedule (use tenant's timezone explicitly)
        tenant_tz = pytz.timezone(self.tenant.timezone)
        now      = timezone.localtime(timezone.now(), tenant_tz)
        day_name = _WEEKDAY_TO_NAME[now.weekday()]
        day_info = self.get_hours_for_day(day_name)
        now_str  = now.strftime('%H:%M')

        if day_info.get('enabled') and day_info['open'] <= now_str <= day_info['close']:
            return {
                'is_open':     True,
                'label':       'مفتوح',
                'mode':        'schedule',
                'message':     self.open_message,
                'next_open':   None,
                'today_hours': f"{day_info['open']} – {day_info['close']}",
                'override':    False,
            }

        return {
            'is_open':     False,
            'label':       'مغلق',
            'mode':        'schedule',
            'message':     self.closed_message,
            'next_open':   self._next_open_str(now),
            'today_hours': f"{day_info['open']} – {day_info['close']}" if day_info.get('enabled') else None,
            'override':    False,
        }

    def _next_open_str(self, now=None) -> str:
        """Return human-readable string of next opening."""
        if now is None:
            tenant_tz = pytz.timezone(self.tenant.timezone)
            now = timezone.localtime(timezone.now(), tenant_tz)
        now_str  = now.strftime('%H:%M')
        weekday  = now.weekday()
        hours    = self.working_hours or DEFAULT_HOURS

        # Check remaining days in the week (up to 7)
        for offset in range(0, 8):
            day_name = _WEEKDAY_TO_NAME[(weekday + offset) % 7]
            day_info = hours.get(day_name, {})
            if not day_info.get('enabled'):
                continue
            open_time = day_info.get('open', '08:00')
            if offset == 0 and open_time <= now_str:
                continue  # already past today's open time
            day_ar = DAYS_AR.get(day_name, day_name)
            if offset == 0:
                return f'يفتح اليوم الساعة {open_time}'
            elif offset == 1:
                return f'يفتح غداً الساعة {open_time}'
            else:
                return f'يفتح {day_ar} الساعة {open_time}'
        return ''


# ──────────────────────────────────────────────────────────────
# Online Order
# ──────────────────────────────────────────────────────────────

class OnlineOrder(TenantMixin):

    STATUS_CHOICES = (
        ('pending',  'في الانتظار'),
        ('approved', 'تم القبول'),
        ('rejected', 'مرفوض'),
    )

    PAYMENT_CHOICES = (
        ('bank',   'تحويل بنكي'),
        ('credit', 'آجل'),
    )

    order_number = models.CharField('رقم الطلب', max_length=30, blank=True)
    token        = models.UUIDField('رمز التتبع', default=uuid.uuid4, unique=True, editable=False)
    store        = models.ForeignKey(
        StoreSettings, on_delete=models.CASCADE,
        related_name='orders', verbose_name='المتجر'
    )

    # Customer info (guest — no account required)
    customer_name    = models.CharField('اسم العميل', max_length=200)
    customer_phone   = models.CharField('رقم الهاتف', max_length=30)
    customer_address = models.TextField('العنوان', blank=True)
    customer_notes   = models.TextField('ملاحظات', blank=True)

    payment_method = models.CharField('طريقة الدفع', max_length=10, choices=PAYMENT_CHOICES)
    status         = models.CharField('الحالة', max_length=15, choices=STATUS_CHOICES, default='pending')

    # Totals (denormalised for quick display)
    subtotal     = models.DecimalField('المجموع', max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField('الإجمالي', max_digits=14, decimal_places=2, default=0)

    # Link to the created SaleInvoice after approval
    sale_invoice = models.OneToOneField(
        'sales.SaleInvoice',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='online_order',
        verbose_name='الفاتورة المُنشأة'
    )

    class Meta:
        db_table     = 'store_online_orders'
        verbose_name = 'طلب أونلاين'
        verbose_name_plural = 'طلبات أونلاين'
        ordering     = ['-created_at']
        indexes      = [
            models.Index(fields=['tenant', 'status', '-created_at']),
            models.Index(fields=['token']),
        ]

    def __str__(self):
        return f"{self.order_number} — {self.customer_name}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            last = (
                OnlineOrder.objects.filter(tenant=self.tenant)
                .exclude(order_number='')
                .order_by('-id').first()
            )
            n = 1
            if last and last.order_number.startswith('ORD-'):
                try:
                    n = int(last.order_number.split('-')[-1]) + 1
                except ValueError:
                    n = OnlineOrder.objects.filter(tenant=self.tenant).count() + 1
            self.order_number = f'ORD-{n:05d}'
        super().save(*args, **kwargs)

    @property
    def is_pending(self):
        return self.status == 'pending'


# ──────────────────────────────────────────────────────────────
# Online Order Line
# ──────────────────────────────────────────────────────────────

class OnlineOrderLine(TenantMixin):

    order      = models.ForeignKey(OnlineOrder, on_delete=models.CASCADE, related_name='lines')
    item       = models.ForeignKey('items.Item', on_delete=models.PROTECT, verbose_name='المنتج')
    item_name  = models.CharField('اسم المنتج', max_length=300)   # snapshot at order time
    unit_price = models.DecimalField('سعر الوحدة', max_digits=14, decimal_places=2)
    quantity   = models.DecimalField('الكمية', max_digits=12, decimal_places=4)
    line_total = models.DecimalField('الإجمالي', max_digits=14, decimal_places=2)

    class Meta:
        db_table     = 'store_online_order_lines'
        verbose_name = 'بند طلب أونلاين'

    def __str__(self):
        return f"{self.item_name} × {self.quantity}"

    def save(self, *args, **kwargs):
        self.line_total = (self.unit_price * self.quantity).quantize(Decimal('0.01'))
        if not self.item_name:
            self.item_name = self.item.name
        super().save(*args, **kwargs)
