from django.urls import path, include
from . import views

app_name = 'bank_accounts'

urlpatterns = [
    path('', views.bank_account_list, name='list'),
    path('api/table/', views.bank_account_table_api, name='table_api'),
    path('api/create/', views.bank_account_create_api, name='create_api'),
    path('api/<int:pk>/detail/', views.bank_account_detail_api, name='detail_api'),
    path('api/<int:pk>/transactions/', views.bank_account_transactions_api, name='transactions_api'),
    path('api/<int:pk>/update/', views.bank_account_update_api, name='update_api'),
    path('api/<int:pk>/delete/', views.bank_account_delete_api, name='delete_api'),
    path('api/transfer/', views.bank_account_transfer_api, name='transfer_api'),
    path('api/treasury-transfer/', views.treasury_bank_transfer_api, name='treasury_transfer_api'),
    # Reports
    path('reports/', include('apps.bank_accounts.reports_urls', namespace='reports')),
]
