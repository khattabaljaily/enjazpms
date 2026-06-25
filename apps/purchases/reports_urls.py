from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.purchases_summary_report, name='reports'),

    # Summary
    path('summary/', views.purchases_summary_report, name='summary_report'),
    path('summary/export/', views.purchases_summary_report_export, name='summary_report_export'),

    # By Supplier
    path('by-supplier/', views.purchases_by_supplier_report, name='by_supplier_report'),
    path('by-supplier/export/', views.purchases_by_supplier_report_export, name='by_supplier_report_export'),

    # By Item
    path('by-item/', views.purchases_by_item_report, name='by_item_report'),
    path('by-item/export/', views.purchases_by_item_report_export, name='by_item_report_export'),

    # By Date
    path('by-date/', views.purchases_by_date_report, name='by_date_report'),
    path('by-date/export/', views.purchases_by_date_report_export, name='by_date_report_export'),

    # Supplier Statement
    path('supplier-statement/', views.purchases_supplier_statement, name='supplier_statement'),
    path('supplier-statement/export/', views.purchases_supplier_statement_export, name='supplier_statement_export'),

    # Supplier Balances (AP)
    path('supplier-balances/', views.purchases_supplier_balances, name='supplier_balances'),
    path('supplier-balances/export/', views.purchases_supplier_balances_export, name='supplier_balances_export'),

    # Payments
    path('payments/', views.purchases_payments_report, name='payments_report'),
    path('payments/export/', views.purchases_payments_report_export, name='payments_report_export'),

    # Returns
    path('returns/', views.purchases_returns_report, name='returns_report'),
    path('returns/export/', views.purchases_returns_report_export, name='returns_report_export'),

    # By User
    path('by-user/', views.purchases_by_user_report, name='by_user_report'),
    path('by-user/export/', views.purchases_by_user_report_export, name='by_user_report_export'),

    # Price History
    path('price-history/', views.purchases_price_history_report, name='price_history_report'),
    path('price-history/export/', views.purchases_price_history_report_export, name='price_history_report_export'),
]
