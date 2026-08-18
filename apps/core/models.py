"""
Core Models - النماذج الأساسية
Multi-Tenant System
"""
from contextvars import ContextVar

from django.db import models
from django.conf import settings
from django.utils.text import slugify
from django.utils import timezone

from .constants import DEFAULT_COUNTRY, DEFAULT_TIMEZONE


# Context flag used during Tenant deletion to bypass related system-default protection signals
tenant_deletion_in_progress = ContextVar('tenant_deletion_in_progress', default=False)


# ============================================
# MANAGERS & QUERYSETS
# ============================================

class TenantQuerySet(models.QuerySet):
    """QuerySet مخصص للفلترة التلقائية بناءً على الـ Tenant"""
    
    def for_tenant(self, tenant):
        """فلترة البيانات حسب tenant معين"""
        return self.filter(tenant=tenant)


class TenantManager(models.Manager):
    """Manager مخصص للفلترة التلقائية"""
    
    def get_queryset(self):
        return TenantQuerySet(self.model, using=self._db)
    
    def for_tenant(self, tenant):
        return self.get_queryset().for_tenant(tenant)


# ============================================
# BUSINESS TYPES
# ============================================

class BusinessType(models.Model):
    """أنواع الأنشطة التجارية المدعومة"""
    
    name = models.CharField('الاسم بالإنجليزية', max_length=100, unique=True)
    name_ar = models.CharField('الاسم بالعربية', max_length=100)
    slug = models.SlugField('الرمز', unique=True)
    icon = models.CharField('الأيقونة', max_length=50, default='fa-store')
    description = models.TextField('الوصف', blank=True)
    features = models.JSONField('المميزات', default=dict, blank=True)
    is_active = models.BooleanField('نشط', default=True)
    display_order = models.IntegerField('ترتيب العرض', default=0)
    
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)
    
    class Meta:
        db_table = 'business_types'
        verbose_name = 'نوع نشاط تجاري'
        verbose_name_plural = 'أنواع الأنشطة التجارية'
        ordering = ['display_order', 'name_ar']
    
    def __str__(self):
        return self.name_ar
    
    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


# ============================================
# TENANT (المشترك/النشاط التجاري)
# ============================================

class Tenant(models.Model):
    """
    Tenant - المشترك/النشاط التجاري
    كل مشترك له بياناته المنفصلة تماماً
    """
    
    VERSION_TYPES = (
        ('single_store', 'محل واحد بمخزن واحد'),
        ('multi_stock', 'محل واحد بمخازن متعددة'),
        ('multi_branch', 'فروع متعددة (محلات ومخازن)'),
    )
    
    SUBSCRIPTION_PLANS = (
        ('trial', 'تجريبي'),
        ('basic', 'أساسي'),
        ('pro', 'احترافي'),
        ('enterprise', 'مؤسسات'),
    )

    # Features available per plan
    PLAN_FEATURES = {
        'trial':      {'ai_assistant': True,  'smart_tips': True,  'store': True,  'agents': True,  'auto_backup': True,  'auto_backup_daily': 1},
        'basic':      {'ai_assistant': False, 'smart_tips': False, 'store': False, 'agents': False, 'auto_backup': False, 'auto_backup_daily': 0},
        'pro':        {'ai_assistant': True,  'smart_tips': True,  'store': True,  'agents': True,  'auto_backup': True,  'auto_backup_daily': 1},
        'enterprise': {'ai_assistant': True,  'smart_tips': True,  'store': True,  'agents': True,  'auto_backup': True,  'auto_backup_daily': 2},
    }
    
    # Basic Info
    name = models.CharField('اسم النشاط التجاري', max_length=200)
    slug = models.SlugField('الرمز', max_length=200, unique=True)
    business_type = models.ForeignKey(
        BusinessType,
        on_delete=models.PROTECT,
        verbose_name='نوع النشاط',
        related_name='tenants'
    )
    logo = models.ImageField('الشعار', upload_to='tenants/logos/', blank=True, null=True)
    
    # Contact Info
    email = models.EmailField('البريد الإلكتروني', blank=True)
    phone = models.CharField('رقم الهاتف', max_length=20, blank=True)
    address = models.TextField('العنوان', blank=True)
    city = models.CharField('المدينة', max_length=100, blank=True)
    country = models.CharField('البلد', max_length=100, blank=True, default=DEFAULT_COUNTRY)
    
    # Subscription
    subscription_plan = models.CharField(
        'الباقة',
        max_length=20,
        choices=SUBSCRIPTION_PLANS,
        default='trial'
    )
    subscription_start = models.DateField('بداية الاشتراك', default=timezone.now)
    subscription_expires = models.DateField('نهاية الاشتراك', null=True, blank=True)
    is_active = models.BooleanField('نشط', default=True)
    is_demo = models.BooleanField('حساب تجريبي', default=False)
    
    # System Settings
    version_type = models.CharField(
        'نوع النسخة',
        max_length=20,
        choices=VERSION_TYPES,
        default='single_store'
    )
    max_branches = models.IntegerField('عدد الفروع', default=1)
    max_stocks = models.IntegerField('عدد المخازن', default=1)
    max_users = models.IntegerField('عدد المستخدمين', default=5)
    
    # Settings
    timezone = models.CharField('المنطقة الزمنية', max_length=50, default=DEFAULT_TIMEZONE)
    language = models.CharField('اللغة', max_length=10, default='ar')
    currency = models.CharField('العملة', max_length=3, default='SDG')

    # Hard Currency Mode
    hard_currency_mode = models.BooleanField('وضع العملة الصعبة', default=False)
    hard_currency = models.CharField('العملة الصعبة', max_length=3, default='USD', blank=True)
    exchange_rate = models.DecimalField(
        'سعر الصرف',
        max_digits=12, decimal_places=2, default=1,
        help_text='سعر العملة المحلية مقابل وحدة واحدة من العملة الصعبة'
    )
    exchange_rate_updated_at = models.DateTimeField('آخر تحديث للسعر', null=True, blank=True)
    
    # Terms of Service
    terms_accepted_at = models.DateTimeField('تاريخ قبول الاتفاقية', null=True, blank=True)
    terms_version     = models.CharField('إصدار الاتفاقية المقبولة', max_length=20, blank=True)

    # Meta
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)
    
    class Meta:
        db_table = 'tenants'
        verbose_name = 'مشترك'
        verbose_name_plural = 'المشتركين'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            import uuid
            base_slug = slugify(self.name, allow_unicode=True) or str(uuid.uuid4())[:8]
            slug = base_slug
            counter = 1
            while Tenant.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        token = tenant_deletion_in_progress.set(True)
        try:
            return super().delete(*args, **kwargs)
        finally:
            tenant_deletion_in_progress.reset(token)
    
    def is_subscription_valid(self):
        """هل الاشتراك ساري"""
        if not self.is_active:
            return False
        if not self.subscription_expires:
            return True
        return self.subscription_expires >= timezone.localdate()

    def days_until_expiry(self):
        """عدد الأيام المتبقية على انتهاء الاشتراك"""
        if not self.subscription_expires:
            return None
        delta = self.subscription_expires - timezone.localdate()
        return delta.days

    def plan_allows(self, feature: str) -> bool:
        """هل الباقة الحالية تتيح ميزة معينة"""
        return bool(self.PLAN_FEATURES.get(self.subscription_plan, {}).get(feature, False))

    def auto_backup_daily_count(self) -> int:
        """عدد النسخ الاحتياطية التلقائية يومياً حسب الباقة"""
        return self.PLAN_FEATURES.get(self.subscription_plan, {}).get('auto_backup_daily', 0)


# ============================================
# TENANT CAPABILITIES (قدرات النشاط التجاري)
# ============================================

class TenantCapabilities(models.Model):
    """
    القدرات التشغيلية لكل tenant.
    تُملأ تلقائياً من BusinessType عند الإنشاء، وقابلة للتعديل لاحقاً.
    """

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        related_name='capabilities'
    )

    # تتبع المنتجات
    has_expiry_dates = models.BooleanField('تواريخ انتهاء الصلاحية', default=False)
    has_batch_numbers = models.BooleanField('أرقام الدُفعات / الباتش', default=False)
    has_serial_numbers = models.BooleanField('أرقام تسلسلية', default=False)

    # الكميات
    has_weight_items = models.BooleanField('منتجات بالوزن أو الحجم', default=False)

    # العمليات
    has_services = models.BooleanField('بنود الخدمة', default=False)
    has_manufacturing = models.BooleanField('التصنيع والوصفات من مواد خام', default=False)
    has_work_orders = models.BooleanField('أوامر العمل', default=False)

    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)

    class Meta:
        db_table = 'tenant_capabilities'
        verbose_name = 'قدرات النشاط'
        verbose_name_plural = 'قدرات الأنشطة'

    def __str__(self):
        return f"قدرات {self.tenant.name}"

    @classmethod
    def from_business_type(cls, tenant):
        """إنشاء قدرات tenant من features نوع النشاط التجاري"""
        features = tenant.business_type.features if tenant.business_type else {}
        return cls(
            tenant=tenant,
            has_expiry_dates=features.get('has_expiry_dates', False),
            has_batch_numbers=features.get('has_batch_numbers', False),
            has_serial_numbers=features.get('has_serial_numbers', False),
            has_weight_items=features.get('has_weight_items', False),
            has_services=features.get('has_services', False),
            has_manufacturing=features.get('has_manufacturing', False),
            has_work_orders=features.get('has_work_orders', False),
        )


# ============================================
# SETTINGS
# ============================================

class Settings(models.Model):
    """إعدادات خاصة بكل Tenant"""
    
    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        related_name='settings'
    )
    
    # Invoice Settings
    invoice_prefix = models.CharField('بادئة الفاتورة', max_length=10, default='INV')
    invoice_footer = models.TextField('تذييل الفاتورة', blank=True)
    invoice_color = models.CharField('لون الفاتورة', max_length=7, default='#6366f1')
    print_sale_invoice = models.BooleanField('طباعة فاتورة البيع', default=True)
    print_purchase_invoice = models.BooleanField('طباعة فاتورة الشراء', default=True)
    print_agent_name = models.BooleanField('طباعة اسم المندوب في الفاتورة', default=False)
    
    # Tax Settings
    tax_enabled = models.BooleanField('تفعيل الضرائب', default=False)
    tax_value = models.DecimalField('قيمة الضريبة %', max_digits=5, decimal_places=2, default=0)
    tax_number = models.CharField('الرقم الضريبي', max_length=50, blank=True)
    
    # Stock Settings
    show_zero_stock = models.BooleanField('إظهار منتجات بمخزون صفر', default=True)
    low_stock_alert = models.BooleanField('تنبيه المخزون المنخفض', default=True)
    
    # Display Settings
    items_per_page = models.IntegerField('عدد العناصر في الصفحة', default=25)
    date_format = models.CharField('تنسيق التاريخ', max_length=20, default='%Y-%m-%d')
    
    # Security
    delete_password = models.CharField('كلمة مرور الحذف', max_length=100, default='delete123')
    
    # Notifications
    email_notifications = models.BooleanField('إشعارات البريد', default=True)
    sms_notifications = models.BooleanField('إشعارات SMS', default=False)
    
    # Custom Settings
    custom_settings = models.JSONField('إعدادات مخصصة', default=dict, blank=True)
    
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)
    
    class Meta:
        db_table = 'tenant_settings'
        verbose_name = 'إعدادات'
        verbose_name_plural = 'إعدادات المشتركين'
    
    def __str__(self):
        return f"إعدادات {self.tenant.name}"


# ============================================
# PLATFORM SETTINGS (إعدادات المنصة - Singleton)
# ============================================

class PlatformSettings(models.Model):
    """إعدادات المنصة العامة — Singleton (سجل واحد فقط بـ pk=1)"""

    ANNOUNCEMENT_TYPES = [
        ('info',    'معلوماتي'),
        ('warning', 'تحذير'),
        ('success', 'نجاح'),
        ('danger',  'خطر'),
    ]

    # ── هوية المنصة ─────────────────────────────
    platform_name    = models.CharField('اسم المنصة', max_length=100, default='EnjazIMS')
    platform_tagline = models.CharField('الشعار النصي', max_length=200, blank=True)
    platform_logo    = models.ImageField('الشعار', upload_to='platform/', blank=True, null=True)
    platform_favicon = models.ImageField('الأيقونة', upload_to='platform/', blank=True, null=True)
    primary_color    = models.CharField('اللون الأساسي', max_length=7, default='#6366f1')
    footer_text      = models.CharField('نص الفوتر', max_length=300, blank=True)

    # ── وضع الصيانة ─────────────────────────────
    maintenance_mode    = models.BooleanField('وضع الصيانة', default=False)
    maintenance_message = models.TextField('رسالة الصيانة', blank=True,
                                           default='النظام قيد الصيانة حالياً. سنعود قريباً.')

    # ── التسجيل الذاتي ──────────────────────────
    self_registration_enabled = models.BooleanField('تفعيل التسجيل الذاتي', default=True)

    # ── الإشعار العام ───────────────────────────
    announcement_active = models.BooleanField('تفعيل الإشعار', default=False)
    announcement_text   = models.CharField('نص الإشعار', max_length=500, blank=True)
    announcement_type   = models.CharField('نوع الإشعار', max_length=10,
                                           choices=ANNOUNCEMENT_TYPES, default='info')

    # ── الإعدادات الافتراضية للمشتركين الجدد ───
    default_currency       = models.CharField('العملة الافتراضية', max_length=3, default='SDG')
    default_timezone       = models.CharField('المنطقة الزمنية الافتراضية', max_length=50,
                                              default='Africa/Khartoum')
    default_tax_enabled    = models.BooleanField('تفعيل الضريبة افتراضياً', default=False)
    default_tax_value      = models.DecimalField('نسبة الضريبة الافتراضية %',
                                                 max_digits=5, decimal_places=2, default=0)
    default_invoice_prefix = models.CharField('بادئة الفاتورة الافتراضية', max_length=10, default='INV')
    default_trial_days     = models.IntegerField('أيام التجربة المجانية', default=30)

    updated_at = models.DateTimeField('آخر تحديث', auto_now=True)

    class Meta:
        db_table = 'platform_settings'
        verbose_name = 'إعدادات المنصة'
        verbose_name_plural = 'إعدادات المنصة'

    def __str__(self):
        return 'إعدادات المنصة'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ============================================
# TENANT MIXIN (للـ Models المشتركة)
# ============================================

class TenantMixin(models.Model):
    """
    Base Model للـ models المشتركة
    كل model يرث من هذا سيكون مرتبط بـ tenant
    """
    
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        db_index=True
    )
    
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)
    
    # Track who created/updated
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_created',
        verbose_name='أنشئ بواسطة'
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='%(class)s_updated',
        verbose_name='عُدل بواسطة'
    )
    
    objects = TenantManager()
    
    class Meta:
        abstract = True


# ============================================
# ACTIVITY LOG
# ============================================

class ActivityLog(models.Model):
    """
    سجل النشاطات - لتتبع كل العمليات
    """
    
    ACTION_TYPES = (
        ('create', 'إضافة'),
        ('update', 'تعديل'),
        ('delete', 'حذف'),
        ('login', 'تسجيل دخول'),
        ('logout', 'تسجيل خروج'),
        ('view', 'عرض'),
        ('export', 'تصدير'),
        ('import', 'استيراد'),
    )
    
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        related_name='activity_logs'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name='المستخدم',
        related_name='activity_logs'
    )
    
    action = models.CharField('الإجراء', max_length=20, choices=ACTION_TYPES)
    model_name = models.CharField('النموذج', max_length=100, blank=True)
    object_id = models.IntegerField('معرف العنصر', null=True, blank=True)
    description = models.TextField('الوصف')
    
    ip_address = models.GenericIPAddressField('عنوان IP', null=True, blank=True)
    user_agent = models.TextField('User Agent', blank=True)
    
    metadata = models.JSONField('بيانات إضافية', default=dict, blank=True)
    
    created_at = models.DateTimeField('التاريخ', auto_now_add=True, db_index=True)
    
    class Meta:
        db_table = 'activity_logs'
        verbose_name = 'سجل نشاط'
        verbose_name_plural = 'سجلات النشاطات'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', '-created_at']),
            models.Index(fields=['user', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.user} - {self.get_action_display()} - {self.created_at}"


# ============================================
# SUPPORT TICKETS (تذاكر الدعم الفني)
# ============================================

class SupportTicket(models.Model):
    """تذكرة دعم فني - من المشترك إلى مشرف النظام"""

    STATUS_CHOICES = (
        ('open', 'مفتوحة'),
        ('in_progress', 'قيد المعالجة'),
        ('resolved', 'محلولة'),
        ('closed', 'مغلقة'),
    )

    PRIORITY_CHOICES = (
        ('low', 'منخفضة'),
        ('medium', 'متوسطة'),
        ('high', 'عالية'),
        ('urgent', 'عاجلة'),
    )

    CATEGORY_CHOICES = (
        ('technical', 'مشكلة تقنية'),
        ('billing', 'الفاتورة والاشتراك'),
        ('feature', 'طلب ميزة'),
        ('account', 'الحساب والمستخدمين'),
        ('other', 'أخرى'),
    )

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        verbose_name='المشترك',
        related_name='support_tickets'
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name='أنشئ بواسطة',
        related_name='created_tickets'
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='مسند إلى',
        related_name='assigned_tickets'
    )

    subject = models.CharField('الموضوع', max_length=300)
    description = models.TextField('الوصف')
    category = models.CharField('التصنيف', max_length=20, choices=CATEGORY_CHOICES, default='other')
    priority = models.CharField('الأولوية', max_length=10, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField('الحالة', max_length=15, choices=STATUS_CHOICES, default='open')

    last_reply_at = models.DateTimeField('آخر رد', null=True, blank=True)
    closed_at = models.DateTimeField('تاريخ الإغلاق', null=True, blank=True)

    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)

    class Meta:
        db_table = 'support_tickets'
        verbose_name = 'تذكرة دعم'
        verbose_name_plural = 'تذاكر الدعم'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', '-created_at']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f"#{self.pk} – {self.subject}"

    def is_open(self):
        return self.status in ('open', 'in_progress')


class SupportMessage(models.Model):
    """رسالة ضمن تذكرة دعم"""

    SENDER_TYPE_CHOICES = (
        ('tenant', 'المشترك'),
        ('admin', 'المشرف'),
    )

    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        verbose_name='التذكرة',
        related_name='messages'
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name='المرسل'
    )
    sender_type = models.CharField('نوع المرسل', max_length=10, choices=SENDER_TYPE_CHOICES)
    body = models.TextField('الرسالة')

    created_at = models.DateTimeField('التاريخ', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'support_messages'
        verbose_name = 'رسالة دعم'
        verbose_name_plural = 'رسائل الدعم'
        ordering = ['created_at']

    def __str__(self):
        return f"رسالة في #{self.ticket_id} من {self.sender}"


# ============================================
# TENANT BACKUPS (النسخ الاحتياطية)
# ============================================

class TenantBackup(models.Model):
    """نسخة احتياطية لبيانات مشترك معين"""

    BACKUP_TYPES = (
        ('manual', 'يدوي'),
        ('auto', 'تلقائي'),
        ('pre_restore', 'نسخة أمان'),
    )

    STATUS_CHOICES = (
        ('completed', 'مكتمل'),
        ('failed', 'فشل'),
        ('in_progress', 'جاري'),
    )

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='backups',
        verbose_name='المشترك',
    )
    filename = models.CharField('اسم الملف', max_length=255)
    file_path = models.CharField('مسار الملف', max_length=500)
    file_size = models.BigIntegerField('الحجم بالبايت', default=0)
    backup_type = models.CharField('نوع النسخة', max_length=20, choices=BACKUP_TYPES, default='manual')
    status = models.CharField('الحالة', max_length=20, choices=STATUS_CHOICES, default='in_progress')
    notes = models.TextField('ملاحظات', blank=True)
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)

    class Meta:
        db_table = 'tenant_backups'
        verbose_name = 'نسخة احتياطية'
        verbose_name_plural = 'النسخ الاحتياطية'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.tenant.name} — {self.filename}"

    @property
    def file_size_display(self):
        if self.status != 'completed':
            return '—'
        size = self.file_size
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.2f} MB"

    @property
    def file_exists(self):
        from pathlib import Path
        return Path(self.file_path).exists()


# ============================================
# ADMIN NOTIFICATIONS (إشعارات مدير النظام)
# ============================================

class AdminNotification(models.Model):
    """إشعارات خاصة بمدير النظام (السوبر يوزر) — مستقلة عن إشعارات المشتركين."""

    TYPE_CHOICES = [
        ('new_tenant',            'مشترك جديد'),
        ('new_ticket',            'تذكرة دعم جديدة'),
        ('subscription_expiring', 'اشتراك قارب الانتهاء'),
        ('subscription_expired',  'اشتراك منتهٍ'),
        ('old_ticket',            'تذكرة مفتوحة منذ فترة'),
        ('general',               'عام'),
    ]
    PRIORITY_CHOICES = [('low', 'منخفضة'), ('medium', 'متوسطة'), ('high', 'عالية')]

    notification_type = models.CharField('النوع', max_length=30, choices=TYPE_CHOICES, default='general')
    priority          = models.CharField('الأولوية', max_length=10, choices=PRIORITY_CHOICES, default='medium')
    title             = models.CharField('العنوان', max_length=200)
    message           = models.TextField('الرسالة')
    is_read           = models.BooleanField('مقروء', default=False)
    link              = models.CharField('الرابط', max_length=300, blank=True)
    ref_key           = models.CharField('مفتاح التكرار', max_length=120, unique=True, blank=True)
    created_at        = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'admin_notifications'
        verbose_name = 'إشعار إداري'
        verbose_name_plural = 'الإشعارات الإدارية'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    @classmethod
    def push(cls, *, notification_type, title, message, link='', priority='medium', ref_key=''):
        """Create a notification only if the ref_key hasn't been used before."""
        if ref_key and cls.objects.filter(ref_key=ref_key).exists():
            return None
        return cls.objects.create(
            notification_type=notification_type,
            title=title,
            message=message,
            link=link,
            priority=priority,
            ref_key=ref_key,
        )


class SocialMediaPost(models.Model):
    """منشور تسويقي جاهز للنشر على وسائل التواصل — محتوى خاص بالمنصة نفسها، وليس ببيانات مشترك."""

    CATEGORY_CHOICES = [
        ('comprehensive',  'منشور شامل'),
        ('problem',        'مشكلة'),
        ('feature',        'ميزة'),
        ('business_type',  'حسب نوع النشاط'),
        ('trust',          'ثقة وأمان'),
        ('objection',      'معالجة اعتراض'),
        ('tip',            'نصيحة تجارية'),
        ('comparison',     'مقارنة'),
        ('cta',            'دعوة مباشرة'),
        ('engagement',     'تفاعلي'),
        ('sudan_context',  'السياق السوداني'),
    ]

    PUBLISH_CHANNEL_CHOICES = [
        ('facebook',  'فيسبوك'),
        ('instagram', 'إنستقرام'),
        ('whatsapp',  'واتساب'),
        ('tiktok',    'تيك توك'),
        ('other',     'أخرى'),
    ]

    category        = models.CharField('التصنيف', max_length=20, choices=CATEGORY_CHOICES, default='feature', db_index=True)
    content         = models.TextField('المحتوى')
    is_ai_generated = models.BooleanField('مولّد بالذكاء الاصطناعي', default=False)
    is_published    = models.BooleanField('تم النشر', default=False, db_index=True)
    published_at    = models.DateField('تاريخ النشر', null=True, blank=True)
    published_channel = models.CharField('قناة النشر', max_length=20, choices=PUBLISH_CHANNEL_CHOICES, blank=True)
    created_at      = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True, db_index=True)
    updated_at      = models.DateTimeField('تاريخ التحديث', auto_now=True)
    created_by      = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name='أنشأه'
    )

    class Meta:
        db_table = 'social_media_posts'
        verbose_name = 'منشور تسويقي'
        verbose_name_plural = 'المنشورات التسويقية'
        ordering = ['-created_at']

    def __str__(self):
        return self.content[:50]


class ExchangeRateHistory(models.Model):
    tenant     = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='exchange_rate_history', db_index=True)
    rate       = models.DecimalField('سعر الصرف', max_digits=12, decimal_places=2)
    changed_at = models.DateTimeField('وقت التغيير', auto_now_add=True, db_index=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, verbose_name='بواسطة'
    )
    notes = models.CharField('ملاحظة', max_length=200, blank=True)

    class Meta:
        db_table      = 'exchange_rate_history'
        ordering      = ['-changed_at']
        verbose_name  = 'سجل سعر الصرف'
        verbose_name_plural = 'سجل أسعار الصرف'

    def __str__(self):
        return f'{self.tenant} — {self.rate} @ {self.changed_at:%Y-%m-%d}'
