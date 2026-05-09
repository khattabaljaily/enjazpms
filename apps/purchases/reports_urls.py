from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # ── Reports ──────────────────────────────────────────────
    path('', views.purchases_summary_report, name='reports'),
    path('purchases/summary/', views.purchases_summary_report, name='summary_report'),
    path('purchases/summary/export/', views.purchases_summary_report_export, name='summary_report_export'),
    path('purchases/by-supplier/', views.purchases_by_supplier_report, name='by_supplier_report'),
    path('purchases/by-supplier/export/', views.purchases_by_supplier_report_export, name='by_supplier_report_export'),
    path('purchases/by-item/', views.purchases_by_item_report, name='by_item_report'),
    path('purchases/by-item/export/', views.purchases_by_item_report_export, name='by_item_report_export'),
    path('purchases/by-date/', views.purchases_by_date_report, name='by_date_report'),
    path('purchases/by-date/export/', views.purchases_by_date_report_export, name='by_date_report_export'),
]