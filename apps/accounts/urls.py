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
    path('api/login/', views.login_api, name='login_api'),

    # Registration
    path('register/step1/', views.register_step1, name='register_step1'),
    path('register/step2/', views.register_step2, name='register_step2'),
    path('register/step3/', views.register_step3, name='register_step3'),
    
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Profile
    path('profile/', views.profile_view, name='profile'),
]
