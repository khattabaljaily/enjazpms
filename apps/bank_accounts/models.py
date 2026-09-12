from django.db import models

from apps.core.models import TenantMixin
from apps.treasury.models import Treasury


class BankAccount(TenantMixin):
    name = models.CharField('اسم الحساب', max_length=200)
    bank_name = models.CharField('اسم البنك', max_length=200, blank=True)
    account_number = models.CharField('رقم الحساب', max_length=100, blank=True)
    iban = models.CharField('الآيبان', max_length=50, blank=True)
    is_default = models.BooleanField('افتراضي', default=False)
    is_active = models.BooleanField('نشط', default=True)
    currency = models.CharField('العملة', max_length=3, blank=True, default='')
    current_balance = models.DecimalField('الرصيد الحالي', max_digits=14, decimal_places=2, default=0)
    notes = models.TextField('ملاحظات', blank=True)

    class Meta:
        db_table = 'bank_accounts'
        verbose_name = 'حساب بنكي'
        verbose_name_plural = 'الحسابات البنكية'
        ordering = ['-is_default', 'name']
        indexes = [
            models.Index(fields=['tenant', 'is_default']),
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        return self.name


class BankAccountMovement(TenantMixin):
    MOVEMENT_TYPE_CHOICES = (
        ('receipt', 'وارد'),
        ('disbursement', 'صادر'),
        ('adjustment', 'تسوية'),
    )

    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='الحساب البنكي',
    )
    movement_type = models.CharField('نوع الحركة', max_length=20, choices=MOVEMENT_TYPE_CHOICES)
    amount = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    movement_date = models.DateField('تاريخ الحركة')
    description = models.TextField('الوصف', blank=True)
    reference_type = models.CharField('نوع المرجع', max_length=50, blank=True)
    reference_id = models.PositiveBigIntegerField('رقم المرجع', null=True, blank=True)
    running_balance = models.DecimalField('الرصيد بعد الحركة', max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = 'bank_account_movements'
        verbose_name = 'حركة حساب بنكي'
        verbose_name_plural = 'حركات الحسابات البنكية'
        ordering = ['-movement_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'bank_account', '-movement_date']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        return f'{self.get_movement_type_display()} — {self.amount}'


class BankAccountTransfer(TenantMixin):
    """تحويل بين حسابين بنكيين — يولّد حركتين مرتبطتين تلقائياً."""

    from_bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT,
        related_name='transfers_out', verbose_name='من حساب',
    )
    to_bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT,
        related_name='transfers_in', verbose_name='إلى حساب',
    )
    from_amount = models.DecimalField('المبلغ المحوَّل', max_digits=14, decimal_places=4)
    to_amount = models.DecimalField('المبلغ المستلَم', max_digits=14, decimal_places=4)
    exchange_rate = models.DecimalField(
        'سعر الصرف', max_digits=12, decimal_places=4, default=1,
        help_text='وحدات العملة الهدف مقابل وحدة واحدة من عملة المصدر',
    )
    transfer_date = models.DateField('تاريخ التحويل')
    notes = models.TextField('ملاحظات', blank=True)
    from_movement = models.OneToOneField(
        BankAccountMovement, on_delete=models.CASCADE,
        related_name='transfer_as_source', null=True, blank=True,
    )
    to_movement = models.OneToOneField(
        BankAccountMovement, on_delete=models.CASCADE,
        related_name='transfer_as_dest', null=True, blank=True,
    )

    class Meta:
        db_table = 'bank_account_transfers'
        verbose_name = 'تحويل بين حسابات بنكية'
        verbose_name_plural = 'تحويلات الحسابات البنكية'
        ordering = ['-transfer_date', '-created_at']

    def __str__(self):
        return f'تحويل {self.from_amount} → {self.to_amount} ({self.transfer_date})'


class TreasuryBankTransfer(TenantMixin):
    """تحويل بين الخزينة وحساب بنكي (بالاتجاهين)."""

    DIRECTION_TREASURY_TO_BANK = 'treasury_to_bank'
    DIRECTION_BANK_TO_TREASURY = 'bank_to_treasury'
    DIRECTION_CHOICES = (
        (DIRECTION_TREASURY_TO_BANK, 'من الخزينة إلى الحساب البنكي'),
        (DIRECTION_BANK_TO_TREASURY, 'من الحساب البنكي إلى الخزينة'),
    )

    direction = models.CharField('الاتجاه', max_length=20, choices=DIRECTION_CHOICES)
    treasury = models.ForeignKey(
        Treasury, on_delete=models.PROTECT,
        related_name='bank_transfers', verbose_name='الخزينة',
    )
    bank_account = models.ForeignKey(
        BankAccount, on_delete=models.PROTECT,
        related_name='treasury_transfers', verbose_name='الحساب البنكي',
    )
    amount = models.DecimalField('المبلغ', max_digits=14, decimal_places=2)
    transfer_date = models.DateField('تاريخ التحويل')
    notes = models.TextField('ملاحظات', blank=True)
    treasury_movement = models.OneToOneField(
        'treasury.TreasuryMovement', on_delete=models.CASCADE,
        related_name='bank_transfer', null=True, blank=True,
    )
    bank_movement = models.OneToOneField(
        BankAccountMovement, on_delete=models.CASCADE,
        related_name='treasury_transfer', null=True, blank=True,
    )

    class Meta:
        db_table = 'treasury_bank_transfers'
        verbose_name = 'تحويل خزينة / حساب بنكي'
        verbose_name_plural = 'تحويلات الخزينة والحسابات البنكية'
        ordering = ['-transfer_date', '-created_at']

    def __str__(self):
        return f'{self.get_direction_display()} — {self.amount} ({self.transfer_date})'
