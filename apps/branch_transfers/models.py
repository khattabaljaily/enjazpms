from django.conf import settings
from django.db import models

from apps.core.models import TenantMixin


class BranchTransfer(TenantMixin):
    """
    تحويل مالي بين الإدارة المركزية وأحد الفروع (نسخة المؤسسات)، نقداً (خزينة) أو بنكياً (حساب بنكي)
    لكل طرف، بنفس العملة دائماً وبلا سعر صرف.

    دورة الحياة (مثل الشحنات): يُرسل الطرف الأول فيخرج المبلغ من رصيده فوراً ويبقى «في الطريق»،
    ثم يؤكد الطرف الآخر المبلغ الفعلي المستلَم فيدخل رصيده، ويعود أي نقص تلقائياً للمُرسِل مع
    تعليم التحويل (has_difference) للمراجعة. يمكن للمُرسِل إلغاء التحويل قبل الاستلام.
    """
    DIRECTION_CHOICES = (
        ('to_branch', 'من الإدارة إلى الفرع'),
        ('to_head_office', 'من الفرع إلى الإدارة'),
    )
    STATUS_CHOICES = (
        ('in_transit', 'في الطريق'),
        ('received', 'تم الاستلام'),
        ('cancelled', 'ملغى'),
    )
    STATUS_COLORS = {'in_transit': 'warning', 'received': 'success', 'cancelled': 'dark'}

    number = models.CharField('رقم التحويل', max_length=20, blank=True)
    direction = models.CharField('الاتجاه', max_length=15, choices=DIRECTION_CHOICES)
    status = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='in_transit')
    branch = models.ForeignKey(
        'core.Branch', on_delete=models.PROTECT, related_name='branch_transfers', verbose_name='الفرع')
    transfer_date = models.DateField('تاريخ التحويل')
    amount = models.DecimalField('المبلغ المحوَّل', max_digits=14, decimal_places=2)
    received_amount = models.DecimalField('المبلغ المستلَم', max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField('العملة', max_length=3, blank=True, default='')

    # طرف المصدر: خزينة (نقداً) أو حساب بنكي — واحد فقط.
    from_treasury = models.ForeignKey(
        'treasury.Treasury', on_delete=models.PROTECT, null=True, blank=True,
        related_name='branch_transfers_out', verbose_name='من خزينة')
    from_bank = models.ForeignKey(
        'bank_accounts.BankAccount', on_delete=models.PROTECT, null=True, blank=True,
        related_name='branch_transfers_out', verbose_name='من حساب بنكي')
    # طرف الوجهة.
    to_treasury = models.ForeignKey(
        'treasury.Treasury', on_delete=models.PROTECT, null=True, blank=True,
        related_name='branch_transfers_in', verbose_name='إلى خزينة')
    to_bank = models.ForeignKey(
        'bank_accounts.BankAccount', on_delete=models.PROTECT, null=True, blank=True,
        related_name='branch_transfers_in', verbose_name='إلى حساب بنكي')

    notes = models.TextField('ملاحظات', blank=True)
    sent_at = models.DateTimeField('وقت الإرسال', null=True, blank=True)
    received_at = models.DateTimeField('وقت الاستلام', null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='استلمه')
    has_difference = models.BooleanField('فيه فرق استلام', default=False)
    cancelled_at = models.DateTimeField('وقت الإلغاء', null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='ألغاه')

    class Meta:
        db_table = 'branch_transfers'
        verbose_name = 'تحويل فرع'
        verbose_name_plural = 'تحويلات الفروع'
        ordering = ['-transfer_date', '-id']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'branch', 'status']),
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            last = (
                BranchTransfer.objects.filter(tenant=self.tenant, number__startswith='BT-')
                .order_by('-id').first()
            )
            next_num = 1
            if last:
                try:
                    next_num = int(last.number.split('-')[-1]) + 1
                except ValueError:
                    pass
            self.number = f'BT-{next_num:05d}'
        super().save(*args, **kwargs)

    # ── أطراف التحويل ──
    @property
    def source(self):
        return self.from_treasury or self.from_bank

    @property
    def destination(self):
        return self.to_treasury or self.to_bank

    @property
    def source_kind(self):
        return 'cash' if self.from_treasury_id else 'bank'

    @property
    def destination_kind(self):
        return 'cash' if self.to_treasury_id else 'bank'

    @property
    def difference(self):
        if self.received_amount is None:
            return None
        return self.amount - self.received_amount

    @property
    def status_color(self):
        return self.STATUS_COLORS.get(self.status, 'secondary')

    @property
    def sender_is_head_office(self):
        return self.direction == 'to_branch'
