from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    path('', views.sales_summary_report, name='reports'),

    # Summary
    path('sales/summary/', views.sales_summary_report, name='summary_report'),
    path('sales/summary/export/', views.sales_summary_report_export, name='summary_report_export'),

    # By Customer
    path('sales/by-customer/', views.sales_by_customer_report, name='by_customer_report'),
    path('sales/by-customer/export/', views.sales_by_customer_report_export, name='by_customer_report_export'),

    # By Item
    path('sales/by-item/', views.sales_by_item_report, name='by_item_report'),
    path('sales/by-item/export/', views.sales_by_item_report_export, name='by_item_report_export'),

    # By Date
    path('sales/by-date/', views.sales_by_date_report, name='by_date_report'),
    path('sales/by-date/export/', views.sales_by_date_report_export, name='by_date_report_export'),

    # Customer Statement
    path('sales/customer-statement/', views.sales_customer_statement, name='customer_statement'),
    path('sales/customer-statement/export/', views.sales_customer_statement_export, name='customer_statement_export'),

    # Customer Balances (AR)
    path('sales/customer-balances/', views.sales_customer_balances, name='customer_balances'),
    path('sales/customer-balances/export/', views.sales_customer_balances_export, name='customer_balances_export'),

    # Payments
    path('sales/payments/', views.sales_payments_report, name='payments_report'),
    path('sales/payments/export/', views.sales_payments_report_export, name='payments_report_export'),

    # Returns
    path('sales/returns/', views.sales_returns_report, name='returns_report'),
    path('sales/returns/export/', views.sales_returns_report_export, name='returns_report_export'),

    # By User
    path('sales/by-user/', views.sales_by_user_report, name='by_user_report'),
    path('sales/by-user/export/', views.sales_by_user_report_export, name='by_user_report_export'),

    # Income Statement (P&L)
    path('accounting/income-statement/', views.income_statement_report, name='income_statement'),
    path('accounting/income-statement/export/', views.income_statement_report_export, name='income_statement_export'),

    # Profit Margin per Item
    path('sales/profit-margin/', views.sales_profit_margin_report, name='profit_margin_report'),
    path('sales/profit-margin/export/', views.sales_profit_margin_report_export, name='profit_margin_report_export'),

    # By Payment Method
    path('sales/by-payment-method/', views.sales_by_payment_method_report, name='by_payment_method_report'),
    path('sales/by-payment-method/export/', views.sales_by_payment_method_report_export, name='by_payment_method_report_export'),
]
