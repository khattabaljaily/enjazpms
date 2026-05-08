"""
Core Admin - لوحة التحكم للنماذج الأساسية
"""
from django.contrib import admin, messages
from django.db import transaction

from .models import BusinessType, Tenant, Settings, ActivityLog, tenant_deletion_in_progress


@admin.register(BusinessType)
class BusinessTypeAdmin(admin.ModelAdmin):
    list_display = ['name_ar', 'name', 'slug', 'icon', 'is_active', 'display_order']
    list_filter = ['is_active']
    search_fields = ['name', 'name_ar']
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ['is_active', 'display_order']
    ordering = ['display_order', 'name_ar']


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ['name', 'business_type', 'subscription_plan', 'is_active', 'created_at']
    list_filter = ['business_type', 'subscription_plan', 'is_active', 'version_type']
    search_fields = ['name', 'slug', 'email', 'phone']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['created_at', 'updated_at']
    actions = ['delete_selected_tenants']
    
    fieldsets = (
        ('معلومات أساسية', {
            'fields': ('name', 'slug', 'business_type', 'logo')
        }),
        ('معلومات الاتصال', {
            'fields': ('email', 'phone', 'address', 'city', 'country')
        }),
        ('الاشتراك', {
            'fields': ('subscription_plan', 'subscription_start', 'subscription_expires', 'is_active', 'is_demo')
        }),
        ('إعدادات النظام', {
            'fields': ('version_type', 'max_branches', 'max_stocks', 'max_users')
        }),
        ('التفضيلات', {
            'fields': ('timezone', 'language', 'currency')
        }),
        ('معلومات إضافية', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def delete_model(self, request, obj):
        token = tenant_deletion_in_progress.set(True)
        try:
            with transaction.atomic():
                obj.delete()
        finally:
            tenant_deletion_in_progress.reset(token)

    def delete_queryset(self, request, queryset):
        token = tenant_deletion_in_progress.set(True)
        try:
            with transaction.atomic():
                queryset.delete()
        finally:
            tenant_deletion_in_progress.reset(token)

    def delete_selected_tenants(self, request, queryset):
        count = queryset.count()
        token = tenant_deletion_in_progress.set(True)
        try:
            with transaction.atomic():
                queryset.delete()
        finally:
            tenant_deletion_in_progress.reset(token)

        self.message_user(
            request,
            f'تم حذف {count} نشاط تجاري وجميع البيانات المرتبطة به.',
            messages.SUCCESS
        )
    delete_selected_tenants.short_description = 'حذف النشاط التجاري وكل ما يتعلق به'


@admin.register(Settings)
class SettingsAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'tax_enabled', 'print_sale_invoice']
    list_filter = ['tax_enabled', 'print_sale_invoice', 'print_purchase_invoice']
    search_fields = ['tenant__name']
    
    fieldsets = (
        ('الفاتورة', {
            'fields': ('tenant', 'invoice_prefix', 'invoice_footer', 'print_sale_invoice', 'print_purchase_invoice')
        }),
        ('الضرائب', {
            'fields': ('tax_enabled', 'tax_value', 'tax_number')
        }),
        ('المخزون', {
            'fields': ('show_zero_stock', 'low_stock_alert')
        }),
        ('العرض', {
            'fields': ('items_per_page', 'date_format')
        }),
        ('الأمان', {
            'fields': ('delete_password',)
        }),
        ('الإشعارات', {
            'fields': ('email_notifications', 'sms_notifications')
        }),
    )


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['tenant', 'user', 'action', 'model_name', 'description', 'created_at']
    list_filter = ['action', 'tenant', 'created_at']
    search_fields = ['user__username', 'description', 'model_name']
    readonly_fields = ['tenant', 'user', 'action', 'model_name', 'object_id', 'description', 'ip_address', 'user_agent', 'metadata', 'created_at']
    date_hierarchy = 'created_at'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
