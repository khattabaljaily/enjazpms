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

    # Pages
    path('', views.stock_list, name='list'),
]
