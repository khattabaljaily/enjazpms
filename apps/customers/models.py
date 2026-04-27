from django.db import models

from apps.core.models import TenantMixin


class Customer(TenantMixin):
    code = models.CharField('كود العميل', max_length=20, blank=True)
    name = models.CharField('اسم العميل', max_length=200)
    phone = models.CharField('رقم الهاتف', max_length=20, blank=True)
    email = models.EmailField('البريد الإلكتروني', blank=True)
    city = models.CharField('المدينة', max_length=100, blank=True)
    address = models.TextField('العنوان', blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    opening_balance = models.DecimalField('الرصيد الافتتاحي', max_digits=12, decimal_places=2, default=0)
    credit_limit = models.DecimalField('الحد الائتماني', max_digits=12, decimal_places=2, default=0)

    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'customers'
        verbose_name = 'عميل'
        verbose_name_plural = 'العملاء'
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
            last_customer = (
                Customer.objects.filter(tenant=self.tenant)
                .exclude(code='')
                .order_by('-id')
                .first()
            )
            next_number = 1
            if last_customer and last_customer.code.startswith('CUS-'):
                try:
                    next_number = int(last_customer.code.split('-')[-1]) + 1
                except ValueError:
                    next_number = Customer.objects.filter(tenant=self.tenant).count() + 1
            self.code = f"CUS-{next_number:05d}"

        super().save(*args, **kwargs)
