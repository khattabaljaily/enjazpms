from django.db import models
from apps.core.models import TenantMixin


class Notification(TenantMixin):
    """
    إشعار داخلي للمستخدمين.
    يُنشأ تلقائياً عبر Signals أو يدوياً.
    """

    TYPE_CHOICES = (
        ('low_stock',        'مخزون منخفض'),
        ('overdue_invoice',  'فاتورة متأخرة'),
        ('rfq_expiry',       'انتهاء صلاحية طلب السعر'),
        ('transfer_done',    'اكتمال تحويل مخزون'),
        ('stocktake_done',   'اكتمال جرد مخزون'),
        ('online_order',     'طلب جديد من المتجر'),
        ('agent_request',    'طلب مندوب جديد'),
        ('general',          'إشعار عام'),
    )

    PRIORITY_CHOICES = (
        ('low',    'منخفضة'),
        ('medium', 'متوسطة'),
        ('high',   'عالية'),
    )

    notification_type = models.CharField('النوع', max_length=25, choices=TYPE_CHOICES, default='general')
    priority          = models.CharField('الأولوية', max_length=10, choices=PRIORITY_CHOICES, default='medium')
    title             = models.CharField('العنوان', max_length=200)
    message           = models.TextField('الرسالة')
    is_read           = models.BooleanField('مقروء', default=False)
    link              = models.CharField('الرابط', max_length=300, blank=True)

    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='notifications',
        verbose_name='المستخدم',
    )

    class Meta:
        db_table = 'notifications'
        verbose_name = 'إشعار'
        verbose_name_plural = 'الإشعارات'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'is_read', '-created_at']),
            models.Index(fields=['tenant', 'user', '-created_at']),
        ]

    def __str__(self):
        return self.title
