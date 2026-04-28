from django.urls import path
from . import views

app_name = 'sales'

urlpatterns = [
    # ── Invoice ──────────────────────────────────────────────
    path('', views.invoice_list, name='invoice_list'),
    path('api/', views.invoice_table_api, name='invoice_api'),
    path('create/', views.invoice_create, name='invoice_create'),
    path('<int:pk>/', views.invoice_detail, name='invoice_detail'),
    path('<int:pk>/edit/', views.invoice_edit, name='invoice_edit'),
    path('<int:pk>/confirm/', views.invoice_confirm_ajax, name='invoice_confirm'),
    path('<int:pk>/cancel/', views.invoice_cancel_ajax, name='invoice_cancel'),
    path('<int:pk>/pay/', views.record_payment_ajax, name='record_payment'),

    # ── Returns ──────────────────────────────────────────────
    path('returns/', views.return_list, name='return_list'),
    path('returns/api/', views.return_table_api, name='return_api'),
    path('<int:invoice_pk>/return/', views.return_create, name='return_create'),
    path('returns/<int:pk>/', views.return_detail, name='return_detail'),
    path('returns/<int:pk>/confirm/', views.return_confirm_ajax, name='return_confirm'),
    path('returns/<int:pk>/cancel/', views.return_cancel_ajax, name='return_cancel'),

    # ── AJAX Helpers ─────────────────────────────────────────
    path('api/item-info/', views.item_info_api, name='item_info_api'),
    path('api/customer-info/', views.customer_info_api, name='customer_info_api'),
    path('api/stock-items/', views.stock_items_api, name='stock_items_api'),
]
