from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.purchases_summary_report, name='reports'),

    # Summary
    path('purchases/summary/', views.purchases_summary_report, name='summary_report'),
    path('purchases/summary/export/', views.purchases_summary_report_export, name='summary_report_export'),

    # By Supplier
    path('purchases/by-supplier/', views.purchases_by_supplier_report, name='by_supplier_report'),
    path('purchases/by-supplier/export/', views.purchases_by_supplier_report_export, name='by_supplier_report_export'),

    # By Item
    path('purchases/by-item/', views.purchases_by_item_report, name='by_item_report'),
    path('purchases/by-item/export/', views.purchases_by_item_report_export, name='by_item_report_export'),

    # By Date
    path('purchases/by-date/', views.purchases_by_date_report, name='by_date_report'),
    path('purchases/by-date/export/', views.purchases_by_date_report_export, name='by_date_report_export'),

    # Supplier Statement
    path('purchases/supplier-statement/', views.purchases_supplier_statement, name='supplier_statement'),
    path('purchases/supplier-statement/export/', views.purchases_supplier_statement_export, name='supplier_statement_export'),

    # Supplier Balances (AP)
    path('purchases/supplier-balances/', views.purchases_supplier_balances, name='supplier_balances'),
    path('purchases/supplier-balances/export/', views.purchases_supplier_balances_export, name='supplier_balances_export'),

    # Payments
    path('purchases/payments/', views.purchases_payments_report, name='payments_report'),
    path('purchases/payments/export/', views.purchases_payments_report_export, name='payments_report_export'),

    # Returns
    path('purchases/returns/', views.purchases_returns_report, name='returns_report'),
    path('purchases/returns/export/', views.purchases_returns_report_export, name='returns_report_export'),
]
