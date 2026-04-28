"""
Items Models - نماذج المنتجات والأصناف
مصمم ليعمل مع جميع أنواع الأنشطة التجارية:
  - صيدلية:          track_expiry, track_batch
  - إلكترونيات:      track_serial
  - ملابس/أحذية:     has_variants (مقاسات × ألوان)
  - سوبرماركت:       track_expiry, barcode
  - شركة طبية/توزيع: track_batch, track_serial
"""
from django.db import models
from django.utils.text import slugify
from apps.core.models import TenantMixin


# ============================================
# CATEGORY (تصنيفات المنتجات)
# ============================================

class Category(TenantMixin):
    """
    تصنيف هرمي للمنتجات (Parent → Children).
    مثال: أدوية → مسكنات → باراسيتامول
    """

    name = models.CharField('اسم التصنيف', max_length=200)
    slug = models.SlugField('الرمز', max_length=220, blank=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='children',
        verbose_name='التصنيف الرئيسي'
    )
    icon = models.CharField('الأيقونة', max_length=50, blank=True, default='fa-tag')
    description = models.TextField('الوصف', blank=True)
    is_active = models.BooleanField('نشط', default=True)
    display_order = models.IntegerField('ترتيب العرض', default=0)

    class Meta:
        db_table = 'item_categories'
        verbose_name = 'تصنيف'
        verbose_name_plural = 'التصنيفات'
        ordering = ['display_order', 'name']
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['tenant', 'parent']),
        ]

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} › {self.name}"
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name) or f"cat-{self.tenant_id}"
            self.slug = f"{self.tenant_id}-{base_slug}"
        super().save(*args, **kwargs)

    @property
    def full_path(self):
        """المسار الكامل: الجد > الأب > الابن"""
        parts = [self.name]
        parent = self.parent
        while parent:
            parts.insert(0, parent.name)
            parent = parent.parent
        return ' › '.join(parts)

    @property
    def is_root(self):
        return self.parent is None


# ============================================
# UNIT (وحدات القياس)
# ============================================

class Unit(TenantMixin):
    """
    وحدة قياس المنتج.
    مثال: قطعة، كرتون (12 قطعة)، كيلوجرام، لتر
    يدعم الوحدات المتداخلة: وحدة أساسية + وحدة تعبئة
    """

    name = models.CharField('اسم الوحدة', max_length=100)
    abbreviation = models.CharField('الاختصار', max_length=20, blank=True)
    # للوحدات المركبة: كرتون = 12 قطعة
    base_unit = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sub_units',
        verbose_name='الوحدة الأساسية'
    )
    conversion_factor = models.DecimalField(
        'عامل التحويل', max_digits=10, decimal_places=4, default=1,
        help_text='كم وحدة أساسية تُعادل هذه الوحدة؟ مثال: كرتون = 12 قطعة'
    )
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'item_units'
        verbose_name = 'وحدة قياس'
        verbose_name_plural = 'وحدات القياس'
        ordering = ['name']
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        if self.abbreviation:
            return f"{self.name} ({self.abbreviation})"
        return self.name


# ============================================
# ITEM (المنتج / الصنف)
# ============================================

class Item(TenantMixin):
    """
    المنتج الأساسي - مشترك بين جميع أنواع الأنشطة التجارية.

    الحقول المشروطة (تظهر/تُفعَّل حسب نوع النشاط التجاري):
      track_expiry  → صيدليات، أغذية، مواد كيميائية
      track_batch   → صناعات، أدوية، أغذية
      track_serial  → إلكترونيات، أجهزة طبية
      has_variants  → ملابس، أحذية، أثاث
    """

    ITEM_TYPE_CHOICES = (
        ('product', 'منتج'),
        ('service', 'خدمة'),
        ('raw_material', 'مادة خام'),
        ('semi_finished', 'منتج نصف مصنع'),
    )

    # ------ معلومات أساسية ------
    name = models.CharField('اسم المنتج', max_length=300)
    name_en = models.CharField('الاسم بالإنجليزية', max_length=300, blank=True)
    sku = models.CharField('رمز المنتج (SKU)', max_length=100, blank=True)
    barcode = models.CharField('الباركود', max_length=100, blank=True)
    item_type = models.CharField(
        'نوع الصنف', max_length=20,
        choices=ITEM_TYPE_CHOICES, default='product'
    )

    # ------ التصنيف والوحدة ------
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='items',
        verbose_name='التصنيف'
    )
    unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='items',
        verbose_name='وحدة البيع'
    )
    purchase_unit = models.ForeignKey(
        Unit,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='purchase_items',
        verbose_name='وحدة الشراء'
    )

    # ------ التسعير ------
    cost_price = models.DecimalField(
        'سعر التكلفة', max_digits=14, decimal_places=2, default=0
    )
    selling_price = models.DecimalField(
        'سعر البيع', max_digits=14, decimal_places=2, default=0
    )
    min_selling_price = models.DecimalField(
        'الحد الأدنى للسعر', max_digits=14, decimal_places=2, default=0,
        help_text='أقل سعر يُسمح بالبيع به'
    )
    tax_rate = models.DecimalField(
        'نسبة الضريبة %', max_digits=5, decimal_places=2, default=0
    )

    # ------ المخزون ------
    min_quantity = models.DecimalField(
        'الحد الأدنى للمخزون', max_digits=12, decimal_places=4, default=0,
        help_text='يُنبّه عند الوصول لهذه الكمية'
    )
    max_quantity = models.DecimalField(
        'الحد الأقصى للمخزون', max_digits=12, decimal_places=4, default=0
    )

    # ------ خيارات التتبع (مشروطة بنوع النشاط) ------
    # الصيدليات، الأغذية، المواد الكيميائية
    track_expiry = models.BooleanField(
        'تتبع تاريخ الانتهاء', default=False
    )
    # الصناعات، الأدوية، الأغذية
    track_batch = models.BooleanField(
        'تتبع رقم الدفعة / الباتش', default=False
    )
    # الإلكترونيات، الأجهزة الطبية
    track_serial = models.BooleanField(
        'تتبع الرقم التسلسلي', default=False
    )
    # الملابس، الأحذية (مقاسات × ألوان)
    has_variants = models.BooleanField(
        'يحتوي على متغيرات (مقاسات/ألوان)', default=False
    )

    # ------ تفاصيل إضافية ------
    description = models.TextField('الوصف', blank=True)
    image = models.ImageField(
        'صورة المنتج', upload_to='items/', blank=True, null=True
    )
    is_active = models.BooleanField('نشط', default=True)
    is_sellable = models.BooleanField('قابل للبيع', default=True)
    is_purchasable = models.BooleanField('قابل للشراء', default=True)

    class Meta:
        db_table = 'items'
        verbose_name = 'منتج'
        verbose_name_plural = 'المنتجات'
        ordering = ['name']
        unique_together = [('tenant', 'sku')]
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['tenant', 'barcode']),
            models.Index(fields=['tenant', 'category']),
            models.Index(fields=['tenant', 'name']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # توليد SKU تلقائي إذا لم يُحدَّد
        if not self.sku:
            last = (
                Item.objects.filter(tenant=self.tenant)
                .exclude(sku='')
                .order_by('-id')
                .first()
            )
            next_num = 1
            if last and last.sku.startswith('ITM-'):
                try:
                    next_num = int(last.sku.split('-')[-1]) + 1
                except ValueError:
                    next_num = Item.objects.filter(tenant=self.tenant).count() + 1
            self.sku = f"ITM-{next_num:05d}"
        super().save(*args, **kwargs)


# ============================================
# ITEM VARIANT (متغيرات المنتج: مقاسات × ألوان)
# ============================================

class ItemVariant(TenantMixin):
    """
    تُستخدم فقط عند item.has_variants = True.
    مثال: تيشيرت أبيض مقاس L = variant منفصل بباركود وسعر مختلف.
    """

    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name='variants',
        verbose_name='المنتج الأساسي'
    )
    name = models.CharField('اسم المتغير', max_length=200,
                            help_text='مثال: أبيض - L')
    barcode = models.CharField('الباركود', max_length=100, blank=True)
    sku_suffix = models.CharField('لاحقة الرمز', max_length=20, blank=True,
                                   help_text='تُضاف لـ SKU الأصل، مثال: -WL')
    price_adjustment = models.DecimalField(
        'فرق السعر', max_digits=10, decimal_places=2, default=0,
        help_text='+ يعني أغلى، - يعني أرخص من الأصل'
    )
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'item_variants'
        verbose_name = 'متغير منتج'
        verbose_name_plural = 'متغيرات المنتجات'
        ordering = ['name']
        indexes = [
            models.Index(fields=['tenant', 'item']),
        ]

    def __str__(self):
        return f"{self.item.name} - {self.name}"

    @property
    def selling_price(self):
        return self.item.selling_price + self.price_adjustment
