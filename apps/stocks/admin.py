from django.contrib import admin
from .models import Stock


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'stock_type', 'tenant', 'is_default', 'is_active']
    list_filter = ['tenant', 'stock_type', 'is_active', 'is_default']
    search_fields = ['name', 'code']
    readonly_fields = ['created_at', 'updated_at']
