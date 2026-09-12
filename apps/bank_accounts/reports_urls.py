from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # Balances
    path('balances/', views.bank_account_balances_report, name='balances_report'),
    path('balances/export/', views.bank_account_balances_report_export, name='balances_report_export'),

    # Statement
    path('statement/', views.bank_account_statement_report, name='statement_report'),
    path('statement/export/', views.bank_account_statement_report_export, name='statement_report_export'),

    # Movements
    path('movements/', views.bank_account_movements_report, name='movements_report'),
    path('movements/export/', views.bank_account_movements_report_export, name='movements_report_export'),
]
