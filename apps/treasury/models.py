from django.db import models

from apps.core.models import TenantMixin


class Treasury(TenantMixin):
    name = models.CharField('اسم الخزينة', max_length=200)
    code = models.CharField('الرمز', max_length=30, blank=True)
    is_default = models.BooleanField('افتراضية', default=False)
    is_system_default = models.BooleanField('افتراضية نظامية', default=False)
    is_active = models.BooleanField('نشطة', default=True)
    current_balance = models.DecimalField('الرصيد الحالي', max_digits=14, decimal_places=2, default=0)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'treasuries'
        verbose_name = 'خزينة'
        verbose_name_plural = 'الخزائن'
        ordering = ['-is_default', 'name']
        indexes = [
            models.Index(fields=['tenant', 'is_default']),
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['tenant', 'is_system_default']),
        ]

    def __str__(self):
        return self.name


class TreasuryMovement(TenantMixin):
    MOVEMENT_TYPE_CHOICES = (
        ('receipt', 'قبض'),
        ('disbursement', 'صرف'),
        ('adjustment', 'تسوية'),
    )

    treasury = models.ForeignKey(
        Treasury,
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='الخزينة',
    )
    movement_type = models.CharField('نوع الحركة', max_length=20, choices=MOVEMENT_TYPE_CHOICES)
    amount = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    movement_date = models.DateField('تاريخ الحركة')
    description = models.TextField('الوصف', blank=True)
    reference_type = models.CharField('نوع المرجع', max_length=50, blank=True)
    reference_id = models.PositiveBigIntegerField('رقم المرجع', null=True, blank=True)
    running_balance = models.DecimalField('الرصيد بعد الحركة', max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = 'treasury_movements'
        verbose_name = 'حركة خزينة'
        verbose_name_plural = 'حركات الخزينة'
        ordering = ['-movement_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'treasury', '-movement_date']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        return f'{self.get_movement_type_display()} — {self.amount}'
