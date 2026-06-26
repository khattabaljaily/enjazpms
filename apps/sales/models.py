"""
وحدة المبيعات — النماذج
========================

الهيكل:
  SaleInvoice        رأس الفاتورة (الهيدر)
  SaleInvoiceLine    بنود/سطور الفاتورة
  SalePayment        تسجيل الدفعات (نقدي / بنكي)
  SaleReturn         رأس مرتجع المبيعات
  SaleReturnLine     بنود المرتجع
  StockMovement      سجل كل حركات المخزون
  CustomerLedger     سجل حساب العميل (المطالبات والمدفوعات)

مبادئ:
  - كل تعديل على الأرصدة يتم حصريًا عبر services.py بـ @transaction.atomic
  - لا يُعدَّل StockQuantity.quantity مباشرة؛ يُستخدم دائمًا StockMovement
  - لا يُعدَّل رصيد العميل مباشرة؛ يُستخدم دائمًا CustomerLedger
"""

from decimal import Decimal
from django.db import models
from django.db.models import Sum

from apps.core.models import TenantMixin


# ============================================================
# SALE INVOICE  (فاتورة المبيعات)
# ============================================================

class SaleInvoice(TenantMixin):
    """
    رأس فاتورة البيع.

    دورة الحياة:
      draft → confirmed → (cancelled | returned | partially_returned)

    payment_method:
      cash    → يُسجَّل في SalePayment نوع cash
      bank    → يُسجَّل في SalePayment نوع bank
      credit  → يُسجَّل في CustomerLedger نوع invoice (مطالبة)
      mixed   → جزء SalePayment + جزء CustomerLedger
    """

    STATUS_CHOICES = (
        ('draft',               'مسودة'),
        ('pending_delivery',    'قيد التسليم'),
        ('confirmed',           'مؤكدة'),
        ('cancelled',           'ملغاة'),
        ('returned',            'مرتجعة كلياً'),
        ('partially_returned',  'مرتجعة جزئياً'),
    )

    DELIVERY_TYPE_CHOICES = (
        ('immediate', 'تسليم فوري'),
        ('deferred',  'تسليم لاحق'),
    )

    PAYMENT_CHOICES = (
        ('cash',   'نقداً'),
        ('bank',   'تحويل بنكي'),
        ('credit', 'آجل (على حساب العميل)'),
        ('mixed',  'مختلط'),
    )

    DISCOUNT_TYPE_CHOICES = (
        ('percent', 'نسبة مئوية'),
        ('amount',  'مبلغ ثابت'),
    )

    # ── المعرّفات ──────────────────────────────────────────
    invoice_number = models.CharField(
        'رقم الفاتورة', max_length=20, blank=True,
        help_text='يُولَّد تلقائياً بصيغة SAL-000001'
    )
    reference_number = models.CharField(
        'رقم مرجعي خارجي', max_length=50, blank=True
    )

    # ── الأطراف ───────────────────────────────────────────
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='sale_invoices',
        verbose_name='العميل',
        help_text='اتركه فارغاً للبيع الفوري (زبون عابر)'
    )
    stock = models.ForeignKey(
        'stocks.Stock',
        on_delete=models.PROTECT,
        related_name='sale_invoices',
        verbose_name='المخزن'
    )
    agent = models.ForeignKey(
        'agents.Agent',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='sale_invoices',
        verbose_name='المندوب',
    )

    # ── التواريخ ───────────────────────────────────────────
    invoice_date = models.DateField('تاريخ الفاتورة')
    due_date = models.DateField(
        'تاريخ الاستحقاق', null=True, blank=True,
        help_text='خاص بالمبيعات الآجلة'
    )

    # ── الحالة والدفع ─────────────────────────────────────
    status = models.CharField(
        'الحالة', max_length=25,
        choices=STATUS_CHOICES, default='draft'
    )
    delivery_type = models.CharField(
        'نوع التسليم', max_length=10,
        choices=DELIVERY_TYPE_CHOICES, default='immediate'
    )
    payment_method = models.CharField(
        'طريقة الدفع', max_length=10,
        choices=PAYMENT_CHOICES, default='cash'
    )

    # ── المبالغ ───────────────────────────────────────────
    subtotal = models.DecimalField(
        'المجموع قبل الخصم', max_digits=14, decimal_places=2, default=0,
        help_text='مجموع أسطر الفاتورة قبل خصم الفاتورة'
    )
    invoice_discount_type = models.CharField(
        'نوع خصم الفاتورة', max_length=10,
        choices=DISCOUNT_TYPE_CHOICES, default='percent'
    )
    invoice_discount_value = models.DecimalField(
        'قيمة خصم الفاتورة', max_digits=10, decimal_places=2, default=0
    )
    invoice_discount_amount = models.DecimalField(
        'مبلغ خصم الفاتورة', max_digits=14, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        'إجمالي الضريبة', max_digits=14, decimal_places=2, default=0
    )
    grand_total = models.DecimalField(
        'الإجمالي النهائي', max_digits=14, decimal_places=2, default=0
    )
    paid_amount = models.DecimalField(
        'المبلغ المدفوع', max_digits=14, decimal_places=2, default=0
    )

    # ── اختلاط الدفع (mixed) ─────────────────────────────
    cash_amount = models.DecimalField(
        'المبلغ نقداً (مختلط)', max_digits=14, decimal_places=2, default=0
    )
    bank_amount = models.DecimalField(
        'المبلغ بنكياً (مختلط)', max_digits=14, decimal_places=2, default=0
    )
    bank_reference = models.CharField(
        'مرجع التحويل البنكي', max_length=100, blank=True
    )

    notes = models.TextField('ملاحظات', blank=True)

    # ── مستخدمو العمليات ──────────────────────────────────
    confirmed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='confirmed_invoices',
        verbose_name='أُكِّدت بواسطة'
    )
    cancelled_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cancelled_invoices',
        verbose_name='أُلغيت بواسطة'
    )
    cancelled_at = models.DateTimeField('وقت الإلغاء', null=True, blank=True)
    cancellation_reason = models.TextField('سبب الإلغاء', blank=True)
    delivered_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='delivered_invoices',
        verbose_name='سُلِّمت بواسطة'
    )
    delivered_at = models.DateTimeField('وقت التسليم', null=True, blank=True)

    class Meta:
        db_table = 'sale_invoices'
        verbose_name = 'فاتورة مبيعات'
        verbose_name_plural = 'فواتير المبيعات'
        ordering = ['-invoice_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'customer']),
            models.Index(fields=['tenant', 'invoice_date']),
            models.Index(fields=['tenant', 'invoice_number']),
        ]

    def __str__(self):
        return f"{self.invoice_number} — {self.customer or 'زبون عابر'}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = self._generate_number()
        super().save(*args, **kwargs)

    def _generate_number(self):
        last = (
            SaleInvoice.objects
            .filter(tenant=self.tenant)
            .exclude(invoice_number='')
            .order_by('-id')
            .first()
        )
        if last and last.invoice_number.startswith('SAL-'):
            try:
                seq = int(last.invoice_number.split('-')[1]) + 1
            except (IndexError, ValueError):
                seq = 1
        else:
            seq = 1
        return f"SAL-{seq:06d}"

    # ── computed helpers ───────────────────────────────────
    @property
    def remaining_amount(self):
        return self.grand_total - self.paid_amount

    @property
    def is_fully_paid(self):
        return self.paid_amount >= self.grand_total

    @property
    def returned_amount(self):
        confirmed_returns = self.sale_returns.filter(status='confirmed')
        total = confirmed_returns.aggregate(s=Sum('total_returned'))['s'] or Decimal('0')
        return total

    def recalculate_totals(self):
        """
        يُعيد حساب subtotal / invoice_discount_amount / tax_amount / grand_total
        من بنود الفاتورة. يُستخدَم قبل الحفظ في services.py.
        """
        lines = self.lines.all()
        subtotal = sum(l.line_subtotal for l in lines)
        tax = sum(l.tax_amount for l in lines)

        if self.invoice_discount_type == 'percent':
            disc = subtotal * (self.invoice_discount_value / Decimal('100'))
        else:
            disc = self.invoice_discount_value

        disc = min(disc, subtotal)
        self.subtotal = subtotal
        self.invoice_discount_amount = disc
        self.tax_amount = tax
        self.grand_total = subtotal - disc + tax

    def sync_paid_amount(self):
        """يُحدِّث paid_amount من مجموع SalePayments المرتبطة."""
        total = self.payments.filter(is_reversed=False).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        self.paid_amount = total


# ============================================================
# SALE INVOICE LINE  (بنود الفاتورة)
# ============================================================

class SaleInvoiceLine(TenantMixin):
    """
    سطر واحد في فاتورة البيع.

    line_subtotal  = quantity × unit_price − discount_amount
    tax_amount     = line_subtotal × (tax_rate / 100)
    line_total     = line_subtotal + tax_amount
    """

    invoice = models.ForeignKey(
        SaleInvoice,
        on_delete=models.CASCADE,
        related_name='lines',
        verbose_name='الفاتورة'
    )
    item = models.ForeignKey(
        'items.Item',
        on_delete=models.PROTECT,
        related_name='sale_lines',
        verbose_name='المنتج'
    )

    quantity = models.DecimalField(
        'الكمية', max_digits=12, decimal_places=4
    )
    unit_price = models.DecimalField(
        'سعر الوحدة', max_digits=14, decimal_places=2
    )
    discount_percent = models.DecimalField(
        'خصم السطر %', max_digits=5, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        'مبلغ خصم السطر', max_digits=14, decimal_places=2, default=0
    )
    tax_rate = models.DecimalField(
        'نسبة الضريبة %', max_digits=5, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        'مبلغ الضريبة', max_digits=14, decimal_places=2, default=0
    )
    line_subtotal = models.DecimalField(
        'مجموع السطر قبل الضريبة', max_digits=14, decimal_places=2, default=0
    )
    line_total = models.DecimalField(
        'مجموع السطر (شامل الضريبة)', max_digits=14, decimal_places=2, default=0
    )

    # تتبع المرتجعات
    returned_quantity = models.DecimalField(
        'الكمية المُرتجَعة', max_digits=12, decimal_places=4, default=0
    )

    # لقطة التكلفة وقت البيع (لحساب الربح)
    cost_price_snapshot = models.DecimalField(
        'سعر التكلفة وقت البيع', max_digits=14, decimal_places=2, default=0
    )

    # وحدة البيع ومعامل التحويل (لدعم وحدات متعددة)
    unit = models.ForeignKey(
        'items.Unit', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sale_lines', verbose_name='الوحدة'
    )
    unit_factor = models.DecimalField(
        'معامل التحويل', max_digits=10, decimal_places=4, default=1,
        help_text='1 وحدة مستخدمة = unit_factor وحدة أساسية'
    )

    # بيانات تتبع الدُفعة / المسلسل / الصلاحية (اختياري)
    batch_number = models.CharField('رقم الدُفعة', max_length=100, blank=True)
    serial_number = models.CharField('الرقم التسلسلي', max_length=100, blank=True)
    expiry_date = models.DateField('تاريخ الصلاحية', null=True, blank=True)

    class Meta:
        db_table = 'sale_invoice_lines'
        verbose_name = 'بند فاتورة'
        verbose_name_plural = 'بنود الفواتير'
        ordering = ['id']

    def __str__(self):
        return f"{self.invoice.invoice_number} – {self.item.name} × {self.quantity}"

    def calculate(self):
        """يحسب مبالغ السطر ويحفظها (لا يستدعي save)."""
        qty = self.quantity or Decimal('0')
        price = self.unit_price or Decimal('0')
        gross = qty * price
        disc_pct = self.discount_percent or Decimal('0')
        disc_amt = gross * (disc_pct / Decimal('100'))
        self.discount_amount = disc_amt.quantize(Decimal('0.01'))
        sub = gross - self.discount_amount
        self.line_subtotal = sub.quantize(Decimal('0.01'))
        tax = sub * (self.tax_rate or Decimal('0')) / Decimal('100')
        self.tax_amount = tax.quantize(Decimal('0.01'))
        self.line_total = (sub + self.tax_amount).quantize(Decimal('0.01'))

    @property
    def returnable_quantity(self):
        return self.quantity - self.returned_quantity


# ============================================================
# SALE PAYMENT  (دفعات السداد)
# ============================================================

class SalePayment(TenantMixin):
    """
    سجل دفعة مالية مرتبطة بفاتورة مبيعات.
    نوع الدفع: cash أو bank فقط.
    المبيعات الآجلة تُسجَّل في CustomerLedger وليس هنا.
    """

    PAYMENT_CHOICES = (
        ('cash', 'نقداً'),
        ('bank', 'تحويل بنكي'),
    )

    invoice = models.ForeignKey(
        SaleInvoice,
        on_delete=models.CASCADE,
        related_name='payments',
        verbose_name='الفاتورة'
    )
    payment_method = models.CharField(
        'طريقة الدفع', max_length=10, choices=PAYMENT_CHOICES
    )
    amount = models.DecimalField(
        'المبلغ', max_digits=14, decimal_places=2
    )
    payment_date = models.DateField('تاريخ الدفع')
    reference_number = models.CharField(
        'رقم مرجعي (شيك / تحويل)', max_length=100, blank=True
    )
    notes = models.TextField('ملاحظات', blank=True)
    is_reversed = models.BooleanField('تم عكسه', default=False)

    class Meta:
        db_table = 'sale_payments'
        verbose_name = 'دفعة مبيعات'
        verbose_name_plural = 'دفعات المبيعات'

    def __str__(self):
        return f"{self.invoice.invoice_number} — {self.amount} {self.get_payment_method_display()}"


# ============================================================
# SALE RETURN  (مرتجع المبيعات)
# ============================================================

class SaleReturn(TenantMixin):
    """
    رأس مرتجع مبيعات.
    يرتبط دائماً بفاتورة مبيعات أصلية مؤكدة.

    refund_method:
      cash    → يُعاد المبلغ نقداً (SalePayment بقيمة سالبة)
      bank    → يُعاد بنكياً
      balance → يُضاف كرصيد دائن في CustomerLedger (مديونية للعميل على الشركة)
    """

    STATUS_CHOICES = (
        ('draft',     'مسودة'),
        ('confirmed', 'مؤكد'),
        ('cancelled', 'ملغي'),
    )

    REFUND_METHOD_CHOICES = (
        ('cash',    'استرداد نقدي'),
        ('bank',    'تحويل بنكي'),
        ('balance', 'رصيد دائن للعميل'),
    )

    return_number = models.CharField(
        'رقم المرتجع', max_length=25, blank=True
    )
    return_date = models.DateField('تاريخ المرتجع')
    original_invoice = models.ForeignKey(
        SaleInvoice,
        on_delete=models.PROTECT,
        related_name='sale_returns',
        verbose_name='الفاتورة الأصلية'
    )
    status = models.CharField(
        'الحالة', max_length=15, choices=STATUS_CHOICES, default='draft'
    )
    refund_method = models.CharField(
        'طريقة الاسترداد', max_length=10,
        choices=REFUND_METHOD_CHOICES, default='cash'
    )
    total_returned = models.DecimalField(
        'إجمالي المرتجع', max_digits=14, decimal_places=2, default=0
    )
    reason = models.TextField('سبب الإرجاع', blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    confirmed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='confirmed_returns',
        verbose_name='أُكِّد بواسطة'
    )

    class Meta:
        db_table = 'sale_returns'
        verbose_name = 'مرتجع مبيعات'
        verbose_name_plural = 'مرتجعات المبيعات'
        ordering = ['-return_date', '-created_at']

    def __str__(self):
        return f"{self.return_number} ← {self.original_invoice.invoice_number}"

    def save(self, *args, **kwargs):
        if not self.return_number:
            self.return_number = self._generate_number()
        super().save(*args, **kwargs)

    def _generate_number(self):
        last = (
            SaleReturn.objects
            .filter(tenant=self.tenant)
            .exclude(return_number='')
            .order_by('-id')
            .first()
        )
        if last and last.return_number.startswith('RET-'):
            try:
                seq = int(last.return_number.split('-')[1]) + 1
            except (IndexError, ValueError):
                seq = 1
        else:
            seq = 1
        return f"RET-{seq:06d}"


# ============================================================
# SALE RETURN LINE  (بنود المرتجع)
# ============================================================

class SaleReturnLine(TenantMixin):
    """
    سطر واحد في مرتجع المبيعات.
    يرتبط بسطر الفاتورة الأصلية للتحقق من الكميات.
    """

    sale_return = models.ForeignKey(
        SaleReturn,
        on_delete=models.CASCADE,
        related_name='lines',
        verbose_name='المرتجع'
    )
    invoice_line = models.ForeignKey(
        SaleInvoiceLine,
        on_delete=models.PROTECT,
        related_name='return_lines',
        verbose_name='سطر الفاتورة الأصلية'
    )
    item = models.ForeignKey(
        'items.Item',
        on_delete=models.PROTECT,
        related_name='return_lines',
        verbose_name='المنتج'
    )
    returned_quantity = models.DecimalField(
        'الكمية المُرتجَعة', max_digits=12, decimal_places=4
    )
    unit_price = models.DecimalField(
        'سعر الوحدة', max_digits=14, decimal_places=2
    )
    line_total = models.DecimalField(
        'إجمالي السطر', max_digits=14, decimal_places=2, default=0
    )

    class Meta:
        db_table = 'sale_return_lines'
        verbose_name = 'بند مرتجع'
        verbose_name_plural = 'بنود المرتجعات'

    def __str__(self):
        return f"{self.sale_return.return_number} – {self.item.name} × {self.returned_quantity}"


# ============================================================
# STOCK MOVEMENT  (حركات المخزون)
# ============================================================

class StockMovement(TenantMixin):
    """
    سجل لكل حركة دخول/خروج في المخزون.

    هذا الجدول هو المصدر الحقيقي لتاريخ حركات المخزون.
    يُنشأ تلقائياً من services.py عند:
      - تأكيد فاتورة بيع   → sale_out
      - إلغاء فاتورة بيع   → عكس (حذف الحركة أو إنشاء sale_in عكسي)
      - تأكيد مرتجع بيع    → sale_return_in
      - تأكيد فاتورة شراء  → purchase_in  (مستقبلاً)
      - تحويل بين مخازن    → transfer_in / transfer_out (مستقبلاً)
      - جرد/تسوية          → adjustment_in / adjustment_out
      - رصيد افتتاحي       → opening_in
    """

    MOVEMENT_TYPE_CHOICES = (
        ('sale_out',           'بيع — خروج'),
        ('sale_return_in',     'مرتجع بيع — دخول'),
        ('purchase_in',        'شراء — دخول'),
        ('purchase_return_out','مرتجع شراء — خروج'),
        ('transfer_in',        'تحويل — دخول'),
        ('transfer_out',       'تحويل — خروج'),
        ('adjustment_in',      'تسوية — دخول'),
        ('adjustment_out',     'تسوية — خروج'),
        ('opening_in',         'رصيد افتتاحي'),
        ('opening_correction', 'تصحيح رصيد افتتاحي'),
    )

    DIRECTION_CHOICES = (
        ('in',  'دخول +'),
        ('out', 'خروج −'),
    )

    item = models.ForeignKey(
        'items.Item',
        on_delete=models.PROTECT,
        related_name='stock_movements',
        verbose_name='المنتج'
    )
    stock = models.ForeignKey(
        'stocks.Stock',
        on_delete=models.PROTECT,
        related_name='movements',
        verbose_name='المخزن'
    )

    movement_type = models.CharField(
        'نوع الحركة', max_length=25, choices=MOVEMENT_TYPE_CHOICES
    )
    direction = models.CharField(
        'الاتجاه', max_length=5, choices=DIRECTION_CHOICES
    )
    quantity = models.DecimalField(
        'الكمية', max_digits=12, decimal_places=4,
        help_text='دائماً موجبة، الاتجاه يحدد الدخول أو الخروج'
    )
    unit_cost = models.DecimalField(
        'تكلفة الوحدة', max_digits=14, decimal_places=2, default=0
    )
    movement_date = models.DateField('تاريخ الحركة')
    notes = models.TextField('ملاحظات', blank=True)

    # ربط بمصدر الحركة (Generic FK بسيطة)
    reference_type = models.CharField(
        'نوع المرجع', max_length=50, blank=True,
        help_text='sale_invoice | sale_return | purchase_invoice | ...'
    )
    reference_id = models.PositiveBigIntegerField(
        'رقم المرجع', null=True, blank=True
    )

    # ما الرصيد الكلي بعد هذه الحركة (snapshot للتقارير)
    balance_after = models.DecimalField(
        'الرصيد بعد الحركة', max_digits=14, decimal_places=4, default=0
    )
    is_reversal = models.BooleanField('قيد عكسي', default=False)

    class Meta:
        db_table = 'stock_movements'
        verbose_name = 'حركة مخزون'
        verbose_name_plural = 'حركات المخزون'
        ordering = ['-movement_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'item', '-movement_date']),
            models.Index(fields=['tenant', 'stock', '-movement_date']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        sign = '+' if self.direction == 'in' else '−'
        return f"{self.item.name} @ {self.stock.name} {sign}{self.quantity}"


# ============================================================
# CUSTOMER LEDGER  (سجل حساب العميل)
# ============================================================

class CustomerLedger(TenantMixin):
    """
    سجل حساب العميل المالي.

    القاعدة:
      amount موجب  → مطالبة على العميل (الشركة لها حق عند العميل)
      amount سالب  → دين من الشركة للعميل (مثل استرداد / رصيد دائن)

    أنواع القيود:
      opening     رصيد افتتاحي
      invoice     فاتورة بيع آجل
      payment     دفعة من العميل
      return      مرتجع أو استرداد
      adjustment  تعديل يدوي
    """

    ENTRY_TYPE_CHOICES = (
        ('opening',    'رصيد افتتاحي'),
        ('invoice',    'فاتورة بيع آجل'),
        ('payment',    'دفعة من العميل'),
        ('return',     'مرتجع أو استرداد'),
        ('adjustment', 'تعديل يدوي'),
    )

    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        related_name='ledger_entries',
        verbose_name='العميل'
    )
    entry_type = models.CharField(
        'نوع القيد', max_length=15, choices=ENTRY_TYPE_CHOICES
    )
    amount = models.DecimalField(
        'المبلغ', max_digits=14, decimal_places=2,
        help_text='موجب = مديونية على العميل | سالب = رصيد دائن'
    )
    entry_date = models.DateField('تاريخ القيد')
    notes = models.TextField('ملاحظات', blank=True)

    # ربط بمصدر القيد
    reference_type = models.CharField(
        'نوع المرجع', max_length=50, blank=True
    )
    reference_id = models.PositiveBigIntegerField(
        'رقم المرجع', null=True, blank=True
    )

    # رصيد تراكمي snapshot لتسهيل عرض كشف الحساب بدون جمع كل مرة
    running_balance = models.DecimalField(
        'الرصيد التراكمي', max_digits=14, decimal_places=2, default=0
    )
    is_reversal = models.BooleanField('قيد عكسي', default=False)

    # حقول العملة الصعبة — تُملأ فقط عندما HC mode مفعّل للـ tenant
    hc_amount = models.DecimalField(
        'المبلغ بالعملة الصعبة', max_digits=14, decimal_places=2,
        null=True, blank=True
    )
    hc_currency = models.CharField(
        'رمز العملة الصعبة', max_length=5, blank=True, default=''
    )
    hc_exchange_rate = models.DecimalField(
        'سعر الصرف المستخدم', max_digits=12, decimal_places=4,
        null=True, blank=True
    )
    hc_running_balance = models.DecimalField(
        'الرصيد التراكمي بالعملة الصعبة', max_digits=14, decimal_places=2,
        null=True, blank=True
    )

    class Meta:
        db_table = 'customer_ledger'
        verbose_name = 'قيد حساب عميل'
        verbose_name_plural = 'سجل حسابات العملاء'
        ordering = ['-entry_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'customer', '-entry_date']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        sign = '+' if self.amount >= 0 else ''
        return f"{self.customer.name} — {sign}{self.amount} ({self.get_entry_type_display()})"


# ============================================================
# SALE QUOTE  (عرض السعر)
# ============================================================

class SaleQuote(TenantMixin):
    """
    عرض سعر (Quotation).

    دورة الحياة:
      draft → sent → accepted → converted (تحوّل لفاتورة)
                  ↘ rejected
                  ↘ expired
             ↘ cancelled

    بعد التحويل يُحفَظ المعرّف invoice_id للربط.
    """

    STATUS_CHOICES = (
        ('draft',     'مسودة'),
        ('sent',      'مُرسَل'),
        ('accepted',  'مقبول'),
        ('rejected',  'مرفوض'),
        ('converted', 'مُحوَّل لفاتورة'),
        ('expired',   'منتهي الصلاحية'),
        ('cancelled', 'ملغى'),
    )

    DISCOUNT_TYPE_CHOICES = (
        ('percent', 'نسبة مئوية'),
        ('amount',  'مبلغ ثابت'),
    )

    # ── المعرّفات ──────────────────────────────────────────
    quote_number = models.CharField(
        'رقم العرض', max_length=20, blank=True,
        help_text='يُولَّد تلقائياً بصيغة QUO-000001'
    )
    reference_number = models.CharField(
        'رقم مرجعي خارجي', max_length=50, blank=True
    )

    # ── الأطراف ───────────────────────────────────────────
    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='sale_quotes',
        verbose_name='العميل'
    )
    stock = models.ForeignKey(
        'stocks.Stock',
        on_delete=models.PROTECT,
        related_name='sale_quotes',
        verbose_name='المخزن'
    )

    # ── التواريخ ───────────────────────────────────────────
    quote_date = models.DateField('تاريخ العرض')
    expiry_date = models.DateField(
        'تاريخ الانتهاء', null=True, blank=True
    )

    # ── الحالة ────────────────────────────────────────────
    status = models.CharField(
        'الحالة', max_length=15,
        choices=STATUS_CHOICES, default='draft'
    )

    # ── المبالغ ───────────────────────────────────────────
    subtotal = models.DecimalField(
        'المجموع قبل الخصم', max_digits=14, decimal_places=2, default=0
    )
    quote_discount_type = models.CharField(
        'نوع الخصم الإجمالي', max_length=10,
        choices=DISCOUNT_TYPE_CHOICES, default='percent'
    )
    quote_discount_value = models.DecimalField(
        'قيمة الخصم الإجمالي', max_digits=10, decimal_places=2, default=0
    )
    quote_discount_amount = models.DecimalField(
        'مبلغ الخصم الإجمالي', max_digits=14, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        'إجمالي الضريبة', max_digits=14, decimal_places=2, default=0
    )
    grand_total = models.DecimalField(
        'الإجمالي النهائي', max_digits=14, decimal_places=2, default=0
    )

    notes = models.TextField('ملاحظات', blank=True)
    terms = models.TextField('الشروط والأحكام', blank=True)

    # ── الربط بالفاتورة بعد التحويل ───────────────────────
    converted_invoice = models.OneToOneField(
        SaleInvoice,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='source_quote',
        verbose_name='الفاتورة المُحوَّلة'
    )
    converted_at = models.DateTimeField('وقت التحويل', null=True, blank=True)
    converted_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL, null=True, blank=True,
        related_name='converted_quotes',
        verbose_name='حُوِّل بواسطة'
    )

    class Meta:
        db_table = 'sale_quotes'
        verbose_name = 'عرض سعر'
        verbose_name_plural = 'عروض الأسعار'
        ordering = ['-quote_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'customer']),
            models.Index(fields=['tenant', 'quote_date']),
            models.Index(fields=['tenant', 'quote_number']),
        ]

    def __str__(self):
        return f"{self.quote_number} — {self.customer or 'زبون عابر'}"

    def save(self, *args, **kwargs):
        if not self.quote_number:
            self.quote_number = self._generate_number()
        super().save(*args, **kwargs)

    def _generate_number(self):
        last = (
            SaleQuote.objects
            .filter(tenant=self.tenant)
            .exclude(quote_number='')
            .order_by('-id')
            .first()
        )
        if last and last.quote_number.startswith('QUO-'):
            try:
                seq = int(last.quote_number.split('-')[1]) + 1
            except (IndexError, ValueError):
                seq = 1
        else:
            seq = 1
        return f"QUO-{seq:06d}"

    def recalculate_totals(self):
        lines = self.quote_lines.all()
        subtotal = sum(l.line_subtotal for l in lines)
        tax = sum(l.tax_amount for l in lines)

        if self.quote_discount_type == 'percent':
            disc = subtotal * (self.quote_discount_value / Decimal('100'))
        else:
            disc = self.quote_discount_value

        disc = min(disc, subtotal)
        self.subtotal = subtotal
        self.quote_discount_amount = disc
        self.tax_amount = tax
        self.grand_total = subtotal - disc + tax

    @property
    def is_editable(self):
        return self.status in ('draft',)

    @property
    def can_send(self):
        return self.status == 'draft'

    @property
    def can_convert(self):
        return self.status in ('draft', 'sent', 'accepted')

    @property
    def can_cancel(self):
        return self.status not in ('converted', 'cancelled')


# ============================================================
# SALE QUOTE LINE  (بنود عرض السعر)
# ============================================================

class SaleQuoteLine(TenantMixin):
    """
    سطر واحد في عرض السعر.
    نفس منطق SaleInvoiceLine لكن بدون تأثير على مخزون.
    """

    quote = models.ForeignKey(
        SaleQuote,
        on_delete=models.CASCADE,
        related_name='quote_lines',
        verbose_name='عرض السعر'
    )
    item = models.ForeignKey(
        'items.Item',
        on_delete=models.PROTECT,
        related_name='quote_lines',
        verbose_name='المنتج'
    )

    quantity = models.DecimalField(
        'الكمية', max_digits=12, decimal_places=4
    )
    unit_price = models.DecimalField(
        'سعر الوحدة', max_digits=14, decimal_places=2
    )
    discount_percent = models.DecimalField(
        'خصم السطر %', max_digits=5, decimal_places=2, default=0
    )
    discount_amount = models.DecimalField(
        'مبلغ خصم السطر', max_digits=14, decimal_places=2, default=0
    )
    tax_rate = models.DecimalField(
        'نسبة الضريبة %', max_digits=5, decimal_places=2, default=0
    )
    tax_amount = models.DecimalField(
        'مبلغ الضريبة', max_digits=14, decimal_places=2, default=0
    )
    line_subtotal = models.DecimalField(
        'مجموع السطر قبل الضريبة', max_digits=14, decimal_places=2, default=0
    )
    line_total = models.DecimalField(
        'مجموع السطر (شامل الضريبة)', max_digits=14, decimal_places=2, default=0
    )

    class Meta:
        db_table = 'sale_quote_lines'
        verbose_name = 'بند عرض سعر'
        verbose_name_plural = 'بنود عروض الأسعار'
        ordering = ['id']

    def __str__(self):
        return f"{self.quote.quote_number} – {self.item.name} × {self.quantity}"
