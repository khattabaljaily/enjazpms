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

    # Admin — Users
    path('admin/users/', views.admin_users, name='admin_users'),
    path('admin/users/create/', views.admin_user_create, name='admin_user_create'),

    # Admin — Support
    path('admin/support/', views.admin_support, name='admin_support'),

    # Admin — Reports
    path('admin/reports/subscriptions/', views.admin_report_subscriptions, name='admin_report_subscriptions'),
    path('admin/reports/revenue/', views.admin_report_revenue, name='admin_report_revenue'),
    path('admin/reports/activity/', views.admin_report_activity, name='admin_report_activity'),

    # Admin — System
    path('admin/audit-log/', views.admin_audit_log, name='admin_audit_log'),
    path('admin/settings/', views.admin_settings, name='admin_settings'),

    # Admin — Backup & Training
    path('admin/backup/', views.admin_backup, name='admin_backup'),
    path('admin/training/', views.admin_training, name='admin_training'),
]
