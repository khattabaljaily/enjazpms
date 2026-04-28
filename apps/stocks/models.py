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
        ('cold', 'مخزن مبرد'),
        ('hazardous', 'مخزن مواد خطرة'),
        ('transit', 'مخزن عبور / أمانات'),
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
