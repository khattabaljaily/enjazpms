from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from apps.core.models import tenant_deletion_in_progress


@receiver(pre_delete, sender='treasury.Treasury')
def prevent_deleting_system_default_treasury(sender, instance, **kwargs):
    if tenant_deletion_in_progress.get():
        return
    if getattr(instance, 'is_system_default', False):
        raise ValidationError('لا يمكن حذف الخزينة الافتراضية النظامية.')
    if getattr(instance, 'is_hard_currency', False):
        raise ValidationError('لا يمكن حذف خزينة العملة الصعبة.')
