"""
Core URLs
"""
from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('subscription-expired/', views.subscription_expired, name='subscription_expired'),
    path('no-tenant/', views.no_tenant, name='no_tenant'),
    path('settings/tenant/', views.tenant_settings, name='tenant_settings'),
    path('subscription/', views.subscription_info, name='subscription'),
]
