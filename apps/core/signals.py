from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import Tenant, TenantCapabilities


@receiver(post_save, sender=Tenant)
def create_tenant_defaults(sender, instance, created, **kwargs):
    if not created:
        return

    from apps.stocks.models import Stock
    from apps.treasury.models import Treasury

    # Default stock (non-deletable system stock)
    Stock.objects.get_or_create(
        tenant=instance,
        is_system_default=True,
        defaults={
            'name': 'المخزن الرئيسي',
            'code': 'WH-MAIN',
            'stock_type': 'main',
            'is_default': True,
            'is_active': True,
        },
    )

    # Create additional stocks if max_stocks > 1
    if instance.max_stocks > 1:
        for i in range(2, instance.max_stocks + 1):
            Stock.objects.get_or_create(
                tenant=instance,
                code=f'WH-{i:03d}',
                defaults={
                    'name': f'مخزن {i}',
                    'stock_type': 'main',
                    'is_active': True,
                },
            )

    # Ensure at least one default stock flag exists
    if not Stock.objects.for_tenant(instance).filter(is_default=True).exists():
        fallback_stock = Stock.objects.for_tenant(instance).order_by('id').first()
        if fallback_stock:
            fallback_stock.is_default = True
            fallback_stock.save(update_fields=['is_default', 'updated_at'])

    # Default treasury (non-deletable system treasury)
    Treasury.objects.get_or_create(
        tenant=instance,
        is_system_default=True,
        defaults={
            'name': 'الخزينة الرئيسية',
            'code': 'TR-MAIN',
            'is_default': True,
            'is_active': True,
            'current_balance': 0,
        },
    )

    if not Treasury.objects.for_tenant(instance).filter(is_default=True).exists():
        fallback_treasury = Treasury.objects.for_tenant(instance).order_by('id').first()
        if fallback_treasury:
            fallback_treasury.is_default = True
            fallback_treasury.save(update_fields=['is_default', 'updated_at'])

    # Capabilities derived from business type
    caps = TenantCapabilities.from_business_type(instance)
    caps.save()
