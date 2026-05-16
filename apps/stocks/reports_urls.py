from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # Summary Report
    path('summary/', views.stocks_summary_report, name='summary_report'),
    path('summary/export/', views.stocks_summary_report_export, name='summary_report_export'),

    # By Item Report
    path('by-item/', views.stocks_by_item_report, name='by_item_report'),
    path('by-item/export/', views.stocks_by_item_report_export, name='by_item_report_export'),

    # By Category Report
    path('by-category/', views.stocks_by_category_report, name='by_category_report'),
    path('by-category/export/', views.stocks_by_category_report_export, name='by_category_report_export'),

    # By Stock Location Report
    path('by-stock/', views.stocks_by_stock_report, name='by_stock_report'),
    path('by-stock/export/', views.stocks_by_stock_report_export, name='by_stock_report_export'),
]
