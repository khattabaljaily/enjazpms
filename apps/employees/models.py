"""
Employees — الموظفون
نظام الرواتب والسلف والحوافز والخصومات
"""
from decimal import Decimal
from django.db import models
from django.utils import timezone

from apps.core.models import TenantMixin
from apps.treasury.models import Treasury


class Employee(TenantMixin):
    SALARY_TYPE = [
        ('fixed',  'راتب ثابت'),
        ('hourly', 'بالساعة'),
        ('daily',  'بالأيام'),
    ]

    name        = models.CharField('الاسم', max_length=200)
    employee_id = models.CharField('رقم الموظف', max_length=30, blank=True)
    phone       = models.CharField('الهاتف', max_length=20, blank=True)
    position    = models.CharField('المسمى الوظيفي', max_length=200, blank=True)
    department  = models.CharField('القسم', max_length=200, blank=True)
    photo       = models.ImageField('الصورة', upload_to='employees/', blank=True, null=True)

    salary_type = models.CharField('نوع الراتب', max_length=20, choices=SALARY_TYPE, default='fixed')
    base_salary = models.DecimalField('الراتب الأساسي', max_digits=14, decimal_places=2, default=Decimal('0'))

    hire_date   = models.DateField('تاريخ التعيين', null=True, blank=True)
    is_active   = models.BooleanField('نشط', default=True)
    notes       = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'employees'
        verbose_name = 'موظف'
        verbose_name_plural = 'الموظفين'
        ordering = ['name']
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.employee_id:
            last = (
                Employee.objects.filter(tenant=self.tenant)
                .exclude(employee_id='')
                .order_by('-id')
                .first()
            )
            next_num = 1
            if last and last.employee_id.startswith('EMP-'):
                try:
                    next_num = int(last.employee_id.split('-')[-1]) + 1
                except ValueError:
                    next_num = Employee.objects.filter(tenant=self.tenant).count() + 1
            self.employee_id = f'EMP-{next_num:04d}'
        super().save(*args, **kwargs)

    @property
    def pending_advances_total(self):
        return (
            self.advances.filter(status='pending')
            .aggregate(t=models.Sum('amount'))['t'] or Decimal('0')
        )


class EmployeeAdvance(TenantMixin):
    """سلفة موظف"""
    STATUS = [
        ('pending',   'قائمة'),
        ('deducted',  'مخصومة'),
        ('cancelled', 'ملغاة'),
    ]
    PAYMENT_METHOD = [('cash', 'نقدي'), ('bank', 'تحويل بنكي')]

    employee        = models.ForeignKey(
        Employee, on_delete=models.CASCADE,
        related_name='advances', verbose_name='الموظف'
    )
    amount          = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    date            = models.DateField('التاريخ', default=timezone.localdate)
    payment_method  = models.CharField('طريقة الدفع', max_length=10, choices=PAYMENT_METHOD, default='cash')
    treasury        = models.ForeignKey(
        Treasury, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='الخزينة / الحساب'
    )
    bank_reference  = models.CharField('رقم الحوالة', max_length=100, blank=True)
    salary_payment  = models.ForeignKey(
        'EmployeeSalaryPayment', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='advance_items', verbose_name='كشف الراتب'
    )
    status          = models.CharField('الحالة', max_length=20, choices=STATUS, default='pending')
    notes           = models.TextField('ملاحظات', blank=True)

    # Treasury movement tracking
    treasury_movement = models.OneToOneField(
        'treasury.TreasuryMovement',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='employee_advance',
        verbose_name='حركة الخزينة',
    )

    class Meta:
        db_table = 'employee_advances'
        verbose_name = 'سلفة موظف'
        verbose_name_plural = 'سلف الموظفين'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'employee']),
        ]

    def __str__(self):
        return f'{self.employee.name} — {self.amount}'

    def cancel(self):
        if self.status == 'cancelled':
            return
        from django.db import transaction
        from apps.treasury.services import post_treasury_receipt
        with transaction.atomic():
            if self.payment_method == 'cash' and self.treasury_movement and self.treasury:
                post_treasury_receipt(
                    tenant=self.tenant,
                    amount=self.amount,
                    date=timezone.localdate(),
                    reference_type='employee_advance_cancel',
                    reference_id=self.pk,
                    description=f'إلغاء سلفة {self.employee.name}',
                    treasury=self.treasury,
                )
            self.status = 'cancelled'
            self.save(update_fields=['status', 'updated_at'])


class EmployeeSalaryPayment(TenantMixin):
    """كشف راتب شهري للموظف"""
    STATUS = [
        ('draft',     'مسودة'),
        ('paid',      'مدفوع'),
        ('cancelled', 'ملغى'),
    ]

    PAYMENT_METHOD = [('cash', 'نقدي'), ('bank', 'تحويل بنكي')]

    employee          = models.ForeignKey(
        Employee, on_delete=models.CASCADE,
        related_name='salary_payments', verbose_name='الموظف'
    )
    period_start      = models.DateField('من')
    period_end        = models.DateField('إلى')
    base_salary       = models.DecimalField('الراتب الأساسي', max_digits=14, decimal_places=2, default=Decimal('0'))
    bonus             = models.DecimalField('الحوافز', max_digits=14, decimal_places=2, default=Decimal('0'))
    advances_deducted = models.DecimalField('السلف المخصومة', max_digits=14, decimal_places=2, default=Decimal('0'))
    deductions        = models.DecimalField('خصومات أخرى', max_digits=14, decimal_places=2, default=Decimal('0'))
    deductions_notes  = models.TextField('تفاصيل الخصومات', blank=True)
    payment_method    = models.CharField('طريقة الدفع', max_length=10, choices=PAYMENT_METHOD, default='cash')
    treasury          = models.ForeignKey(
        Treasury, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='الخزينة / الحساب'
    )
    bank_reference    = models.CharField('رقم الحوالة', max_length=100, blank=True)
    status            = models.CharField('الحالة', max_length=20, choices=STATUS, default='draft')
    notes             = models.TextField('ملاحظات', blank=True)

    # Treasury movement tracking
    treasury_movement = models.OneToOneField(
        'treasury.TreasuryMovement',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='employee_salary',
        verbose_name='حركة الخزينة',
    )

    class Meta:
        db_table = 'employee_salary_payments'
        verbose_name = 'كشف راتب'
        verbose_name_plural = 'كشوف الرواتب'
        ordering = ['-period_start', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'employee']),
            models.Index(fields=['tenant', 'period_start']),
        ]

    def __str__(self):
        return f'{self.employee.name} — {self.period_start}'

    @property
    def total_due(self):
        return (
            self.base_salary
            + self.bonus
            - self.advances_deducted
            - self.deductions
        )

    def get_pending_with_salary_incentives(self):
        return self.employee.incentives.filter(
            status__in=('pending', 'paid'),
            payout='with_salary',
            date__range=(self.period_start, self.period_end),
        ).order_by('date', 'pk')

    def pay(self):
        if self.status != 'draft':
            return
        from django.db import transaction
        from apps.treasury.services import post_treasury_disbursement
        with transaction.atomic():
            self.status = 'paid'
            self.save(update_fields=['status', 'updated_at'])

            if self.payment_method == 'cash' and self.treasury and self.total_due > 0:
                mv = post_treasury_disbursement(
                    tenant=self.tenant,
                    amount=self.total_due,
                    date=timezone.localdate(),
                    reference_type='employee_salary',
                    reference_id=self.pk,
                    description=f'راتب {self.employee.name} — {self.period_start}',
                    treasury=self.treasury,
                )
                if mv:
                    self.treasury_movement = mv
                    self.save(update_fields=['treasury_movement', 'updated_at'])

            # Mark linked advances as deducted
            self.advance_items.filter(status='pending').update(status='deducted')

            # Mark deferred incentives/deductions as paid once the salary is paid
            self.employee.incentives.filter(
                status='pending',
                payout='with_salary',
                date__range=(self.period_start, self.period_end),
            ).update(status='paid')

    def cancel(self):
        if self.status == 'cancelled':
            return
        from django.db import transaction
        from apps.treasury.services import post_treasury_receipt
        with transaction.atomic():
            if self.payment_method == 'cash' and self.treasury_movement and self.treasury and self.total_due > 0:
                post_treasury_receipt(
                    tenant=self.tenant,
                    amount=self.total_due,
                    date=timezone.localdate(),
                    reference_type='employee_salary_cancel',
                    reference_id=self.pk,
                    description=f'إلغاء راتب {self.employee.name} — {self.period_start}',
                    treasury=self.treasury,
                )

            # Restore linked advances to pending
            self.advance_items.filter(status='deducted').update(
                status='pending', salary_payment=None
            )
            self.status = 'cancelled'
            self.save(update_fields=['status', 'updated_at'])


class EmployeeIncentive(TenantMixin):
    """حافز أو خصم للموظف"""
    TYPE           = [('bonus', 'حافز'), ('deduction', 'خصم')]
    PAYOUT         = [('immediate', 'فوري'), ('with_salary', 'مع الراتب')]
    STATUS         = [('pending', 'معلق'), ('paid', 'مدفوع'), ('cancelled', 'ملغى')]
    PAYMENT_METHOD = [('cash', 'نقدي'), ('bank', 'تحويل بنكي')]

    employee        = models.ForeignKey(
        Employee, on_delete=models.CASCADE,
        related_name='incentives', verbose_name='الموظف'
    )
    type            = models.CharField('النوع', max_length=20, choices=TYPE, default='bonus')
    amount          = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    description     = models.CharField('الوصف', max_length=255)
    payout          = models.CharField('طريقة الصرف', max_length=20, choices=PAYOUT, default='with_salary')
    payment_method  = models.CharField('طريقة الدفع', max_length=10, choices=PAYMENT_METHOD, default='cash')
    treasury        = models.ForeignKey(
        Treasury, on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name='الخزينة / الحساب'
    )
    bank_reference  = models.CharField('رقم الحوالة', max_length=100, blank=True)
    status          = models.CharField('الحالة', max_length=20, choices=STATUS, default='pending')
    date            = models.DateField('التاريخ', default=timezone.localdate)
    notes           = models.TextField('ملاحظات', blank=True)

    # Treasury movement tracking
    treasury_movement = models.OneToOneField(
        'treasury.TreasuryMovement',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='employee_incentive',
        verbose_name='حركة الخزينة',
    )

    class Meta:
        db_table = 'employee_incentives'
        verbose_name = 'حافز / خصم'
        verbose_name_plural = 'الحوافز والخصومات'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'type']),
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'employee']),
        ]

    def __str__(self):
        return f'{self.get_type_display()} — {self.employee.name} — {self.amount}'

    def pay(self):
        """دفع الحافز فوراً من الخزينة (للحوافز الفورية النقدية فقط)"""
        if self.status != 'pending' or self.type != 'bonus':
            return
        from django.db import transaction
        from apps.treasury.services import post_treasury_disbursement
        with transaction.atomic():
            self.status = 'paid'
            self.save(update_fields=['status', 'updated_at'])

            if self.payment_method == 'cash' and self.treasury:
                mv = post_treasury_disbursement(
                    tenant=self.tenant,
                    amount=self.amount,
                    date=timezone.localdate(),
                    reference_type='employee_incentive',
                    reference_id=self.pk,
                    description=f'حافز {self.employee.name} — {self.description}',
                    treasury=self.treasury,
                )
                if mv:
                    self.treasury_movement = mv
                    self.save(update_fields=['treasury_movement', 'updated_at'])

    def cancel(self):
        if self.status == 'cancelled':
            return
        from django.db import transaction
        from apps.treasury.services import post_treasury_receipt
        with transaction.atomic():
            if self.payment_method == 'cash' and self.treasury_movement and self.treasury and self.type == 'bonus':
                post_treasury_receipt(
                    tenant=self.tenant,
                    amount=self.amount,
                    date=timezone.localdate(),
                    reference_type='employee_incentive_cancel',
                    reference_id=self.pk,
                    description=f'إلغاء حافز {self.employee.name} — {self.description}',
                    treasury=self.treasury,
                )
            self.status = 'cancelled'
            self.save(update_fields=['status', 'updated_at'])
