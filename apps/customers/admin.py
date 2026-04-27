from django.contrib import admin

from .models import Customer


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'tenant', 'phone', 'email', 'city', 'is_active', 'created_at']
    list_filter = ['tenant', 'is_active', 'city']
    search_fields = ['code', 'name', 'phone', 'email']
    readonly_fields = ['code', 'created_at', 'updated_at', 'created_by', 'updated_by']
    ordering = ['-created_at']
