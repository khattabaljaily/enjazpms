from django.apps import AppConfig


class StocksConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.stocks'
    verbose_name = 'المخازن'

    def ready(self):
        # تسجيل الـ signals - يُستدعى مرة واحدة عند بدء Django
        import apps.stocks.signals  # noqa: F401
