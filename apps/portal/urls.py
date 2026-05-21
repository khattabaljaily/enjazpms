from django.urls import path
from . import views

app_name = 'portal'

urlpatterns = [
    path('', views.portal_dashboard, name='dashboard'),
    path('invoices/', views.portal_invoices, name='invoices'),
    path('invoices/<int:pk>/', views.portal_invoice_detail, name='invoice_detail'),
    path('login/<uuid:token>/', views.login_via_token, name='login_via_token'),
    path('logout/', views.portal_logout, name='logout'),
    path('error/', views.portal_login_error, name='login_error'),
]
