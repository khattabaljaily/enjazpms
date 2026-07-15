import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantMixin


class Agent(TenantMixin):
    COMMISSION_TYPE_CHOICES = (
        ('none',       'بدون عمولة'),
        ('percentage', 'نسبة مئوية'),
        ('fixed',      'مبلغ ثابت'),
    )

    code = models.CharField('كود المندوب', max_length=20, blank=True)
    name = models.CharField('اسم المندوب', max_length=200)
    phone = models.CharField('رقم الهاتف', max_length=20, blank=True)
    email = models.EmailField('البريد الإلكتروني', blank=True)
    city = models.CharField('المدينة', max_length=100, blank=True)
    address = models.TextField('العنوان', blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    COMMISSION_BASIS_CHOICES = (
        ('invoice',    'نسبة من الفاتورة'),
        ('collection', 'نسبة من التحصيل'),
        ('both',       'نسبة من الفاتورة والتحصيل معاً'),
    )

    commission_type = models.CharField(
        'نوع العمولة', max_length=15,
        choices=COMMISSION_TYPE_CHOICES, default='none'
    )
    commission_basis = models.CharField(
        'أساس العمولة', max_length=10,
        choices=COMMISSION_BASIS_CHOICES, default='invoice',
        help_text='هل تُحسب العمولة على إجمالي الفاتورة أم على المبالغ المُحصَّلة فعلياً أم الاثنين'
    )
    commission_rate = models.DecimalField(
        'معدل العمولة', max_digits=10, decimal_places=4, default=0,
        help_text='نسبة % أو مبلغ ثابت حسب نوع العمولة (على الفاتورة، أو على التحصيل عند اختيار أساس واحد)'
    )
    commission_rate_collection = models.DecimalField(
        'معدل عمولة التحصيل', max_digits=10, decimal_places=4, default=0,
        help_text='يُستخدم فقط عند اختيار أساس «الفاتورة والتحصيل معاً» — معدل الشق الخاص بالتحصيل'
    )

    opening_balance = models.DecimalField(
        'المستحقات الافتتاحية', max_digits=12, decimal_places=2, default=0
    )

    is_active = models.BooleanField('نشط', default=True)

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='agent_profile',
        verbose_name='حساب المستخدم',
    )
    portal_password = models.CharField(
        'كلمة مرور البوابة', max_length=64, blank=True,
        help_text='آخر كلمة مرور مُنشأة للبوابة — للعرض الإداري فقط'
    )

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

    def _commission_amount(self, base_amount: Decimal, rate: Decimal) -> Decimal:
        if self.commission_type == 'percentage':
            return (base_amount * rate / Decimal('100')).quantize(Decimal('0.01'))
        if self.commission_type == 'fixed':
            return rate.quantize(Decimal('0.01'))
        return Decimal('0')

    def invoice_commission(self, grand_total: Decimal) -> Decimal:
        if self.commission_basis not in ('invoice', 'both'):
            return Decimal('0')
        return self._commission_amount(grand_total, self.commission_rate)

    def collection_commission(self, paid_amount: Decimal) -> Decimal:
        if self.commission_basis not in ('collection', 'both'):
            return Decimal('0')
        rate = self.commission_rate_collection if self.commission_basis == 'both' else self.commission_rate
        return self._commission_amount(paid_amount, rate)


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


class AgentInvoiceRequest(TenantMixin):
    STATUS_CHOICES = (
        ('pending',  'في الانتظار'),
        ('approved', 'تم الاعتماد'),
        ('rejected', 'مرفوض'),
    )

    request_number = models.CharField('رقم الطلب', max_length=20, blank=True)
    agent = models.ForeignKey(
        Agent, on_delete=models.PROTECT,
        related_name='invoice_requests', verbose_name='المندوب',
    )

    customer = models.ForeignKey(
        'customers.Customer',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='agent_requests',
        verbose_name='العميل',
    )
    customer_name  = models.CharField('اسم العميل', max_length=200)
    customer_phone = models.CharField('هاتف العميل', max_length=30, blank=True)
    notes          = models.TextField('ملاحظات', blank=True)
    admin_comment  = models.TextField('تعليق المدير', blank=True)

    status = models.CharField('الحالة', max_length=10, choices=STATUS_CHOICES, default='pending')

    subtotal     = models.DecimalField('المجموع', max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField('الإجمالي', max_digits=14, decimal_places=2, default=0)

    sale_invoice = models.OneToOneField(
        'sales.SaleInvoice',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='agent_request',
        verbose_name='الفاتورة المُنشأة',
    )

    class Meta:
        db_table  = 'agent_invoice_requests'
        verbose_name = 'طلب فاتورة مندوب'
        verbose_name_plural = 'طلبات فواتير المناديب'
        ordering = ['-created_at']
        indexes  = [models.Index(fields=['tenant', 'status', '-created_at'])]

    def __str__(self):
        return f'{self.request_number} — {self.agent.name}'

    def save(self, *args, **kwargs):
        if not self.request_number:
            last = AgentInvoiceRequest.objects.filter(tenant=self.tenant).exclude(request_number='').order_by('-id').first()
            n = 1
            if last and last.request_number.startswith('AGR-'):
                try:
                    n = int(last.request_number.split('-')[-1]) + 1
                except ValueError:
                    n = AgentInvoiceRequest.objects.filter(tenant=self.tenant).count() + 1
            self.request_number = f'AGR-{n:05d}'
        super().save(*args, **kwargs)

    @property
    def is_pending(self):
        return self.status == 'pending'


class AgentInvoiceRequestLine(TenantMixin):
    request    = models.ForeignKey(AgentInvoiceRequest, on_delete=models.CASCADE, related_name='lines')
    item       = models.ForeignKey('items.Item', on_delete=models.PROTECT, verbose_name='المنتج')
    quantity   = models.DecimalField('الكمية', max_digits=14, decimal_places=4)
    unit_price = models.DecimalField('سعر الوحدة', max_digits=14, decimal_places=2)
    line_total = models.DecimalField('الإجمالي', max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = 'agent_invoice_request_lines'

    def save(self, *args, **kwargs):
        self.line_total = (self.quantity * self.unit_price).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)
