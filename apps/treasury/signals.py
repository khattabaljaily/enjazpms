from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete
from django.dispatch import receiver


@receiver(pre_delete, sender='treasury.Treasury')
def prevent_deleting_system_default_treasury(sender, instance, **kwargs):
    if getattr(instance, 'is_system_default', False):
        raise ValidationError('لا يمكن حذف الخزينة الافتراضية النظامية.')
