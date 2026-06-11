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
    path('<int:pk>/delete-draft/', views.invoice_delete_draft_ajax, name='invoice_delete_draft'),
    path('<int:pk>/confirm/', views.invoice_confirm_ajax, name='invoice_confirm'),
    path('<int:pk>/deliver/', views.invoice_deliver_ajax, name='invoice_deliver'),
    path('<int:pk>/cancel/', views.invoice_cancel_ajax, name='invoice_cancel'),
    path('<int:pk>/pay/', views.record_payment_ajax, name='record_payment'),
    path('<int:pk>/send-email/', views.invoice_send_email_ajax, name='invoice_send_email'),

    # ── Returns ──────────────────────────────────────────────
    path('returns/', views.return_list, name='return_list'),
    path('returns/api/', views.return_table_api, name='return_api'),
    path('returns/<int:return_pk>/lines/', views.return_lines_api, name='return_lines_api'),
    path('<int:invoice_pk>/return/', views.return_create, name='return_create'),
    path('returns/<int:pk>/', views.return_detail, name='return_detail'),
    path('returns/<int:pk>/confirm/', views.return_confirm_ajax, name='return_confirm'),
    path('returns/<int:pk>/cancel/', views.return_cancel_ajax, name='return_cancel'),

    # ── Quotes (عروض الأسعار) ─────────────────────────────
    path('quotes/', views.quote_list, name='quote_list'),
    path('quotes/api/', views.quote_table_api, name='quote_api'),
    path('quotes/create/', views.quote_create, name='quote_create'),
    path('quotes/<int:pk>/', views.quote_detail, name='quote_detail'),
    path('quotes/<int:pk>/edit/', views.quote_edit, name='quote_edit'),
    path('quotes/<int:pk>/delete-draft/', views.quote_delete_draft_ajax, name='quote_delete_draft'),
    path('quotes/<int:pk>/send/', views.quote_send_ajax, name='quote_send'),
    path('quotes/<int:pk>/accept/', views.quote_accept_ajax, name='quote_accept'),
    path('quotes/<int:pk>/reject/', views.quote_reject_ajax, name='quote_reject'),
    path('quotes/<int:pk>/cancel/', views.quote_cancel_ajax, name='quote_cancel'),
    path('quotes/<int:pk>/convert/', views.quote_convert_ajax, name='quote_convert'),

    # ── AJAX Helpers ─────────────────────────────────────────
    path('api/item-info/', views.item_info_api, name='item_info_api'),
    path('api/customer-info/', views.customer_info_api, name='customer_info_api'),
    path('api/stock-items/', views.stock_items_api, name='stock_items_api'),

    # ── POS ──────────────────────────────────────────────────
    path('pos/', views.pos_view, name='pos'),
    path('pos/api/items/', views.pos_items_api, name='pos_items_api'),
    path('pos/api/checkout/', views.pos_checkout_api, name='pos_checkout_api'),
]
