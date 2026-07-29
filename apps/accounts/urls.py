"""
URLs للحسابات
"""
from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # API endpoints (AJAX)
    path('api/register/step1/', views.register_step1_api, name='register_step1_api'),
    path('api/register/step2/', views.register_step2_api, name='register_step2_api'),
    path('api/register/step3/', views.register_step3_api, name='register_step3_api'),
    path('api/register/request/', views.registration_request_api, name='registration_request_api'),
    path('api/login/', views.login_api, name='login_api'),

    # Registration (register_step1 shows the closed/contact page instead of the form
    # when self-service registration is turned off from Admin Settings)
    path('register/step1/', views.register_step1, name='register_step1'),
    path('register/step2/', views.register_step2, name='register_step2'),
    path('register/step3/', views.register_step3, name='register_step3'),
    
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('api/login-as/<int:tenant_id>/', views.login_as_tenant_api, name='login_as_tenant_api'),
    path('exit-impersonation/', views.exit_impersonation, name='exit_impersonation'),
    
    # Password Reset
    path('password-reset/', views.password_reset_request, name='password_reset_request'),
    path('password-reset/done/', views.password_reset_complete, name='password_reset_complete'),
    path('password-reset/<uidb64>/<token>/', views.password_reset_confirm, name='password_reset_confirm'),
    
    # Profile
    path('profile/', views.profile_view, name='profile'),

    # User management
    path('users/', views.user_list, name='user_list'),
    path('users/api/table/', views.user_table_api, name='user_table_api'),
    path('users/api/create/', views.user_create_api, name='user_create_api'),
    path('users/api/<int:pk>/detail/', views.user_detail_api, name='user_detail_api'),
    path('users/api/<int:pk>/update/', views.user_update_api, name='user_update_api'),
    path('users/api/<int:pk>/delete/', views.user_delete_api, name='user_delete_api'),

    # Permission groups
    path('groups/', views.permission_group_list, name='permission_group_list'),
    path('groups/api/table/', views.permission_group_table_api, name='permission_group_table_api'),
    path('groups/api/schema/', views.permission_group_schema_api, name='permission_group_schema_api'),
    path('groups/api/create/', views.permission_group_create_api, name='permission_group_create_api'),
    path('groups/api/<int:pk>/detail/', views.permission_group_detail_api, name='permission_group_detail_api'),
    path('groups/api/<int:pk>/update/', views.permission_group_update_api, name='permission_group_update_api'),
    path('groups/api/<int:pk>/delete/', views.permission_group_delete_api, name='permission_group_delete_api'),

    # Activity log
    path('activity/', views.activity_log, name='activity_log'),
]
