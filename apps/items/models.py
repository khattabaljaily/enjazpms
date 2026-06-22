"""
Items Models - نماذج المنتجات والأصناف
مصمم ليعمل مع جميع أنواع الأنشطة التجارية:
  - صيدلية:          track_expiry, track_batch
  - إلكترونيات:      track_serial
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
    has_multiple_units = models.BooleanField('وحدات متعددة', default=False)

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

    # ------ أسعار العملة الصعبة (تُستخدم فقط عند تفعيل وضع العملة الصعبة) ------
    cost_price_hc = models.DecimalField(
        'سعر التكلفة بالعملة الصعبة', max_digits=14, decimal_places=4,
        null=True, blank=True
    )
    selling_price_hc = models.DecimalField(
        'سعر البيع بالعملة الصعبة', max_digits=14, decimal_places=4,
        null=True, blank=True
    )
    min_selling_price_hc = models.DecimalField(
        'الحد الأدنى بالعملة الصعبة', max_digits=14, decimal_places=4,
        null=True, blank=True
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

    @property
    def base_unit_name(self) -> str:
        """Returns the smallest unit name from ItemUnit, falls back to legacy unit FK."""
        try:
            iu = self.item_units.order_by('factor').first()
            if iu:
                return iu.name
        except Exception:
            pass
        return self.unit.name if self.unit else ''

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
# BOM RECIPE (وصفة التصنيع)
# ============================================

class BOMRecipe(TenantMixin):
    """وصفة التصنيع — مرتبطة بمنتج نهائي واحد"""
    item = models.OneToOneField(
        Item, on_delete=models.CASCADE, related_name='bom_recipe',
        verbose_name='المنتج النهائي'
    )
    notes = models.TextField('ملاحظات', blank=True)
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'bom_recipes'
        verbose_name = 'وصفة تصنيع'
        verbose_name_plural = 'وصفات التصنيع'

    def __str__(self):
        return f"وصفة: {self.item.name}"

    @property
    def total_cost(self):
        return sum(
            (line.component.cost_price * line.quantity)
            for line in self.lines.select_related('component').all()
        )


class BOMLine(TenantMixin):
    """مكوّن واحد في الوصفة"""
    recipe = models.ForeignKey(
        BOMRecipe, on_delete=models.CASCADE, related_name='lines'
    )
    component = models.ForeignKey(
        Item, on_delete=models.PROTECT, related_name='used_in_bom',
        verbose_name='المكوّن'
    )
    quantity = models.DecimalField('الكمية', max_digits=12, decimal_places=4)
    unit = models.ForeignKey(
        'ItemUnit', on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name='الوحدة'
    )
    notes = models.CharField('ملاحظات', max_length=200, blank=True)

    class Meta:
        db_table = 'bom_lines'
        verbose_name = 'مكوّن وصفة'
        verbose_name_plural = 'مكوّنات الوصفات'

    @property
    def line_total(self):
        return self.quantity * self.component.cost_price

    def __str__(self):
        return f"{self.component.name} × {self.quantity}"


# ============================================
# ITEM BATCH (دفعات المنتج)
# ============================================

class ItemBatch(TenantMixin):
    """تتبع الدفعات والمخزون لكل دفعة"""
    item = models.ForeignKey(
        Item, on_delete=models.CASCADE, related_name='batches',
        verbose_name='المنتج'
    )
    stock = models.ForeignKey(
        'stocks.Stock', on_delete=models.CASCADE, related_name='batches',
        verbose_name='المخزن'
    )
    batch_number = models.CharField('رقم الدفعة', max_length=100, blank=True)
    expiry_date = models.DateField('تاريخ انتهاء الصلاحية', null=True, blank=True)
    quantity_received = models.DecimalField('الكمية الواردة', max_digits=12, decimal_places=4, default=0)
    quantity_remaining = models.DecimalField('الكمية المتبقية', max_digits=12, decimal_places=4, default=0)
    purchase_date = models.DateField('تاريخ الشراء', null=True, blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'item_batches'
        verbose_name = 'دفعة'
        verbose_name_plural = 'الدفعات'
        ordering = ['expiry_date', 'batch_number']
        indexes = [
            models.Index(fields=['tenant', 'item', 'stock']),
            models.Index(fields=['tenant', 'expiry_date']),
        ]

    def __str__(self):
        return f"{self.item.name} — {self.batch_number or 'بدون رقم'}"

    @property
    def is_expired(self):
        from django.utils import timezone
        if self.expiry_date:
            return self.expiry_date < timezone.localdate()
        return False

    @property
    def days_to_expiry(self):
        from django.utils import timezone
        if self.expiry_date:
            return (self.expiry_date - timezone.localdate()).days
        return None


# ============================================
# ITEM UNIT (وحدات المنتج)
# ============================================

class ItemUnit(TenantMixin):
    """
    وحدات قياس مرتبطة بمنتج محدد.
    كل وحدة لها عامل تحويل بالنسبة للوحدة الأصغر (factor=1).
    مثال: حبة=1، كرتون=12، طرد=144
    """
    item = models.ForeignKey(
        Item,
        on_delete=models.CASCADE,
        related_name='item_units',
        verbose_name='المنتج'
    )
    name = models.CharField('اسم الوحدة', max_length=50)
    factor = models.DecimalField(
        'العامل', max_digits=10, decimal_places=4, default=1,
        help_text='الكمية بالنسبة للوحدة الأصغر — الوحدة الأصغر دائماً = 1'
    )

    class Meta:
        db_table      = 'item_product_units'
        ordering      = ['factor']
        verbose_name  = 'وحدة المنتج'
        unique_together = [('item', 'name')]
        indexes = [
            models.Index(fields=['tenant', 'item']),
        ]

    def __str__(self):
        return self.name
