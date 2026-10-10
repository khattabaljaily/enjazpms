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
from decimal import Decimal

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

    # ------ معلومات أساسية ------
    name = models.CharField('اسم المخزن', max_length=200)
    code = models.CharField('الرمز', max_length=20, blank=True)

    # ------ الفرع المرتبط (للنسخة multi_branch فقط) ------
    branch = models.ForeignKey(
        'core.Branch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='stocks', verbose_name='الفرع'
    )
    # المخزن المركزي للإدارة (نمط المشتريات "هجين" فقط): بلا فرع، تملكه الإدارة
    # وتوزّع منه على مخازن الفروع بشحنات. مخزن توزيع فقط (لا بيع منه)، ويختفي عن
    # مستخدمي الفروع لأنه بلا فرع. مخزن مركزي واحد فقط لكل مشترك.
    is_central = models.BooleanField(
        'مخزن مركزي', default=False,
        help_text='مخزن الإدارة المركزية: بلا فرع، للشراء المركزي والتوزيع على الفروع فقط.'
    )
    branch_name = models.CharField(
        'اسم الفرع (قديم)', max_length=200, blank=True,
        help_text='حقل قديم قبل إضافة موديل الفرع الحقيقي — يُستخدم كنسخة احتياطية فقط'
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
        return self.name

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
        نسخة "محل واحد بمخزن واحد" محدودة بمخزن واحد بغض النظر عن max_stocks
        (المخزن الأول لازم يُسمح به دائماً، حتى لو لم يُضبط للباقة بعدُ
        max_stocks صحيح لأي سبب).
        """
        current_count = Stock.objects.filter(tenant=tenant, is_active=True).count()
        if tenant.version_type == 'single_store':
            return current_count < 1
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
      - إتلاف مخزون    (StockMovement type=destruction_out)
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
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gte=0), name='stockquantity_quantity_gte_0'
            ),
            models.CheckConstraint(
                check=models.Q(reserved_quantity__gte=0), name='stockquantity_reserved_quantity_gte_0'
            ),
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
    """
    تحويل مخزون بين مخزنين.
    - داخل الفرع نفسه (أو نسخة بلا فروع): مسودة ← مؤكد، تنتقل الكمية فوراً.
    - بين فرعين (is_inter_branch): مسودة ← (إرسال من الفرع المحوِّل) في الطريق ←
      (اعتماد الفرع المستلِم للكمية الفعلية) تم الاستلام. الفرق يتحمّله الفرع المحوِّل:
      الزائد يُخصم من مخزنه عند الاستلام، والناقص يبقى معلّقاً (shortage_status=pending)
      حتى تسويته: إرجاع لمخزنه أو شطبه تالفاً بسجل إتلاف عليه.
    في نسخة الفروع التحويلات شغل الفروع وحدها؛ مدير النشاط لا يدخلها.
    """
    STATUS_CHOICES = (
        ('draft',      'مسودة'),
        ('confirmed',  'مؤكد'),
        ('in_transit', 'في الطريق'),
        ('received',   'تم الاستلام'),
        ('cancelled',  'ملغي'),
    )
    SHORTAGE_CHOICES = (
        ('none',        'لا يوجد'),
        ('pending',     'بانتظار التسوية'),
        ('restocked',   'أُعيد لمخزن الفرع المحوِّل'),
        ('written_off', 'شُطب تالفاً على الفرع المحوِّل'),
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

    # ------ التحويل بين الفروع ------
    is_inter_branch = models.BooleanField('بين فرعين', default=False)
    sent_at         = models.DateTimeField('وقت الإرسال', null=True, blank=True)
    received_at     = models.DateTimeField('وقت الاستلام', null=True, blank=True)
    received_by     = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='received_stock_transfers', verbose_name='استلمه'
    )
    has_difference  = models.BooleanField('فيه فرق استلام', default=False)
    shortage_status = models.CharField('حالة الناقص', max_length=12, choices=SHORTAGE_CHOICES, default='none')
    settled_at      = models.DateTimeField('وقت تسوية الناقص', null=True, blank=True)
    settled_by      = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='settled_stock_transfers', verbose_name='سوّى الناقص'
    )
    shortage_destruction = models.ForeignKey(
        'StockDestruction', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transfer_shortages', verbose_name='سجل إتلاف الناقص'
    )

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
    # للتحويل بين الفروع: الكمية الفعلية التي اعتمدها الفرع المستلِم، وتكلفة الوحدة وقت الإرسال.
    quantity_received = models.DecimalField('الكمية المستلَمة', max_digits=12, decimal_places=4, null=True, blank=True)
    unit_cost = models.DecimalField('تكلفة الوحدة', max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = 'stock_transfer_lines'
        verbose_name = 'بند تحويل'
        verbose_name_plural = 'بنود التحويل'

    def __str__(self):
        return f"{self.item.name} × {self.quantity}"

    @property
    def difference(self):
        """المستلَم − المرسل: موجب = زائد، سالب = ناقص، None قبل الاستلام."""
        if self.quantity_received is None:
            return None
        return self.quantity_received - self.quantity

    @property
    def shortage(self):
        diff = self.difference
        return -diff if diff is not None and diff < 0 else Decimal('0')


class StockTransferLineBatch(TenantMixin):
    """ما خرج من كل دفعة في تحويل بين فرعين، وما استُلم منها (FEFO عند الإرسال)."""
    line = models.ForeignKey(StockTransferLine, on_delete=models.CASCADE, related_name='batches', verbose_name='البند')
    source_batch = models.ForeignKey(
        'items.ItemBatch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='الدفعة المصدر')
    batch_number = models.CharField('رقم الدفعة', max_length=100, blank=True)
    expiry_date = models.DateField('تاريخ الانتهاء', null=True, blank=True)
    quantity_sent = models.DecimalField('المرسلة', max_digits=12, decimal_places=4)
    quantity_received = models.DecimalField('المستلَمة', max_digits=12, decimal_places=4, default=0)

    class Meta:
        db_table = 'stock_transfer_line_batches'
        verbose_name = 'دفعة بند تحويل'
        verbose_name_plural = 'دفعات بنود التحويل'
        ordering = ['id']

    @property
    def shortage(self):
        return max(self.quantity_sent - self.quantity_received, Decimal('0'))


# ============================================
# SHIPMENT (شحنة من المخزن المركزي إلى مخزن فرع) — النمط الهجين
# ============================================

class Shipment(TenantMixin):
    """
    شحنة توزيع من المخزن المركزي للإدارة إلى مخزن أحد الفروع.
    تخرج الكمية من المركزي عند الإرسال وتبقى "في الطريق" (بلا مهلة) حتى يؤكد
    مدير الفرع الكمية الفعلية المستلَمة؛ يدخل مخزن الفرع ما استُلم فعلاً، ويعود الفرق
    تلقائياً للمخزن المركزي مع تعليم الشحنة (has_difference) لمراجعة الإدارة.
    """
    STATUS_CHOICES = (
        ('draft',      'مسودة'),
        ('in_transit', 'في الطريق'),
        ('received',   'تم الاستلام'),
        ('cancelled',  'ملغية'),
    )

    # to_branch: توزيع من المخزن المركزي إلى الفرع (ترسلها الإدارة ويؤكد الفرع استلامها).
    # to_central: مرتجع من الفرع إلى المخزن المركزي (يرسله الفرع وتؤكد الإدارة استلامه)،
    # ثم تعيده الإدارة للمورد بمرتجع شراء مركزي.
    DIRECTION_CHOICES = (
        ('to_branch', 'توزيع من المركزي إلى الفرع'),
        ('to_central', 'مرتجع من الفرع إلى المركزي'),
    )
    direction = models.CharField('الاتجاه', max_length=10, choices=DIRECTION_CHOICES, default='to_branch')

    shipment_number = models.CharField('رقم الشحنة', max_length=30, blank=True)
    shipment_date = models.DateField('تاريخ الشحنة')
    from_stock = models.ForeignKey(
        Stock, on_delete=models.PROTECT, related_name='shipments_out', verbose_name='من المخزن')
    to_stock = models.ForeignKey(
        Stock, on_delete=models.PROTECT, related_name='shipments_in', verbose_name='إلى المخزن')
    # الفرع المعني بالشحنة (مشتق من مخزن الفرع في أي اتجاه)؛ عليه يقوم عزل الشحنات بين الفروع.
    branch = models.ForeignKey(
        'core.Branch', on_delete=models.PROTECT, related_name='incoming_shipments', verbose_name='الفرع')
    status = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField('ملاحظات', blank=True)
    has_difference = models.BooleanField('فيها فرق استلام', default=False)
    sent_at = models.DateTimeField('وقت الإرسال', null=True, blank=True)
    received_at = models.DateTimeField('وقت الاستلام', null=True, blank=True)
    received_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='received_shipments', verbose_name='استلمها')

    class Meta:
        db_table = 'stock_shipments'
        verbose_name = 'شحنة مخزن مركزي'
        verbose_name_plural = 'شحنات المخزن المركزي'
        ordering = ['-shipment_date', '-id']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'branch', 'status']),
        ]

    def __str__(self):
        return f"{self.shipment_number} ({self.from_stock} → {self.to_stock})"

    def save(self, *args, **kwargs):
        if not self.shipment_number:
            prefix = 'RET' if self.direction == 'to_central' else 'SHP'
            last = (
                Shipment.objects.filter(tenant=self.tenant, shipment_number__startswith=f'{prefix}-')
                .order_by('-id').first()
            )
            next_num = 1
            if last:
                try:
                    next_num = int(last.shipment_number.split('-')[-1]) + 1
                except ValueError:
                    pass
            self.shipment_number = f"{prefix}-{next_num:05d}"
        super().save(*args, **kwargs)


class ShipmentLine(TenantMixin):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='lines', verbose_name='الشحنة')
    item = models.ForeignKey('items.Item', on_delete=models.PROTECT, related_name='shipment_lines', verbose_name='المنتج')
    quantity_sent = models.DecimalField('الكمية المرسلة', max_digits=12, decimal_places=4)
    quantity_received = models.DecimalField('الكمية المستلَمة', max_digits=12, decimal_places=4, null=True, blank=True)
    # تكلفة الصنف وقت الإرسال (تكلفة الشراء المركزي) — لقطة للتقارير.
    unit_cost = models.DecimalField('تكلفة الوحدة', max_digits=14, decimal_places=4, default=0)

    class Meta:
        db_table = 'stock_shipment_lines'
        verbose_name = 'بند شحنة'
        verbose_name_plural = 'بنود الشحنات'

    def __str__(self):
        return f"{self.item.name} × {self.quantity_sent}"

    @property
    def difference(self):
        if self.quantity_received is None:
            return None
        return self.quantity_sent - self.quantity_received


class ShipmentLineBatch(TenantMixin):
    """الدفعات (FEFO) التي خرجت من المخزن المركزي لبند شحنة، وما استُلم منها."""
    line = models.ForeignKey(ShipmentLine, on_delete=models.CASCADE, related_name='batches', verbose_name='البند')
    source_batch = models.ForeignKey(
        'items.ItemBatch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='الدفعة المصدر')
    batch_number = models.CharField('رقم الدفعة', max_length=100, blank=True)
    expiry_date = models.DateField('تاريخ الانتهاء', null=True, blank=True)
    quantity_sent = models.DecimalField('الكمية المرسلة', max_digits=12, decimal_places=4)
    quantity_received = models.DecimalField('الكمية المستلَمة', max_digits=12, decimal_places=4, null=True, blank=True)

    class Meta:
        db_table = 'stock_shipment_line_batches'
        verbose_name = 'دفعة بند شحنة'
        verbose_name_plural = 'دفعات بنود الشحنات'
        ordering = ['id']


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
# STOCK DESTRUCTION (إتلاف الأصناف منتهية الصلاحية)
# ============================================

class StockDestruction(TenantMixin):
    """
    سجل إتلاف موثّق لأصناف منتهية الصلاحية أو تالفة — متطلب رقابي شائع للصيدليات.
    - draft:     جاري تجهيز السجل
    - confirmed: تم تطبيق الإتلاف على المخزون (لا يمكن التراجع)
    - cancelled: أُلغي السجل بدون أي تأثير على المخزون
    """
    STATUS_CHOICES = (
        ('draft',     'مسودة'),
        ('confirmed', 'مؤكد'),
        ('cancelled', 'ملغي'),
    )
    REASON_CHOICES = (
        ('expired', 'منتهي الصلاحية'),
        ('damaged', 'تالف'),
        ('recalled', 'مسحوب من الشركة المصنعة'),
        ('other', 'أخرى'),
    )

    destruction_number = models.CharField('رقم السجل', max_length=30, blank=True)
    destruction_date    = models.DateField('تاريخ الإتلاف')
    stock                = models.ForeignKey(
        Stock, on_delete=models.PROTECT,
        related_name='destructions', verbose_name='المخزن'
    )
    status = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='draft')
    reason = models.CharField('السبب', max_length=20, choices=REASON_CHOICES, default='expired')
    witness_name = models.CharField(
        'اسم الشاهد / المسؤول', max_length=200, blank=True,
        help_text='اسم الشخص الذي شهد عملية الإتلاف (متطلب رقابي شائع)'
    )
    reference_number = models.CharField('رقم مرجعي / محضر', max_length=100, blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    confirmed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='confirmed_destructions', verbose_name='أُكِّد بواسطة'
    )
    confirmed_at = models.DateTimeField('تاريخ التأكيد', null=True, blank=True)

    class Meta:
        db_table = 'stock_destructions'
        verbose_name = 'سجل إتلاف'
        verbose_name_plural = 'سجلات الإتلاف'
        ordering = ['-destruction_date', '-id']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', '-destruction_date']),
        ]

    def __str__(self):
        return f"{self.destruction_number} — {self.stock.name}"

    def save(self, *args, **kwargs):
        if not self.destruction_number:
            last = (
                StockDestruction.objects.filter(tenant=self.tenant)
                .exclude(destruction_number='')
                .order_by('-id').first()
            )
            next_num = 1
            if last and last.destruction_number.startswith('DES-'):
                try:
                    next_num = int(last.destruction_number.split('-')[-1]) + 1
                except ValueError:
                    pass
            self.destruction_number = f"DES-{next_num:05d}"
        super().save(*args, **kwargs)

    @property
    def total_quantity(self):
        return sum((l.quantity for l in self.lines.all()), Decimal('0'))

    @property
    def total_value(self):
        return sum((l.quantity * l.unit_cost_snapshot for l in self.lines.all()), Decimal('0'))


class StockDestructionLine(TenantMixin):
    destruction = models.ForeignKey(
        StockDestruction, on_delete=models.CASCADE,
        related_name='lines', verbose_name='سجل الإتلاف'
    )
    item = models.ForeignKey(
        'items.Item', on_delete=models.PROTECT,
        related_name='destruction_lines', verbose_name='المنتج'
    )
    batch = models.ForeignKey(
        'items.ItemBatch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='destruction_lines', verbose_name='الدفعة'
    )
    batch_number_snapshot = models.CharField('رقم الدفعة', max_length=100, blank=True)
    expiry_date_snapshot = models.DateField('تاريخ انتهاء الصلاحية', null=True, blank=True)
    quantity = models.DecimalField('الكمية', max_digits=12, decimal_places=4)
    unit_cost_snapshot = models.DecimalField('تكلفة الوحدة', max_digits=14, decimal_places=2, default=0)
    notes = models.CharField('ملاحظات', max_length=300, blank=True)

    class Meta:
        db_table = 'stock_destruction_lines'
        verbose_name = 'بند إتلاف'
        verbose_name_plural = 'بنود الإتلاف'

    def __str__(self):
        return f"{self.item.name} × {self.quantity}"

    @property
    def line_value(self):
        return self.quantity * self.unit_cost_snapshot


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
