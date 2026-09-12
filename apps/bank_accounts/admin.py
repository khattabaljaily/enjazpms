from django.contrib import admin

from .models import BankAccount, BankAccountMovement, BankAccountTransfer, TreasuryBankTransfer


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'bank_name', 'tenant', 'current_balance', 'is_default', 'is_active', 'created_at')
    list_filter = ('tenant', 'is_default', 'is_active')
    search_fields = ('name', 'bank_name', 'account_number', 'iban')


@admin.register(BankAccountMovement)
class BankAccountMovementAdmin(admin.ModelAdmin):
    list_display = ('movement_date', 'bank_account', 'movement_type', 'amount', 'reference_type', 'reference_id')
    list_filter = ('tenant', 'movement_type', 'bank_account')
    search_fields = ('description', 'reference_type')
    readonly_fields = ('running_balance',)


@admin.register(BankAccountTransfer)
class BankAccountTransferAdmin(admin.ModelAdmin):
    list_display = ('transfer_date', 'from_bank_account', 'to_bank_account', 'from_amount', 'to_amount')
    list_filter = ('tenant',)


@admin.register(TreasuryBankTransfer)
class TreasuryBankTransferAdmin(admin.ModelAdmin):
    list_display = ('transfer_date', 'direction', 'treasury', 'bank_account', 'amount')
    list_filter = ('tenant', 'direction')
