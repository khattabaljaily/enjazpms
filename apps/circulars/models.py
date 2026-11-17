from django.conf import settings
from django.db import models

from apps.core.models import TenantMixin


class Circular(TenantMixin):
    """تعميم نظام: يُنشأ تلقائيًا عند أي إجراء حساس، ويراه كل مستخدمي النشاط (مدير النشاط أيضًا).

    إلحاق فقط: لا تعديل ولا حذف من الواجهة، ويتزامن مع نسخة الأوفلاين.
    """

    LEVEL_RED = 'red'
    LEVEL_YELLOW = 'yellow'
    LEVEL_CHOICES = (
        (LEVEL_RED, 'خطير'),
        (LEVEL_YELLOW, 'يستحق الانتباه'),
    )

    branch = models.ForeignKey(
        'core.Branch', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='circulars', verbose_name='الفرع',
        help_text='فارغ = تعميم عام يراه كل الفروع',
    )
    level = models.CharField('المستوى', max_length=10, choices=LEVEL_CHOICES, default=LEVEL_RED)
    event_key = models.CharField('نوع الحدث', max_length=60, db_index=True)
    title = models.CharField('العنوان', max_length=200)
    message = models.TextField('النص')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='circulars_made', verbose_name='المنفّذ',
    )
    actor_name = models.CharField('اسم المنفّذ', max_length=150, blank=True)
    object_label = models.CharField('العنصر المتأثر', max_length=200, blank=True)
    link = models.CharField('الرابط', max_length=300, blank=True)
    before = models.JSONField('قبل', default=dict, blank=True)
    after = models.JSONField('بعد', default=dict, blank=True)

    class Meta:
        db_table = 'circulars'
        verbose_name = 'تعميم'
        verbose_name_plural = 'التعاميم'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['tenant', '-created_at']),
            models.Index(fields=['tenant', 'level', '-created_at']),
        ]

    def __str__(self):
        return self.title


class CircularRead(models.Model):
    """حالة القراءة لكل مستخدم (محلية لكل جهاز، لا تُزامَن)."""

    tenant = models.ForeignKey('core.Tenant', on_delete=models.CASCADE)
    circular = models.ForeignKey(Circular, on_delete=models.CASCADE, related_name='reads')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='circular_reads')
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'circular_reads'
        unique_together = [('circular', 'user')]
