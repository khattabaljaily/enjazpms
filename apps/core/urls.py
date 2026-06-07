"""
Core URLs
"""
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('subscription-expired/', views.subscription_expired, name='subscription_expired'),
    path('no-tenant/', views.no_tenant, name='no_tenant'),
    path('no-permission/', views.no_permission, name='no_permission'),
    path('settings/tenant/', views.tenant_settings, name='tenant_settings'),
    path('settings/tenant/api/update/', views.tenant_settings_update_api, name='tenant_settings_update_api'),
    path('subscription/', views.subscription_info, name='subscription'),
    path('pricing/', views.pricing, name='pricing'),
    path('about/', views.about, name='about'),

    # Tenant management (superuser)
    path('tenants/', views.tenant_list, name='tenant_list'),
    path('tenants/api/table/', views.tenant_table_api, name='tenant_table_api'),
    path('tenants/api/create/', views.tenant_create_api, name='tenant_create_api'),
    path('tenants/api/<int:pk>/detail/', views.tenant_detail_api, name='tenant_detail_api'),
    path('tenants/api/<int:pk>/update/', views.tenant_update_api, name='tenant_update_api'),
    path('tenants/api/<int:pk>/delete/', views.tenant_delete_api, name='tenant_delete_api'),
    path('tenants/api/<int:pk>/suspend/', views.tenant_suspend_api, name='tenant_suspend_api'),

    # System — Users (superuser)
    path('system/users/', views.admin_users, name='admin_users'),
    path('system/users/create/', views.admin_user_create, name='admin_user_create'),

    # System — Support
    path('system/support/', views.admin_support, name='admin_support'),
    path('system/support/<int:pk>/', views.admin_support_detail, name='admin_support_detail'),

    # Tenant — Support
    path('support/', views.tenant_support, name='tenant_support'),
    path('support/new/', views.tenant_support_create, name='tenant_support_create'),
    path('support/<int:pk>/', views.tenant_support_detail, name='tenant_support_detail'),

    # System — Reports
    path('system/reports/subscriptions/', views.admin_report_subscriptions, name='admin_report_subscriptions'),
    path('system/reports/revenue/', views.admin_report_revenue, name='admin_report_revenue'),
    path('system/reports/activity/', views.admin_report_activity, name='admin_report_activity'),

    # System — Audit & Settings
    path('system/audit-log/', views.admin_audit_log, name='admin_audit_log'),
    path('system/settings/', views.admin_settings, name='admin_settings'),
    path('system/settings/api/update/', views.admin_settings_update_api, name='admin_settings_update_api'),

    # System — Backup
    path('system/backup/', views.admin_backup, name='admin_backup'),
    path('system/backup/<slug:tenant_slug>/', views.admin_backup_detail, name='admin_backup_detail'),
    path('system/backup/<slug:tenant_slug>/create/', views.admin_backup_create_api, name='admin_backup_create_api'),
    path('system/backup/restore/<int:backup_id>/', views.admin_backup_restore_api, name='admin_backup_restore_api'),
    path('system/backup/delete/<int:backup_id>/', views.admin_backup_delete_api, name='admin_backup_delete_api'),
    path('system/backup/download/<int:backup_id>/', views.admin_backup_download, name='admin_backup_download'),

    # System — Training
    path('system/training/', views.admin_training, name='admin_training'),
]
