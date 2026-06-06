"""
Accounts Admin - لوحة التحكم للمستخدمين والصلاحيات
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, PermissionGroup


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'tenant', 'is_tenant_admin', 'is_active', 'date_joined']
    list_filter = ['tenant', 'is_tenant_admin', 'is_active', 'is_staff', 'is_superuser']
    search_fields = ['username', 'email', 'first_name', 'last_name', 'phone']
    
    fieldsets = (
        (None, {
            'fields': ('username', 'password')
        }),
        ('معلومات شخصية', {
            'fields': ('first_name', 'last_name', 'email', 'phone', 'avatar')
        }),
        ('المشترك والصلاحيات', {
            'fields': ('tenant', 'is_tenant_admin')
        }),
        ('صلاحيات النظام', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
            'classes': ('collapse',)
        }),
        ('تواريخ مهمة', {
            'fields': ('last_login', 'date_joined'),
            'classes': ('collapse',)
        }),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2', 'tenant', 'is_tenant_admin'),
        }),
    )
    
    readonly_fields = ['date_joined', 'last_login']
    ordering = ['-date_joined']


@admin.register(PermissionGroup)
class PermissionGroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'tenant', 'is_active', 'created_at']
    list_filter = ['tenant', 'is_active']
    search_fields = ['name', 'description']
    filter_horizontal = ['users']
    
    fieldsets = (
        ('معلومات أساسية', {
            'fields': ('tenant', 'name', 'description', 'is_active')
        }),
        ('الصلاحيات', {
            'fields': ('permissions',)
        }),
        ('المستخدمون', {
            'fields': ('users',)
        }),
    )
