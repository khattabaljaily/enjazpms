"""
Stocks Models - نماذج المخازن
يدعم 3 أنواع من الإعداد:
  - single_store:  مخزن واحد فقط
  - multi_stock:   عدة مخازن تحت محل واحد
  - multi_branch:  مخازن متعددة موزعة على فروع متعددة
"""
from django.db import models
from apps.core.models import TenantMixin


# ============================================
# STOCK (المخزن)
# ============================================

class Stock(TenantMixin):
    """
    المخزن الفيزيائي - المكان الذي تُحفظ فيه البضائع.

    - في single_store: يُنشأ تلقائياً مخزن واحد ولا يستطيع المستخدم إضافة آخر.
    - في multi_stock:  يستطيع المستخدم إضافة مخازن متعددة مرتبطة بنفس المحل.
    - في multi_branch: كل مخزن مرتبط بفرع (branch) معين.
    """

    TYPE_CHOICES = (
        ('main', 'مخزن رئيسي'),
        ('branch', 'مخزن فرع'),
        ('cold', 'مخزن مبرد'),
        ('hazardous', 'مخزن مواد خطرة'),
        ('transit', 'مخزن عبور / أمانات'),
    )

    # ------ معلومات أساسية ------
    name = models.CharField('اسم المخزن', max_length=200)
    code = models.CharField('الرمز', max_length=20, blank=True)
    stock_type = models.CharField(
        'نوع المخزن', max_length=20,
        choices=TYPE_CHOICES, default='main'
    )

    # ------ الفرع المرتبط (للنسخة multi_branch فقط) ------
    # سيتم ربطه بـ branches.Branch عند إنشاء تطبيق الفروع
    # branch = models.ForeignKey('branches.Branch', ...)
    branch_name = models.CharField(
        'اسم الفرع', max_length=200, blank=True,
        help_text='يُستخدم مؤقتاً حتى يتم إنشاء تطبيق الفروع'
    )

    # ------ تفاصيل إضافية ------
    address = models.TextField('العنوان / الموقع', blank=True)
    notes = models.TextField('ملاحظات', blank=True)
    is_active = models.BooleanField('نشط', default=True)
    is_default = models.BooleanField(
        'المخزن الافتراضي', default=False,
        help_text='المخزن الذي تُضاف إليه البضاعة تلقائياً عند الشراء'
    )

    class Meta:
        db_table = 'stocks'
        verbose_name = 'مخزن'
        verbose_name_plural = 'المخازن'
        ordering = ['-is_default', 'name']
        # كل tenant له رمز مخزن فريد
        unique_together = [('tenant', 'code')]
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['tenant', 'is_default']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_stock_type_display()})"

    def save(self, *args, **kwargs):
        # توليد رمز تلقائي إذا لم يُحدَّد
        if not self.code:
            last = (
                Stock.objects.filter(tenant=self.tenant)
                .exclude(code='')
                .order_by('-id')
                .first()
            )
            next_num = 1
            if last and last.code.startswith('WH-'):
                try:
                    next_num = int(last.code.split('-')[-1]) + 1
                except ValueError:
                    next_num = Stock.objects.filter(tenant=self.tenant).count() + 1
            self.code = f"WH-{next_num:03d}"

        # إذا كان هذا هو أول مخزن، اجعله افتراضياً
        if not self.pk and not Stock.objects.filter(tenant=self.tenant).exists():
            self.is_default = True

        super().save(*args, **kwargs)

    def can_add_stock(tenant):
        """
        هل يستطيع هذا الـ tenant إضافة مخزن جديد؟
        يُستخدم في الـ view للتحقق قبل السماح بالإضافة.
        """
        current_count = Stock.objects.filter(tenant=tenant, is_active=True).count()
        return current_count < tenant.max_stocks
