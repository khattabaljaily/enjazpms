from django.contrib import admin
from .models import Category, Unit, Item


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'tenant', 'is_active', 'display_order']
    list_filter = ['tenant', 'is_active']
    search_fields = ['name']


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ['name', 'abbreviation', 'base_unit', 'conversion_factor', 'tenant', 'is_active']
    list_filter = ['tenant', 'is_active']
    search_fields = ['name', 'abbreviation']


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'sku', 'barcode', 'category', 'selling_price', 'tenant', 'is_active']
    list_filter = ['tenant', 'is_active', 'item_type', 'track_expiry', 'track_serial']
    search_fields = ['name', 'sku', 'barcode']
    readonly_fields = ['created_at', 'updated_at']
