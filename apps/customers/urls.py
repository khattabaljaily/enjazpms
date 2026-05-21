from django.urls import path

from . import views

app_name = 'customers'

urlpatterns = [
    path('api/table/', views.customer_table_api, name='table_api'),
    path('api/create/', views.customer_create_api, name='create_api'),
    path('api/<int:pk>/detail/', views.customer_detail_api, name='detail_api'),
    path('api/<int:pk>/portal-token/', views.generate_portal_token, name='generate_portal_token'),
    path('api/<int:pk>/transactions/', views.customer_transactions_api, name='transactions_api'),
    path('api/<int:pk>/update/', views.customer_update_api, name='update_api'),
    path('api/<int:pk>/delete/', views.customer_delete_api, name='delete_api'),
    path('api/import/', views.customer_import_api, name='import_api'),
    path('api/export/', views.customer_export_api, name='export_api'),
    path('api/download-template/', views.download_template, name='download_template'),

    path('payments/', views.customer_payments, name='payments'),
    path('payments/api/', views.customer_payments_table_api, name='payments_api'),
    path('payments/create/', views.customer_payment_create_api, name='payments_create'),
    path('payments/<int:pk>/detail/', views.customer_payment_detail_api, name='payment_detail'),
    path('payments/<int:pk>/cancel/', views.customer_payment_cancel_api, name='payment_cancel'),

    path('', views.customer_list, name='list'),
    path('create/', views.customer_create, name='create'),
]
