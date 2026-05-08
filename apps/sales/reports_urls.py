from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # ── Reports ──────────────────────────────────────────────
    path('', views.sales_summary_report, name='reports'),
    path('sales/summary/', views.sales_summary_report, name='summary_report'),
    path('sales/summary/export/', views.sales_summary_report_export, name='summary_report_export'),
    path('sales/by-customer/', views.sales_by_customer_report, name='by_customer_report'),
    path('sales/by-customer/export/', views.sales_by_customer_report_export, name='by_customer_report_export'),
    path('sales/by-item/', views.sales_by_item_report, name='by_item_report'),
    path('sales/by-item/export/', views.sales_by_item_report_export, name='by_item_report_export'),
    path('sales/by-date/', views.sales_by_date_report, name='by_date_report'),
    path('sales/by-date/export/', views.sales_by_date_report_export, name='by_date_report_export'),
]
