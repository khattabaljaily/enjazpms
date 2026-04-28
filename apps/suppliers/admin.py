from django.contrib import admin

from .models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'phone', 'city', 'opening_balance', 'is_active', 'created_at')
    list_filter = ('is_active', 'city', 'created_at')
    search_fields = ('code', 'name', 'phone', 'email')
    readonly_fields = ('code', 'created_at', 'updated_at', 'created_by', 'updated_by')
    
    fieldsets = (
        ('المعلومات الأساسية', {
            'fields': ('code', 'name', 'phone', 'email')
        }),
        ('العنوان', {
            'fields': ('city', 'address')
        }),
        ('المعلومات المالية', {
            'fields': ('opening_balance', 'credit_limit')
        }),
        ('إضافي', {
            'fields': ('notes', 'is_active')
        }),
        ('معلومات النظام', {
            'fields': ('created_at', 'updated_at', 'created_by', 'updated_by'),
            'classes': ('collapse',)
        }),
    )
