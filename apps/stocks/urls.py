from django.urls import path, include
from . import views

app_name = 'stocks'

urlpatterns = [
    # Reports
    path('reports/', include('apps.stocks.reports_urls')),

    # APIs
    path('api/table/', views.stock_table_api, name='table_api'),
    path('api/create/', views.stock_create_api, name='create_api'),
    path('api/<int:pk>/detail/', views.stock_detail_api, name='detail_api'),
    path('api/<int:pk>/update/', views.stock_update_api, name='update_api'),
    path('api/<int:pk>/delete/', views.stock_delete_api, name='delete_api'),
    path('api/<int:pk>/set-default/', views.stock_set_default_api, name='set_default_api'),
    path('api/opening/table/', views.opening_balance_table_api, name='opening_table_api'),
    path('api/opening/save/', views.opening_balance_save_api, name='opening_save_api'),
    path('api/quantities/table/', views.stock_quantities_table_api, name='quantities_table_api'),

    # Pages
    path('', views.stock_list, name='list'),
    path('opening-balances/', views.opening_balance_list, name='opening_balance_list'),
    path('quantities/', views.stock_quantities_list, name='quantities_list'),

    # Transfers
    path('transfers/', views.transfer_list, name='transfer_list'),
    path('transfers/api/', views.transfer_table_api, name='transfer_api'),
    path('transfers/create/', views.transfer_create, name='transfer_create'),
    path('transfers/<int:pk>/', views.transfer_detail, name='transfer_detail'),
    path('transfers/<int:pk>/confirm/', views.transfer_confirm_ajax, name='transfer_confirm'),
    path('transfers/<int:pk>/cancel/', views.transfer_cancel_ajax, name='transfer_cancel'),
    path('transfers/<int:pk>/delete/', views.transfer_delete_draft_ajax, name='transfer_delete'),
    path('transfers/api/items/', views.transfer_items_api, name='transfer_items_api'),

    # Stocktake
    path('stocktakes/', views.stocktake_list, name='stocktake_list'),
    path('stocktakes/api/', views.stocktake_table_api, name='stocktake_api'),
    path('stocktakes/create/', views.stocktake_create, name='stocktake_create'),
    path('stocktakes/<int:pk>/', views.stocktake_detail, name='stocktake_detail'),
    path('stocktakes/<int:pk>/save-counts/', views.stocktake_save_counts_ajax, name='stocktake_save_counts'),
    path('stocktakes/<int:pk>/confirm/', views.stocktake_confirm_ajax, name='stocktake_confirm'),
    path('stocktakes/<int:pk>/cancel/', views.stocktake_cancel_ajax, name='stocktake_cancel'),

    # Manufacturing Orders
    path('manufacturing/', views.manufacturing_list, name='manufacturing_list'),
    path('manufacturing/api/', views.manufacturing_table_api, name='manufacturing_api'),
    path('manufacturing/create/', views.manufacturing_create, name='manufacturing_create'),
    path('manufacturing/<int:pk>/', views.manufacturing_detail, name='manufacturing_detail'),
    path('manufacturing/<int:pk>/confirm/', views.manufacturing_confirm_ajax, name='manufacturing_confirm'),
    path('manufacturing/<int:pk>/cancel/', views.manufacturing_cancel_ajax, name='manufacturing_cancel'),
    path('manufacturing/<int:pk>/delete/', views.manufacturing_delete_ajax, name='manufacturing_delete'),
]
