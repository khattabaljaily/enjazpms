from django.db import models

from apps.core.models import TenantMixin


class InsuranceCompany(TenantMixin):
    code = models.CharField('الكود', max_length=20, blank=True)
    name = models.CharField('اسم شركة التأمين', max_length=200)
    contact_person = models.CharField('مسؤول التواصل', max_length=150, blank=True)
    phone = models.CharField('الهاتف', max_length=20, blank=True)
    email = models.EmailField('البريد الإلكتروني', blank=True)
    address = models.TextField('العنوان', blank=True)
    default_coverage_percent = models.DecimalField(
        'نسبة التغطية الافتراضية %', max_digits=5, decimal_places=2, default=0
    )
    settlement_period_days = models.PositiveIntegerField('مدة التسوية المتوقعة (يوم)', default=30)
    is_active = models.BooleanField('نشط', default=True)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'insurance_companies'
        verbose_name = 'شركة تأمين'
        verbose_name_plural = 'شركات التأمين'
        ordering = ['-created_at']
        unique_together = [('tenant', 'code')]
        indexes = [
            models.Index(fields=['tenant', 'name']),
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            last = (
                InsuranceCompany.objects.filter(tenant=self.tenant)
                .exclude(code='')
                .order_by('-id')
                .first()
            )
            next_number = 1
            if last and last.code.startswith('INS-'):
                try:
                    next_number = int(last.code.split('-')[-1]) + 1
                except ValueError:
                    next_number = InsuranceCompany.objects.filter(tenant=self.tenant).count() + 1
            self.code = f"INS-{next_number:05d}"
        super().save(*args, **kwargs)


class CustomerInsurancePolicy(TenantMixin):
    customer = models.ForeignKey(
        'customers.Customer', on_delete=models.CASCADE,
        related_name='insurance_policies', verbose_name='العميل'
    )
    insurance_company = models.ForeignKey(
        InsuranceCompany, on_delete=models.PROTECT,
        related_name='policies', verbose_name='شركة التأمين'
    )
    policy_number = models.CharField('رقم البوليصة / العضوية', max_length=100, blank=True)
    coverage_percent = models.DecimalField('نسبة التغطية %', max_digits=5, decimal_places=2, default=0)
    valid_from = models.DateField('سارية من', null=True, blank=True)
    valid_to = models.DateField('سارية حتى', null=True, blank=True)
    is_active = models.BooleanField('نشطة', default=True)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'customer_insurance_policies'
        verbose_name = 'بوليصة تأمين عميل'
        verbose_name_plural = 'بوليصات تأمين العملاء'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'customer', 'is_active']),
        ]

    def __str__(self):
        return f"{self.customer.name} — {self.insurance_company.name}"

    @property
    def effective_coverage_percent(self):
        return self.coverage_percent or self.insurance_company.default_coverage_percent


class InsuranceClaim(TenantMixin):
    STATUS_CHOICES = (
        ('draft', 'مسودة'),
        ('submitted', 'مُقدَّمة'),
        ('approved', 'معتمدة'),
        ('partially_approved', 'معتمدة جزئياً'),
        ('rejected', 'مرفوضة'),
        ('partially_paid', 'مسددة جزئياً'),
        ('paid', 'مسددة بالكامل'),
        ('cancelled', 'ملغاة'),
    )

    claim_number = models.CharField('رقم المطالبة', max_length=20, blank=True)
    invoice = models.OneToOneField(
        'sales.SaleInvoice', on_delete=models.PROTECT,
        related_name='insurance_claim', verbose_name='الفاتورة'
    )
    customer = models.ForeignKey(
        'customers.Customer', on_delete=models.PROTECT,
        related_name='insurance_claims', verbose_name='العميل'
    )
    insurance_company = models.ForeignKey(
        InsuranceCompany, on_delete=models.PROTECT,
        related_name='claims', verbose_name='شركة التأمين'
    )
    policy = models.ForeignKey(
        CustomerInsurancePolicy, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='claims', verbose_name='البوليصة'
    )
    status = models.CharField('الحالة', max_length=20, choices=STATUS_CHOICES, default='draft')

    covered_amount = models.DecimalField('المبلغ المطالَب به', max_digits=14, decimal_places=2, default=0)
    patient_amount = models.DecimalField('نصيب المريض', max_digits=14, decimal_places=2, default=0)
    approved_amount = models.DecimalField('المبلغ المعتمد', max_digits=14, decimal_places=2, null=True, blank=True)
    paid_amount = models.DecimalField('المبلغ المسدد فعلياً', max_digits=14, decimal_places=2, default=0)

    submitted_at = models.DateTimeField('تاريخ التقديم', null=True, blank=True)
    submitted_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='submitted_insurance_claims', verbose_name='قُدِّمت بواسطة'
    )
    responded_at = models.DateTimeField('تاريخ الرد', null=True, blank=True)
    due_date = models.DateField('تاريخ التسوية المتوقع', null=True, blank=True)
    rejection_reason = models.TextField('سبب الرفض/التخفيض', blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'insurance_claims'
        verbose_name = 'مطالبة تأمين'
        verbose_name_plural = 'مطالبات التأمين'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'customer']),
            models.Index(fields=['tenant', 'insurance_company']),
        ]

    def __str__(self):
        return self.claim_number or f"مطالبة #{self.pk}"

    def save(self, *args, **kwargs):
        if not self.claim_number:
            last = (
                InsuranceClaim.objects.filter(tenant=self.tenant)
                .exclude(claim_number='')
                .order_by('-id')
                .first()
            )
            next_number = 1
            if last and last.claim_number.startswith('CLM-'):
                try:
                    next_number = int(last.claim_number.split('-')[-1]) + 1
                except ValueError:
                    next_number = InsuranceClaim.objects.filter(tenant=self.tenant).count() + 1
            self.claim_number = f"CLM-{next_number:06d}"
        super().save(*args, **kwargs)

    @property
    def approved_or_covered_amount(self):
        return self.approved_amount if self.approved_amount is not None else self.covered_amount

    @property
    def remaining_amount(self):
        return (self.approved_or_covered_amount or 0) - (self.paid_amount or 0)

    @property
    def is_settled(self):
        return self.status in ('paid', 'rejected', 'cancelled')


class InsuranceClaimLine(TenantMixin):
    claim = models.ForeignKey(
        InsuranceClaim, on_delete=models.CASCADE,
        related_name='lines', verbose_name='المطالبة'
    )
    invoice_line = models.ForeignKey(
        'sales.SaleInvoiceLine', on_delete=models.PROTECT,
        related_name='insurance_claim_lines', verbose_name='بند الفاتورة'
    )
    coverage_percent = models.DecimalField('نسبة التغطية %', max_digits=5, decimal_places=2, default=0)
    claimed_amount = models.DecimalField('المبلغ المُطالَب به', max_digits=14, decimal_places=2, default=0)
    approved_amount = models.DecimalField('المبلغ المعتمد', max_digits=14, decimal_places=2, null=True, blank=True)
    rejection_reason = models.CharField('سبب الرفض', max_length=300, blank=True)

    class Meta:
        db_table = 'insurance_claim_lines'
        verbose_name = 'بند مطالبة تأمين'
        verbose_name_plural = 'بنود مطالبات التأمين'

    def __str__(self):
        return f"{self.claim.claim_number} — {self.invoice_line}"


class InsuranceClaimSettlement(TenantMixin):
    ENTRY_TYPE_CHOICES = (
        ('opening', 'رصيد افتتاحي'),
        ('claim_submitted', 'مطالبة مُقدَّمة'),
        ('adjustment', 'تعديل اعتماد'),
        ('payment_received', 'دفعة مستلمة'),
        ('writeoff', 'شطب/رفض'),
    )

    insurance_company = models.ForeignKey(
        InsuranceCompany, on_delete=models.PROTECT,
        related_name='ledger_entries', verbose_name='شركة التأمين'
    )
    claim = models.ForeignKey(
        InsuranceClaim, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='settlement_entries', verbose_name='المطالبة'
    )
    entry_type = models.CharField('نوع القيد', max_length=20, choices=ENTRY_TYPE_CHOICES)
    amount = models.DecimalField(
        'المبلغ', max_digits=14, decimal_places=2,
        help_text='موجب = مستحق لنا على شركة التأمين | سالب = تحصيل/تخفيض'
    )
    entry_date = models.DateField('تاريخ القيد')
    reference_type = models.CharField('نوع المرجع', max_length=50, blank=True)
    reference_id = models.PositiveBigIntegerField('رقم المرجع', null=True, blank=True)
    running_balance = models.DecimalField('الرصيد التراكمي', max_digits=14, decimal_places=2, default=0)
    is_reversal = models.BooleanField('قيد عكسي', default=False)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'insurance_ledger'
        verbose_name = 'قيد تسوية تأمين'
        verbose_name_plural = 'سجل تسويات التأمين'
        ordering = ['-entry_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'insurance_company', '-entry_date']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        return f"{self.insurance_company.name} — {self.get_entry_type_display()} — {self.amount}"
