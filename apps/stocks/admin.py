from django.contrib import admin
from .models import Stock, StockQuantity


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'stock_type', 'tenant', 'is_default', 'is_active']
    list_filter = ['tenant', 'stock_type', 'is_active', 'is_default']
    search_fields = ['name', 'code']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(StockQuantity)
class StockQuantityAdmin(admin.ModelAdmin):
    list_display = ['item', 'stock', 'quantity', 'reserved_quantity', 'tenant']
    list_filter = ['tenant', 'stock']
    search_fields = ['item__name', 'stock__name']
    readonly_fields = ['created_at', 'updated_at']
