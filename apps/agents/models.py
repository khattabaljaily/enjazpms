from decimal import Decimal

from django.db import models

from apps.core.models import TenantMixin


class Agent(TenantMixin):
    COMMISSION_TYPE_CHOICES = (
        ('none',       'بدون عمولة'),
        ('percentage', 'نسبة مئوية من الفاتورة'),
        ('fixed',      'مبلغ ثابت لكل فاتورة'),
    )

    code = models.CharField('كود المندوب', max_length=20, blank=True)
    name = models.CharField('اسم المندوب', max_length=200)
    phone = models.CharField('رقم الهاتف', max_length=20, blank=True)
    email = models.EmailField('البريد الإلكتروني', blank=True)
    city = models.CharField('المدينة', max_length=100, blank=True)
    address = models.TextField('العنوان', blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    commission_type = models.CharField(
        'نوع العمولة', max_length=15,
        choices=COMMISSION_TYPE_CHOICES, default='none'
    )
    commission_rate = models.DecimalField(
        'معدل العمولة', max_digits=10, decimal_places=4, default=0,
        help_text='نسبة % أو مبلغ ثابت حسب نوع العمولة'
    )

    opening_balance = models.DecimalField(
        'المستحقات الافتتاحية', max_digits=12, decimal_places=2, default=0
    )

    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'agents'
        verbose_name = 'مندوب'
        verbose_name_plural = 'المناديب'
        ordering = ['-created_at']
        unique_together = [('tenant', 'code')]
        indexes = [
            models.Index(fields=['tenant', 'name']),
            models.Index(fields=['tenant', 'phone']),
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            last = (
                Agent.objects.filter(tenant=self.tenant)
                .exclude(code='')
                .order_by('-id')
                .first()
            )
            next_num = 1
            if last and last.code.startswith('AGT-'):
                try:
                    next_num = int(last.code.split('-')[-1]) + 1
                except ValueError:
                    next_num = Agent.objects.filter(tenant=self.tenant).count() + 1
            self.code = f'AGT-{next_num:05d}'
        super().save(*args, **kwargs)

    def calculate_commission(self, invoice_total: Decimal) -> Decimal:
        if self.commission_type == 'percentage':
            return (invoice_total * self.commission_rate / Decimal('100')).quantize(Decimal('0.01'))
        if self.commission_type == 'fixed':
            return self.commission_rate.quantize(Decimal('0.01'))
        return Decimal('0')


class AgentLedger(TenantMixin):
    ENTRY_TYPE_CHOICES = (
        ('commission',  'عمولة مبيعات'),
        ('payment',     'دفعة للمندوب'),
        ('return',      'مرتجع مبيعات'),
        ('adjustment',  'تعديل يدوي'),
        ('opening',     'مستحقات افتتاحية'),
    )

    agent = models.ForeignKey(
        Agent,
        on_delete=models.PROTECT,
        related_name='ledger_entries',
        verbose_name='المندوب',
    )
    entry_type = models.CharField('نوع القيد', max_length=15, choices=ENTRY_TYPE_CHOICES)
    amount = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    entry_date = models.DateField('تاريخ القيد')
    notes = models.TextField('ملاحظات', blank=True)
    reference_type = models.CharField('نوع المرجع', max_length=50, blank=True)
    reference_id = models.PositiveBigIntegerField('رقم المرجع', null=True, blank=True)
    running_balance = models.DecimalField('المستحقات التراكمية', max_digits=14, decimal_places=2, default=0)
    is_reversal = models.BooleanField('قيد عكسي', default=False)

    class Meta:
        db_table = 'agent_ledger'
        verbose_name = 'قيد حساب مندوب'
        verbose_name_plural = 'سجل حسابات المناديب'
        ordering = ['-entry_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'agent', '-entry_date']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        return f'{self.agent.name} — {self.entry_type} — {self.amount}'
