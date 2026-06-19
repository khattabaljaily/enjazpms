from django.db import models

from apps.core.models import TenantMixin


class Supplier(TenantMixin):
    code = models.CharField('كود المورد', max_length=20, blank=True)
    name = models.CharField('اسم المورد', max_length=200)
    phone = models.CharField('رقم الهاتف', max_length=20, blank=True)
    email = models.EmailField('البريد الإلكتروني', blank=True)
    city = models.CharField('المدينة', max_length=100, blank=True)
    address = models.TextField('العنوان', blank=True)
    notes = models.TextField('ملاحظات', blank=True)

    opening_balance = models.DecimalField('المديونية الافتتاحية', max_digits=12, decimal_places=2, default=0)
    credit_limit = models.DecimalField('الحد الائتماني', max_digits=12, decimal_places=2, default=0)

    is_active = models.BooleanField('نشط', default=True)

    class Meta:
        db_table = 'suppliers'
        verbose_name = 'مورد'
        verbose_name_plural = 'الموردين'
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
            last_supplier = (
                Supplier.objects.filter(tenant=self.tenant)
                .exclude(code='')
                .order_by('-id')
                .first()
            )
            next_number = 1
            if last_supplier and last_supplier.code.startswith('SUP-'):
                try:
                    next_number = int(last_supplier.code.split('-')[-1]) + 1
                except ValueError:
                    next_number = Supplier.objects.filter(tenant=self.tenant).count() + 1
            self.code = f"SUP-{next_number:05d}"
        super().save(*args, **kwargs)
