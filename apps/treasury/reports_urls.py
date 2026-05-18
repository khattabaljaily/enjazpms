from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # Treasury Balances
    path('balances/', views.treasury_balances_report, name='balances_report'),
    path('balances/export/', views.treasury_balances_report_export, name='balances_report_export'),

    # Treasury Statement
    path('statement/', views.treasury_statement_report, name='statement_report'),
    path('statement/export/', views.treasury_statement_report_export, name='statement_report_export'),

    # Treasury Movements
    path('movements/', views.treasury_movements_report, name='movements_report'),
    path('movements/export/', views.treasury_movements_report_export, name='movements_report_export'),
]
