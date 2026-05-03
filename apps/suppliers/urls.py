from django.urls import path

from . import views

app_name = 'suppliers'

urlpatterns = [
    path('api/table/', views.supplier_table_api, name='table_api'),
    path('api/create/', views.supplier_create_api, name='create_api'),
    path('api/<int:pk>/detail/', views.supplier_detail_api, name='detail_api'),
    path('api/<int:pk>/transactions/', views.supplier_transactions_api, name='transactions_api'),
    path('api/<int:pk>/update/', views.supplier_update_api, name='update_api'),
    path('api/<int:pk>/delete/', views.supplier_delete_api, name='delete_api'),
    path('api/import/', views.supplier_import_api, name='import_api'),
    path('api/export/', views.supplier_export_api, name='export_api'),
    path('api/download-template/', views.download_template, name='download_template'),

    path('payments/', views.supplier_payments, name='payments'),
    path('payments/api/', views.supplier_payments_table_api, name='payments_api'),
    path('payments/create/', views.supplier_payment_create_api, name='payments_create'),
    path('payments/<int:pk>/detail/', views.supplier_payment_detail_api, name='payment_detail'),
    path('payments/<int:pk>/cancel/', views.supplier_payment_cancel_api, name='payment_cancel'),

    path('', views.supplier_list, name='list'),
    path('create/', views.supplier_create, name='create'),
]
