from django.contrib import admin

from .models import Treasury, TreasuryMovement


@admin.register(Treasury)
class TreasuryAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'current_balance', 'is_default', 'is_active', 'created_at')
    list_filter = ('tenant', 'is_default', 'is_active')
    search_fields = ('name', 'code')


@admin.register(TreasuryMovement)
class TreasuryMovementAdmin(admin.ModelAdmin):
    list_display = ('movement_date', 'treasury', 'movement_type', 'amount', 'reference_type', 'reference_id')
    list_filter = ('tenant', 'movement_type', 'treasury')
    search_fields = ('description', 'reference_type')
    readonly_fields = ('running_balance',)
