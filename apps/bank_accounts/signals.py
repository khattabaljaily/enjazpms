from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from apps.core.models import tenant_deletion_in_progress


@receiver(pre_delete, sender='bank_accounts.BankAccount')
def prevent_deleting_bank_account_with_movements(sender, instance, **kwargs):
    if tenant_deletion_in_progress.get():
        return
    if instance.movements.exists():
        raise ValidationError('لا يمكن حذف حساب بنكي له حركات.')
