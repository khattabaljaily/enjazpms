from decimal import Decimal

from django.db import models

from apps.core.models import TenantMixin


class PurchaseInvoice(TenantMixin):
    STATUS_CHOICES = (
        ('draft', 'مسودة'),
        ('confirmed', 'مؤكدة'),
        ('partially_returned', 'مرتجعة جزئياً'),
        ('returned', 'مرتجعة كلياً'),
        ('cancelled', 'ملغاة'),
    )

    PAYMENT_CHOICES = (
        ('cash', 'نقداً'),
        ('bank', 'تحويل بنكي'),
        ('credit', 'آجل'),
        ('mixed', 'مختلط'),
    )

    invoice_number = models.CharField('رقم أمر الشراء', max_length=20, blank=True)
    supplier = models.ForeignKey(
        'suppliers.Supplier',
        on_delete=models.PROTECT,
        related_name='purchase_invoices',
        verbose_name='المورد',
        null=True,
        blank=True,
    )
    stock = models.ForeignKey(
        'stocks.Stock',
        on_delete=models.PROTECT,
        related_name='purchase_invoices',
        verbose_name='المخزن',
    )

    invoice_date = models.DateField('تاريخ الأمر')
    status = models.CharField('الحالة', max_length=20, choices=STATUS_CHOICES, default='draft')
    payment_method = models.CharField('طريقة الدفع', max_length=10, choices=PAYMENT_CHOICES, default='cash')
    cash_amount = models.DecimalField('المبلغ نقداً (مختلط)', max_digits=14, decimal_places=2, default=0)
    bank_amount = models.DecimalField('المبلغ بنكياً (مختلط)', max_digits=14, decimal_places=2, default=0)
    bank_reference = models.CharField('مرجع التحويل البنكي', max_length=100, blank=True)

    subtotal = models.DecimalField('المجموع الفرعي', max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField('الضريبة', max_digits=14, decimal_places=2, default=0)
    grand_total = models.DecimalField('الإجمالي', max_digits=14, decimal_places=2, default=0)
    notes = models.TextField('ملاحظات', blank=True)
    confirmed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_purchase_invoices',
        verbose_name='أُكد بواسطة',
    )
    cancelled_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cancelled_purchase_invoices',
        verbose_name='أُلغي بواسطة',
    )
    cancelled_at = models.DateTimeField('وقت الإلغاء', null=True, blank=True)
    cancellation_reason = models.TextField('سبب الإلغاء', blank=True)

    class Meta:
        db_table = 'purchase_invoices'
        verbose_name = 'أمر شراء'
        verbose_name_plural = 'أوامر الشراء'
        ordering = ['-invoice_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'invoice_number']),
            models.Index(fields=['tenant', 'invoice_date']),
        ]

    def __str__(self):
        return self.invoice_number or f'PUR-{self.pk}'

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = self._generate_number()
        super().save(*args, **kwargs)

    def _generate_number(self):
        last = (
            PurchaseInvoice.objects.filter(tenant=self.tenant)
            .exclude(invoice_number='')
            .order_by('-id')
            .first()
        )
        seq = 1
        if last and last.invoice_number.startswith('PUR-'):
            try:
                seq = int(last.invoice_number.split('-')[1]) + 1
            except (IndexError, ValueError):
                seq = 1
        return f'PUR-{seq:06d}'

    def recalculate_totals(self):
        lines = self.lines.all()
        subtotal = sum((line.line_subtotal for line in lines), Decimal('0'))
        tax = sum((line.tax_amount for line in lines), Decimal('0'))
        self.subtotal = subtotal.quantize(Decimal('0.01'))
        self.tax_amount = tax.quantize(Decimal('0.01'))
        self.grand_total = (subtotal + tax).quantize(Decimal('0.01'))


class PurchaseInvoiceLine(TenantMixin):
    invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name='lines',
        verbose_name='أمر الشراء',
    )
    item = models.ForeignKey(
        'items.Item',
        on_delete=models.PROTECT,
        related_name='purchase_lines',
        verbose_name='المنتج',
    )

    quantity = models.DecimalField('الكمية', max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField('سعر الوحدة', max_digits=14, decimal_places=2)
    unit = models.ForeignKey(
        'items.Unit', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='purchase_lines', verbose_name='الوحدة'
    )
    unit_factor = models.DecimalField(
        'معامل التحويل', max_digits=10, decimal_places=4, default=1,
    )
    tax_rate = models.DecimalField('نسبة الضريبة', max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField('مبلغ الضريبة', max_digits=14, decimal_places=2, default=0)
    line_subtotal = models.DecimalField('المجموع قبل الضريبة', max_digits=14, decimal_places=2, default=0)
    line_total = models.DecimalField('المجموع النهائي', max_digits=14, decimal_places=2, default=0)
    returned_quantity = models.DecimalField('الكمية المُرتجعة', max_digits=12, decimal_places=3, default=0)
    batch_number = models.CharField('رقم الدفعة', max_length=100, blank=True)
    serial_number = models.CharField('الرقم التسلسلي', max_length=100, blank=True)
    expiry_date = models.DateField('تاريخ انتهاء الصلاحية', null=True, blank=True)

    class Meta:
        db_table = 'purchase_invoice_lines'
        verbose_name = 'بند شراء'
        verbose_name_plural = 'بنود الشراء'
        ordering = ['id']

    def __str__(self):
        return f'{self.invoice.invoice_number} - {self.item.name}'

    def calculate(self):
        qty = self.quantity or Decimal('0')
        cost = self.unit_cost or Decimal('0')
        subtotal = qty * cost
        tax = subtotal * ((self.tax_rate or Decimal('0')) / Decimal('100'))
        self.line_subtotal = subtotal.quantize(Decimal('0.01'))
        self.tax_amount = tax.quantize(Decimal('0.01'))
        self.line_total = (self.line_subtotal + self.tax_amount).quantize(Decimal('0.01'))

    @property
    def returnable_quantity(self):
        return (self.quantity or Decimal('0')) - (self.returned_quantity or Decimal('0'))


class PurchasePayment(TenantMixin):
    PAYMENT_CHOICES = (
        ('cash', 'نقداً'),
        ('bank', 'تحويل بنكي'),
    )

    invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.CASCADE,
        related_name='payments',
        verbose_name='أمر الشراء',
    )
    payment_method = models.CharField('طريقة الدفع', max_length=10, choices=PAYMENT_CHOICES)
    amount = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    payment_date = models.DateField('تاريخ الدفع')
    reference_number = models.CharField('رقم مرجعي', max_length=100, blank=True)
    notes = models.TextField('ملاحظات', blank=True)
    is_reversed = models.BooleanField('تم عكسها', default=False)

    class Meta:
        db_table = 'purchase_payments'
        verbose_name = 'دفعة شراء'
        verbose_name_plural = 'دفعات الشراء'
        ordering = ['-payment_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'invoice', '-payment_date']),
        ]


class SupplierLedger(TenantMixin):
    ENTRY_TYPE_CHOICES = (
        ('opening', 'رصيد افتتاحي'),
        ('invoice', 'أمر شراء آجل'),
        ('payment', 'سداد للمورد'),
        ('return', 'مرتجع شراء'),
        ('adjustment', 'تعديل يدوي'),
    )

    supplier = models.ForeignKey(
        'suppliers.Supplier',
        on_delete=models.PROTECT,
        related_name='ledger_entries',
        verbose_name='المورد',
    )
    entry_type = models.CharField('نوع القيد', max_length=15, choices=ENTRY_TYPE_CHOICES)
    amount = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    entry_date = models.DateField('تاريخ القيد')
    notes = models.TextField('ملاحظات', blank=True)
    reference_type = models.CharField('نوع المرجع', max_length=50, blank=True)
    reference_id = models.PositiveBigIntegerField('رقم المرجع', null=True, blank=True)
    running_balance = models.DecimalField('الرصيد التراكمي', max_digits=14, decimal_places=2, default=0)
    is_reversal = models.BooleanField('قيد عكسي', default=False)

    class Meta:
        db_table = 'supplier_ledger'
        verbose_name = 'قيد حساب مورد'
        verbose_name_plural = 'سجل حسابات الموردين'
        ordering = ['-entry_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'supplier', '-entry_date']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]


class PurchaseReturn(TenantMixin):
    STATUS_CHOICES = (
        ('draft', 'مسودة'),
        ('confirmed', 'مؤكد'),
        ('cancelled', 'ملغي'),
    )

    REFUND_CHOICES = (
        ('cash', 'نقدي'),
        ('bank', 'بنكي'),
        ('balance', 'رصيد مورد'),
    )

    return_number = models.CharField('رقم المرتجع', max_length=20, blank=True)
    return_date = models.DateField('تاريخ المرتجع')
    original_invoice = models.ForeignKey(
        PurchaseInvoice,
        on_delete=models.PROTECT,
        related_name='purchase_returns',
        verbose_name='أمر الشراء الأصلي',
    )
    status = models.CharField('الحالة', max_length=20, choices=STATUS_CHOICES, default='draft')
    refund_method = models.CharField('طريقة التسوية', max_length=10, choices=REFUND_CHOICES, default='balance')
    total_returned = models.DecimalField('إجمالي المرتجع', max_digits=14, decimal_places=2, default=0)
    reason = models.TextField('السبب', blank=True)
    notes = models.TextField('ملاحظات', blank=True)
    confirmed_by = models.ForeignKey(
        'accounts.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_purchase_returns',
        verbose_name='أُكد بواسطة',
    )

    class Meta:
        db_table = 'purchase_returns'
        verbose_name = 'مرتجع شراء'
        verbose_name_plural = 'مرتجعات الشراء'
        ordering = ['-return_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'return_date']),
            models.Index(fields=['tenant', 'return_number']),
        ]

    def save(self, *args, **kwargs):
        if not self.return_number:
            last = (
                PurchaseReturn.objects.filter(tenant=self.tenant)
                .exclude(return_number='')
                .order_by('-id')
                .first()
            )
            seq = 1
            if last and last.return_number.startswith('PRR-'):
                try:
                    seq = int(last.return_number.split('-')[1]) + 1
                except (IndexError, ValueError):
                    seq = 1
            self.return_number = f'PRR-{seq:06d}'
        super().save(*args, **kwargs)


class PurchaseReturnLine(TenantMixin):
    purchase_return = models.ForeignKey(
        PurchaseReturn,
        on_delete=models.CASCADE,
        related_name='lines',
        verbose_name='المرتجع',
    )
    invoice_line = models.ForeignKey(
        PurchaseInvoiceLine,
        on_delete=models.PROTECT,
        related_name='return_lines',
        verbose_name='بند أمر الشراء',
    )
    item = models.ForeignKey(
        'items.Item',
        on_delete=models.PROTECT,
        related_name='purchase_return_lines',
        verbose_name='المنتج',
    )
    returned_quantity = models.DecimalField('الكمية المرتجعة', max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField('سعر الوحدة', max_digits=14, decimal_places=2)
    line_total = models.DecimalField('إجمالي السطر', max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = 'purchase_return_lines'
        verbose_name = 'بند مرتجع شراء'
        verbose_name_plural = 'بنود مرتجعات الشراء'
        ordering = ['id']


# ============================================================
# PURCHASE RFQ  (طلب عرض أسعار شراء)
# ============================================================

class PurchaseRFQ(TenantMixin):
    """
    Request for Quotation — طلب عرض أسعار للموردين.

    دورة الحياة:
      draft → sent → received → accepted → converted (تحوّل لأمر شراء)
                              ↘ rejected
           ↘ cancelled
    """

    STATUS_CHOICES = (
        ('draft',     'مسودة'),
        ('sent',      'مُرسَل للمورد'),
        ('received',  'استُلم الرد'),
        ('accepted',  'مقبول'),
        ('rejected',  'مرفوض'),
        ('converted', 'مُحوَّل لأمر شراء'),
        ('cancelled', 'ملغى'),
    )

    rfq_number   = models.CharField('رقم الطلب', max_length=20, blank=True)
    rfq_date     = models.DateField('تاريخ الطلب')
    expiry_date  = models.DateField('تاريخ الانتهاء', null=True, blank=True)

    supplier = models.ForeignKey(
        'suppliers.Supplier',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='purchase_rfqs',
        verbose_name='المورد'
    )
    stock = models.ForeignKey(
        'stocks.Stock',
        on_delete=models.PROTECT,
        related_name='purchase_rfqs',
        verbose_name='المخزن'
    )

    status = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='draft')
    notes  = models.TextField('ملاحظات', blank=True)
    terms  = models.TextField('الشروط والأحكام', blank=True)

    grand_total = models.DecimalField(
        'الإجمالي المقتبَس', max_digits=14, decimal_places=2, default=0
    )

    converted_invoice = models.OneToOneField(
        PurchaseInvoice,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='source_rfq',
        verbose_name='أمر الشراء المُحوَّل'
    )

    class Meta:
        db_table = 'purchase_rfqs'
        verbose_name = 'طلب عرض أسعار'
        verbose_name_plural = 'طلبات عروض الأسعار'
        ordering = ['-rfq_date', '-id']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', '-rfq_date']),
        ]

    def __str__(self):
        return self.rfq_number

    def save(self, *args, **kwargs):
        if not self.rfq_number:
            last = (
                PurchaseRFQ.objects.filter(tenant=self.tenant)
                .exclude(rfq_number='')
                .order_by('-id').first()
            )
            next_num = 1
            if last and last.rfq_number.startswith('RFQ-'):
                try:
                    next_num = int(last.rfq_number.split('-')[-1]) + 1
                except ValueError:
                    pass
            self.rfq_number = f"RFQ-{next_num:05d}"
        super().save(*args, **kwargs)

    def recalculate_total(self):
        total = sum(ln.line_total for ln in self.lines.all())
        self.grand_total = total
        self.save(update_fields=['grand_total', 'updated_at'])


class PurchaseRFQLine(TenantMixin):
    rfq      = models.ForeignKey(
        PurchaseRFQ, on_delete=models.CASCADE,
        related_name='lines', verbose_name='طلب العرض'
    )
    item     = models.ForeignKey(
        'items.Item', on_delete=models.PROTECT,
        related_name='rfq_lines', verbose_name='المنتج'
    )
    requested_quantity = models.DecimalField('الكمية المطلوبة', max_digits=12, decimal_places=4)
    quoted_price       = models.DecimalField(
        'السعر المقتبَس', max_digits=14, decimal_places=2, default=0,
        help_text='يُملأ عند استلام رد المورد'
    )
    line_total         = models.DecimalField('إجمالي السطر', max_digits=14, decimal_places=2, default=0)
    notes              = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'purchase_rfq_lines'
        verbose_name = 'بند طلب عرض'
        verbose_name_plural = 'بنود طلب العرض'
        ordering = ['id']

    def __str__(self):
        return f"{self.item.name} × {self.requested_quantity}"

    def save(self, *args, **kwargs):
        self.line_total = (self.quoted_price or Decimal('0')) * self.requested_quantity
        super().save(*args, **kwargs)
