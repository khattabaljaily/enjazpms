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
# TENANT (العميل/النشاط التجاري)
# ============================================

class Tenant(models.Model):
    """
    Tenant - العميل/النشاط التجاري
    كل عميل له بياناته المنفصلة تماماً
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
    
    # Meta
    created_at = models.DateTimeField('تاريخ الإنشاء', auto_now_add=True)
    updated_at = models.DateTimeField('تاريخ التحديث', auto_now=True)
    
    class Meta:
        db_table = 'tenants'
        verbose_name = 'عميل'
        verbose_name_plural = 'العملاء'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['is_active']),
        ]
    
    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
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
        return self.subscription_expires >= timezone.now().date()
    
    def days_until_expiry(self):
        """عدد الأيام المتبقية على انتهاء الاشتراك"""
        if not self.subscription_expires:
            return None
        delta = self.subscription_expires - timezone.now().date()
        return delta.days


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
        verbose_name='العميل',
        related_name='capabilities'
    )

    # تتبع المنتجات
    has_expiry_dates = models.BooleanField('تواريخ انتهاء الصلاحية', default=False)
    has_batch_numbers = models.BooleanField('أرقام الدُفعات / الباتش', default=False)
    has_serial_numbers = models.BooleanField('أرقام تسلسلية', default=False)

    # الكميات والتشكيلات
    has_weight_items = models.BooleanField('منتجات بالوزن أو الحجم', default=False)
    has_variants = models.BooleanField('متغيرات (مقاسات / ألوان)', default=False)

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
            has_variants=features.get('has_variants', False),
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
        verbose_name='العميل',
        related_name='settings'
    )
    
    # Invoice Settings
    invoice_prefix = models.CharField('بادئة الفاتورة', max_length=10, default='INV')
    invoice_footer = models.TextField('تذييل الفاتورة', blank=True)
    print_sale_invoice = models.BooleanField('طباعة فاتورة البيع', default=True)
    print_purchase_invoice = models.BooleanField('طباعة فاتورة الشراء', default=True)
    
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
        verbose_name_plural = 'إعدادات العملاء'
    
    def __str__(self):
        return f"إعدادات {self.tenant.name}"


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
        verbose_name='العميل',
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
        verbose_name='العميل',
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
