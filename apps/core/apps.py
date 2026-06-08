from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    verbose_name = 'Core - النواة'

    def ready(self):
        import apps.core.signals  # noqa: F401
        from apps.core.signals import connect_activity_signals
        connect_activity_signals()
        from apps.core.business_types_seed import sync_business_types

        def seed_business_types(sender, **kwargs):
            if sender.name != 'apps.core':
                return
            sync_business_types(verbose=False)

        post_migrate.connect(seed_business_types, dispatch_uid='apps.core.seed_business_types')
