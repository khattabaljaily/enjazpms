from django.db import models
from apps.core.models import TenantMixin
from apps.treasury.models import Treasury


class ExpenseCategory(TenantMixin):
    name = models.CharField('اسم التصنيف', max_length=200)
    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'expense_categories'
        verbose_name = 'تصنيف مصروف'
        verbose_name_plural = 'تصنيفات المصروفات'
        ordering = ['name']
        unique_together = [('tenant', 'name')]

    def __str__(self):
        return self.name


class Expense(TenantMixin):
    STATUS_DRAFT = 'draft'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'مسودة'),
        (STATUS_CONFIRMED, 'مؤكد'),
        (STATUS_CANCELLED, 'ملغي'),
    ]

    PAYMENT_CASH = 'cash'
    PAYMENT_BANK = 'bank'
    PAYMENT_CHOICES = [
        (PAYMENT_CASH, 'نقدي'),
        (PAYMENT_BANK, 'بنكي'),
    ]

    code = models.CharField('رقم المصروف', max_length=30, blank=True)
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name='expenses',
        verbose_name='التصنيف',
    )
    description = models.CharField('الوصف', max_length=500)
    amount = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    expense_date = models.DateField('تاريخ المصروف')
    payment_method = models.CharField('طريقة الدفع', max_length=10, choices=PAYMENT_CHOICES, default=PAYMENT_CASH)
    treasury = models.ForeignKey(
        Treasury,
        on_delete=models.PROTECT,
        related_name='expenses',
        verbose_name='الخزينة / الحساب',
        null=True,
        blank=True,
    )
    reference_number = models.CharField('رقم المرجع', max_length=100, blank=True,
                                        help_text='رقم الحوالة أو أمر الدفع عند التحويل البنكي')
    notes = models.TextField('ملاحظات', blank=True)
    status = models.CharField('الحالة', max_length=15, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # Treasury effect tracking
    treasury_movement = models.OneToOneField(
        'treasury.TreasuryMovement',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expense',
        verbose_name='حركة الخزينة',
    )

    class Meta:
        db_table = 'expenses'
        verbose_name = 'مصروف'
        verbose_name_plural = 'المصروفات'
        ordering = ['-expense_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'expense_date']),
            models.Index(fields=['tenant', 'category']),
        ]

    def __str__(self):
        return f'{self.code} — {self.description}'

    def save(self, *args, **kwargs):
        if not self.code:
            last = (
                Expense.objects.filter(tenant=self.tenant)
                .exclude(code='')
                .order_by('-id')
                .first()
            )
            next_num = 1
            if last and last.code.startswith('EXP-'):
                try:
                    next_num = int(last.code.split('-')[-1]) + 1
                except ValueError:
                    next_num = Expense.objects.filter(tenant=self.tenant).count() + 1
            self.code = f'EXP-{next_num:05d}'
        super().save(*args, **kwargs)
