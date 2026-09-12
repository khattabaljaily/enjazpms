from django.apps import AppConfig


class BankAccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.bank_accounts'
    verbose_name = 'الحسابات البنكية'

    def ready(self):
        import apps.bank_accounts.signals  # noqa: F401
