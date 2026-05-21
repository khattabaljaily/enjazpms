from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='stocks.StockTransfer')
def on_transfer_confirmed(sender, instance, **kwargs):
    if instance.status != 'confirmed':
        return
    from .models import Notification
    Notification.objects.get_or_create(
        tenant=instance.tenant,
        notification_type='transfer_done',
        link=f'/stocks/transfers/{instance.id}/',
        is_read=False,
        defaults={
            'title': f'اكتمل التحويل {instance.transfer_number}',
            'message': (
                f'تم تأكيد تحويل المخزون {instance.transfer_number} '
                f'من {instance.from_stock.name} إلى {instance.to_stock.name}.'
            ),
            'priority': 'low',
        },
    )


@receiver(post_save, sender='stocks.Stocktake')
def on_stocktake_confirmed(sender, instance, **kwargs):
    if instance.status != 'confirmed':
        return
    from .models import Notification
    Notification.objects.get_or_create(
        tenant=instance.tenant,
        notification_type='stocktake_done',
        link=f'/stocks/stocktakes/{instance.id}/',
        is_read=False,
        defaults={
            'title': f'اكتمل الجرد {instance.stocktake_number}',
            'message': f'تم تأكيد جرد مخزون {instance.stock.name}.',
            'priority': 'low',
        },
    )
