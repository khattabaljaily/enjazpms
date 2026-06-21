"""
Stocks Models - نماذج المخازن
يدعم 3 أنواع من الإعداد:
  - single_store:  مخزن واحد فقط
  - multi_stock:   عدة مخازن تحت محل واحد
  - multi_branch:  مخازن متعددة موزعة على فروع متعددة

العلاقة بين المنتجات والمخازن:
  Item ←→ StockQuantity ←→ Stock
  - كل منتج جديد يحصل تلقائياً على سجل StockQuantity (كمية=0) في كل مخازن الـ tenant
  - كل مخزن جديد يحصل تلقائياً على سجل StockQuantity (كمية=0) لكل منتجات الـ tenant
  - هذا يتم عبر Django Signals في ملف signals.py
"""
from django.db import models
from django.db.models import Sum
from apps.core.models import TenantMixin


# ============================================
# STOCK (المخزن)
# ============================================

class Stock(TenantMixin):
    """
    المخزن الفيزيائي - المكان الذي تُحفظ فيه البضائع.

    - في single_store: يُنشأ تلقائياً مخزن واحد ولا يستطيع المستخدم إضافة آخر.
    - في multi_stock:  يستطيع المستخدم إضافة مخازن متعددة مرتبطة بنفس المحل.
    - في multi_branch: كل مخزن مرتبط بفرع (branch) معين.
    """

    TYPE_CHOICES = (
        ('main', 'مخزن رئيسي'),
        ('branch', 'مخزن فرع'),
    )

    # ------ معلومات أساسية ------
    name = models.CharField('اسم المخزن', max_length=200)
    code = models.CharField('الرمز', max_length=20, blank=True)
    stock_type = models.CharField(
        'نوع المخزن', max_length=20,
        choices=TYPE_CHOICES, default='main'
    )

    # ------ الفرع المرتبط (للنسخة multi_branch فقط) ------
    # سيتم ربطه بـ branches.Branch عند إنشاء تطبيق الفروع
    # branch = models.ForeignKey('branches.Branch', ...)
    branch_name = models.CharField(
        'اسم الفرع', max_length=200, blank=True,
        help_text='يُستخدم مؤقتاً حتى يتم إنشاء تطبيق الفروع'
    )

    # ------ تفاصيل إضافية ------
    address = models.TextField('العنوان / الموقع', blank=True)
    notes = models.TextField('ملاحظات', blank=True)
    is_active = models.BooleanField('نشط', default=True)
    is_default = models.BooleanField(
        'المخزن الافتراضي', default=False,
        help_text='المخزن الذي تُضاف إليه البضاعة تلقائياً عند الشراء'
    )
    is_system_default = models.BooleanField(
        'افتراضي نظامي',
        default=False,
        help_text='مخزن نظامي يُنشأ تلقائياً مع الاشتراك ولا يمكن حذفه.',
    )

    class Meta:
        db_table = 'stocks'
        verbose_name = 'مخزن'
        verbose_name_plural = 'المخازن'
        ordering = ['-is_default', 'name']
        # كل tenant له رمز مخزن فريد
        unique_together = [('tenant', 'code')]
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['tenant', 'is_default']),
            models.Index(fields=['tenant', 'is_system_default']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_stock_type_display()})"

    def save(self, *args, **kwargs):
        # توليد رمز تلقائي إذا لم يُحدَّد
        if not self.code:
            last = (
                Stock.objects.filter(tenant=self.tenant)
                .exclude(code='')
                .order_by('-id')
                .first()
            )
            next_num = 1
            if last and last.code.startswith('WH-'):
                try:
                    next_num = int(last.code.split('-')[-1]) + 1
                except ValueError:
                    next_num = Stock.objects.filter(tenant=self.tenant).count() + 1
            self.code = f"WH-{next_num:03d}"

        # إذا كان هذا هو أول مخزن، اجعله افتراضياً
        if not self.pk and not Stock.objects.filter(tenant=self.tenant).exists():
            self.is_default = True

        super().save(*args, **kwargs)

    @staticmethod
    def can_add_stock(tenant):
        """
        هل يستطيع هذا الـ tenant إضافة مخزن جديد؟
        يُستخدم في الـ view للتحقق قبل السماح بالإضافة.
        """
        current_count = Stock.objects.filter(tenant=tenant, is_active=True).count()
        return current_count < tenant.max_stocks


# ============================================
# STOCK QUANTITY (كميات المنتجات في المخازن)
# ============================================

class StockQuantity(TenantMixin):
    """
    يربط منتجاً بمخزن ويحتفظ بالكمية المتاحة.

    هذا الجدول هو المصدر الوحيد للحقيقة لكميات المخزون.
    يُنشأ تلقائياً (كمية=0) عبر Signals في الحالتين:
      - إضافة منتج جديد  → يُنشأ سجل في كل مخازن الـ tenant
      - إضافة مخزن جديد  → يُنشأ سجل لكل منتجات الـ tenant

    الكميات تتغير فقط عبر:
      - فواتير الشراء  (StockMovement type=in)
      - فواتير البيع   (StockMovement type=out)
      - تحويلات المخزن (StockMovement type=transfer)
      - تسوية الجرد    (StockMovement type=adjustment)
    لا يُسمح بتعديل الكمية مباشرة من هذا الجدول.
    """

    stock = models.ForeignKey(
        Stock,
        on_delete=models.CASCADE,
        related_name='quantities',
        verbose_name='المخزن'
    )
    # ForeignKey إلى Item عبر string reference لتجنب circular imports
    item = models.ForeignKey(
        'items.Item',
        on_delete=models.CASCADE,
        related_name='stock_quantities',
        verbose_name='المنتج'
    )
    quantity = models.DecimalField(
        'الكمية المتاحة', max_digits=14, decimal_places=4, default=0
    )
    opening_quantity = models.DecimalField(
        'الكمية الافتتاحية', max_digits=14, decimal_places=4, default=0,
        help_text='الكمية الافتتاحية المُدخلة — لا تتأثر بحركات البيع والشراء'
    )
    reserved_quantity = models.DecimalField(
        'الكمية المحجوزة', max_digits=14, decimal_places=4, default=0,
        help_text='كميات مخصصة لأوامر بيع لم تُسلَّم بعد'
    )
    min_quantity = models.DecimalField(
        'حد الطلب الأدنى (للمخزن)', max_digits=12, decimal_places=4, default=0,
        help_text='تنبيه نقص مخزون خاص بهذا المخزن (يتجاوز حد المنتج العام)'
    )

    class Meta:
        db_table = 'stock_quantities'
        verbose_name = 'كمية مخزون'
        verbose_name_plural = 'كميات المخزون'
        # كل منتج يظهر مرة واحدة فقط في كل مخزن
        unique_together = [('tenant', 'stock', 'item')]
        indexes = [
            models.Index(fields=['tenant', 'stock']),
            models.Index(fields=['tenant', 'item']),
            models.Index(fields=['tenant', 'stock', 'quantity']),
        ]

    def __str__(self):
        return f"{self.item.name} @ {self.stock.name} = {self.quantity}"

    @property
    def available_quantity(self):
        """الكمية الفعلية المتاحة للبيع (بعد طرح المحجوز)"""
        return self.quantity - self.reserved_quantity

    @property
    def is_low_stock(self):
        """هل الكمية أقل من حد الطلب الأدنى؟"""
        threshold = self.min_quantity or self.item.min_quantity
        if threshold <= 0:
            return False
        return self.quantity <= threshold

    @classmethod
    def get_total_quantity(cls, item, tenant):
        """مجموع الكمية في كل المخازن لمنتج معين"""
        result = cls.objects.filter(
            tenant=tenant, item=item
        ).aggregate(total=Sum('quantity'))
        return result['total'] or 0


# ============================================
# STOCK TRANSFER (تحويل بين المخازن)
# ============================================

class StockTransfer(TenantMixin):
    STATUS_CHOICES = (
        ('draft',     'مسودة'),
        ('confirmed', 'مؤكد'),
        ('cancelled', 'ملغي'),
    )

    transfer_number = models.CharField('رقم التحويل', max_length=30, blank=True)
    transfer_date   = models.DateField('تاريخ التحويل')
    from_stock      = models.ForeignKey(
        Stock, on_delete=models.PROTECT,
        related_name='transfers_out', verbose_name='من مخزن'
    )
    to_stock        = models.ForeignKey(
        Stock, on_delete=models.PROTECT,
        related_name='transfers_in', verbose_name='إلى مخزن'
    )
    status          = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='draft')
    notes           = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'stock_transfers'
        verbose_name = 'تحويل مخزون'
        verbose_name_plural = 'تحويلات المخزون'
        ordering = ['-transfer_date', '-id']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', '-transfer_date']),
        ]

    def __str__(self):
        return f"{self.transfer_number} ({self.from_stock} → {self.to_stock})"

    def save(self, *args, **kwargs):
        if not self.transfer_number:
            last = (
                StockTransfer.objects.filter(tenant=self.tenant)
                .exclude(transfer_number='')
                .order_by('-id').first()
            )
            next_num = 1
            if last and last.transfer_number.startswith('TRF-'):
                try:
                    next_num = int(last.transfer_number.split('-')[-1]) + 1
                except ValueError:
                    pass
            self.transfer_number = f"TRF-{next_num:05d}"
        super().save(*args, **kwargs)

    @property
    def total_lines(self):
        return self.lines.count()


class StockTransferLine(TenantMixin):
    transfer = models.ForeignKey(
        StockTransfer, on_delete=models.CASCADE,
        related_name='lines', verbose_name='التحويل'
    )
    item     = models.ForeignKey(
        'items.Item', on_delete=models.PROTECT,
        related_name='transfer_lines', verbose_name='المنتج'
    )
    quantity = models.DecimalField('الكمية', max_digits=12, decimal_places=4)
    notes    = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'stock_transfer_lines'
        verbose_name = 'بند تحويل'
        verbose_name_plural = 'بنود التحويل'

    def __str__(self):
        return f"{self.item.name} × {self.quantity}"


# ============================================
# STOCKTAKE / INVENTORY COUNT (جرد المخزون)
# ============================================

class Stocktake(TenantMixin):
    """
    جلسة جرد مخزون.
    - draft:     جاري تسجيل الأعداد الفعلية
    - confirmed: تم تطبيق الفروقات على المخزون
    - cancelled: ألغيت الجلسة بدون تطبيق
    """
    STATUS_CHOICES = (
        ('draft',     'جاري الجرد'),
        ('confirmed', 'مكتمل'),
        ('cancelled', 'ملغي'),
    )

    stocktake_number = models.CharField('رقم الجرد', max_length=30, blank=True)
    stocktake_date   = models.DateField('تاريخ الجرد')
    stock            = models.ForeignKey(
        Stock, on_delete=models.PROTECT,
        related_name='stocktakes', verbose_name='المخزن'
    )
    status           = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='draft')
    notes            = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'stocktakes'
        verbose_name = 'جرد مخزون'
        verbose_name_plural = 'جرد المخزون'
        ordering = ['-stocktake_date', '-id']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', '-stocktake_date']),
        ]

    def __str__(self):
        return f"{self.stocktake_number} — {self.stock.name}"

    def save(self, *args, **kwargs):
        if not self.stocktake_number:
            last = (
                Stocktake.objects.filter(tenant=self.tenant)
                .exclude(stocktake_number='')
                .order_by('-id').first()
            )
            next_num = 1
            if last and last.stocktake_number.startswith('INV-'):
                try:
                    next_num = int(last.stocktake_number.split('-')[-1]) + 1
                except ValueError:
                    pass
            self.stocktake_number = f"INV-{next_num:05d}"
        super().save(*args, **kwargs)


class StocktakeLine(TenantMixin):
    stocktake        = models.ForeignKey(
        Stocktake, on_delete=models.CASCADE,
        related_name='lines', verbose_name='الجرد'
    )
    item             = models.ForeignKey(
        'items.Item', on_delete=models.PROTECT,
        related_name='stocktake_lines', verbose_name='المنتج'
    )
    system_quantity  = models.DecimalField(
        'كمية النظام', max_digits=12, decimal_places=4, default=0,
        help_text='الكمية في النظام عند بدء الجرد'
    )
    counted_quantity = models.DecimalField(
        'الكمية المعدودة', max_digits=12, decimal_places=4, default=0
    )
    notes            = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'stocktake_lines'
        verbose_name = 'بند جرد'
        verbose_name_plural = 'بنود الجرد'
        unique_together = [('stocktake', 'item')]

    def __str__(self):
        return f"{self.item.name}: {self.system_quantity} → {self.counted_quantity}"

    @property
    def difference(self):
        return self.counted_quantity - self.system_quantity


# ============================================
# MANUFACTURING ORDER (أوامر التصنيع)
# ============================================

class ManufacturingOrder(TenantMixin):
    """أمر تصنيع — تنفيذ وصفة BOM"""
    STATUS_CHOICES = (
        ('draft', 'مسودة'),
        ('confirmed', 'مؤكد'),
        ('cancelled', 'ملغي'),
    )
    order_number = models.CharField('رقم الأمر', max_length=30, blank=True)
    recipe = models.ForeignKey(
        'items.BOMRecipe', on_delete=models.PROTECT,
        related_name='manufacturing_orders', verbose_name='الوصفة'
    )
    stock = models.ForeignKey(
        Stock, on_delete=models.PROTECT,
        related_name='manufacturing_orders', verbose_name='المخزن'
    )
    quantity = models.DecimalField('الكمية المنتجة', max_digits=12, decimal_places=4)
    order_date = models.DateField('تاريخ الأمر')
    status = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='draft')
    cost = models.DecimalField('التكلفة الكلية', max_digits=14, decimal_places=2, default=0)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'manufacturing_orders'
        verbose_name = 'أمر تصنيع'
        verbose_name_plural = 'أوامر التصنيع'
        ordering = ['-order_date', '-id']

    def __str__(self):
        return self.order_number or f"MFG-{self.pk}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            last = ManufacturingOrder.objects.filter(
                tenant=self.tenant,
                order_number__startswith='MFG-'
            ).order_by('-id').first()
            num = 1
            if last:
                try:
                    num = int(last.order_number.split('-')[-1]) + 1
                except (ValueError, IndexError):
                    pass
            self.order_number = f"MFG-{num:05d}"
        super().save(*args, **kwargs)
