from django.urls import path
from . import views

app_name = 'stocks'

urlpatterns = [
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
]
