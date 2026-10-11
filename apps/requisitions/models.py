from decimal import Decimal

from django.db import models
from django.db.models import Sum

from apps.core.models import TenantMixin


class PurchaseRequisition(TenantMixin):
    """
    طلب شراء من فرع إلى الإدارة (النمط المركزي): الفرع لا يشتري ولا يتعامل مع الموردين،
    بل يطلب أصنافاً وكميات فقط، وتقرر الإدارة وتنفّذ بشحنة من المخزن المركزي أو بأمر شراء
    مركزي ثم شحنة.

    دورة الحياة:
      draft → submitted → approved (كلياً أو جزئياً) → fulfilled
                       ↘ rejected
      draft/submitted → cancelled (الفرع)   ·   approved → cancelled (الإدارة، بشرط ألا تُشحن كمية)
    """
    STATUS_CHOICES = (
        ('draft', 'مسودة'),
        ('submitted', 'مُرسَل للإدارة'),
        ('approved', 'معتمد'),
        ('rejected', 'مرفوض'),
        ('fulfilled', 'مكتمل التوريد'),
        ('cancelled', 'ملغى'),
    )
    PRIORITY_CHOICES = (
        ('normal', 'عادي'),
        ('urgent', 'عاجل'),
    )
    # حالات تُعدّ فيها الشحنات والمشتريات جارية على الطلب.
    OPEN_STATUSES = ('approved',)

    number = models.CharField('رقم الطلب', max_length=20, blank=True)
    branch = models.ForeignKey(
        'core.Branch', on_delete=models.PROTECT, related_name='purchase_requisitions', verbose_name='الفرع')
    stock = models.ForeignKey(
        'stocks.Stock', on_delete=models.PROTECT, related_name='purchase_requisitions',
        verbose_name='مخزن الفرع المستلِم')
    request_date = models.DateField('تاريخ الطلب')
    needed_by = models.DateField('مطلوب قبل', null=True, blank=True)
    priority = models.CharField('الأولوية', max_length=10, choices=PRIORITY_CHOICES, default='normal')
    status = models.CharField('الحالة', max_length=12, choices=STATUS_CHOICES, default='draft')
    notes = models.TextField('ملاحظات الفرع', blank=True)
    submitted_at = models.DateTimeField('وقت الإرسال', null=True, blank=True)
    reviewed_at = models.DateTimeField('وقت المراجعة', null=True, blank=True)
    reviewed_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_requisitions', verbose_name='راجعه')
    review_notes = models.TextField('ملاحظات الإدارة', blank=True)

    class Meta:
        db_table = 'purchase_requisitions'
        verbose_name = 'طلب شراء'
        verbose_name_plural = 'طلبات الشراء'
        ordering = ['-request_date', '-id']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'branch', 'status']),
        ]

    def __str__(self):
        return self.number

    def save(self, *args, **kwargs):
        if not self.number:
            last = (
                PurchaseRequisition.objects.filter(tenant=self.tenant, number__startswith='REQ-')
                .order_by('-id').first()
            )
            next_num = 1
            if last:
                try:
                    next_num = int(last.number.split('-')[-1]) + 1
                except ValueError:
                    pass
            self.number = f'REQ-{next_num:05d}'
        super().save(*args, **kwargs)

    STATUS_COLORS = {
        'draft': 'secondary', 'submitted': 'info', 'approved': 'primary',
        'rejected': 'danger', 'fulfilled': 'success', 'cancelled': 'dark',
    }

    @property
    def status_color(self):
        return self.STATUS_COLORS.get(self.status, 'secondary')


class PurchaseRequisitionLine(TenantMixin):
    requisition = models.ForeignKey(
        PurchaseRequisition, on_delete=models.CASCADE, related_name='lines', verbose_name='الطلب')
    item = models.ForeignKey(
        'items.Item', on_delete=models.PROTECT, related_name='requisition_lines', verbose_name='المنتج')
    requested_quantity = models.DecimalField('الكمية المطلوبة', max_digits=12, decimal_places=4)
    # تحدده الإدارة عند المراجعة (صفر = رفض هذا البند)؛ فارغ قبل المراجعة.
    approved_quantity = models.DecimalField(
        'الكمية المعتمدة', max_digits=12, decimal_places=4, null=True, blank=True)
    notes = models.CharField('ملاحظة', max_length=200, blank=True)

    class Meta:
        db_table = 'purchase_requisition_lines'
        verbose_name = 'بند طلب شراء'
        verbose_name_plural = 'بنود طلبات الشراء'
        ordering = ['id']

    def __str__(self):
        return f'{self.item.name} × {self.requested_quantity}'

    def _shipped(self, pending_statuses):
        """
        الكمية الخارجة على هذا البند: الشحنات في pending_statuses بكمية الإرسال، والمستلَمة بالكمية
        الفعلية التي أكدها الفرع (فالنقص الذي يعود للمركزي لا يُحسب توريداً).
        """
        lines = self.shipment_lines.filter(shipment__direction='to_branch')
        pending = lines.filter(shipment__status__in=pending_statuses).aggregate(t=Sum('quantity_sent'))['t']
        received = Decimal('0')
        for sl in lines.filter(shipment__status='received').only('quantity_sent', 'quantity_received'):
            received += sl.quantity_received if sl.quantity_received is not None else sl.quantity_sent
        return (pending or Decimal('0')) + received

    @property
    def fulfilled_quantity(self):
        """ما خرج فعلاً للفرع: في الطريق (كمية الإرسال) + مستلَم (الكمية المؤكَّدة)."""
        return self._shipped(('in_transit',))

    @property
    def committed_quantity(self):
        """ما أُنشئت له شحنة (مسودة أو أكثر) — يمنع تجاوز الكمية المعتمدة بمسودات متعددة."""
        return self._shipped(('draft', 'in_transit'))

    @property
    def remaining_quantity(self):
        """ما بقي من الكمية المعتمدة دون شحنة أُنشئت له."""
        approved = self.approved_quantity or Decimal('0')
        return max(approved - self.committed_quantity, Decimal('0'))
