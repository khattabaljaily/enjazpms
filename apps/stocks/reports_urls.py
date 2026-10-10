from django.urls import path
from . import central_reports, views

app_name = 'reports'

urlpatterns = [
    # Summary
    path('summary/', views.stocks_summary_report, name='summary_report'),
    path('summary/export/', views.stocks_summary_report_export, name='summary_report_export'),

    # By Item
    path('by-item/', views.stocks_by_item_report, name='by_item_report'),
    path('by-item/export/', views.stocks_by_item_report_export, name='by_item_report_export'),

    # By Category
    path('by-category/', views.stocks_by_category_report, name='by_category_report'),
    path('by-category/export/', views.stocks_by_category_report_export, name='by_category_report_export'),

    # By Stock Location
    path('by-stock/', views.stocks_by_stock_report, name='by_stock_report'),
    path('by-stock/export/', views.stocks_by_stock_report_export, name='by_stock_report_export'),

    # Item Movement
    path('item-movement/', views.stocks_item_movement_report, name='item_movement_report'),
    path('item-movement/export/', views.stocks_item_movement_report_export, name='item_movement_report_export'),

    # Low Stock Alert
    path('low-stock/', views.stocks_low_stock_report, name='low_stock_report'),
    path('low-stock/export/', views.stocks_low_stock_report_export, name='low_stock_report_export'),

    # Inventory Valuation
    path('valuation/', views.stocks_valuation_report, name='valuation_report'),
    path('valuation/export/', views.stocks_valuation_report_export, name='valuation_report_export'),

    # Non-Moving Items
    path('non-moving/', views.stocks_non_moving_report, name='non_moving_report'),
    path('non-moving/export/', views.stocks_non_moving_report_export, name='non_moving_report_export'),

    # Central warehouse shipments (hybrid purchasing, head office only)
    path('shipments-in-transit/', central_reports.shipments_in_transit_report, name='shipments_in_transit_report'),
    path('shipments-in-transit/export/', central_reports.shipments_in_transit_report_export, name='shipments_in_transit_report_export'),
    path('shipment-differences/', central_reports.shipment_differences_report, name='shipment_differences_report'),
    path('shipment-differences/export/', central_reports.shipment_differences_report_export, name='shipment_differences_report_export'),

    # Controlled Substances Register (Pharmacy only)
    path('controlled-substances/', views.stocks_controlled_substances_report, name='controlled_substances_report'),
    path('controlled-substances/export/', views.stocks_controlled_substances_report_export, name='controlled_substances_report_export'),
]
