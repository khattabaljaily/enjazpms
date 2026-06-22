from django.urls import path, include
from . import views

app_name = 'treasury'

urlpatterns = [
    path('', views.treasury_list, name='list'),
    path('api/table/', views.treasury_table_api, name='table_api'),
    path('api/create/', views.treasury_create_api, name='create_api'),
    path('api/<int:pk>/detail/', views.treasury_detail_api, name='detail_api'),
    path('api/<int:pk>/transactions/', views.treasury_transactions_api, name='transactions_api'),
    path('api/<int:pk>/update/', views.treasury_update_api, name='update_api'),
    path('api/<int:pk>/delete/', views.treasury_delete_api, name='delete_api'),
    path('api/transfer/', views.treasury_transfer_api, name='transfer_api'),
    # Reports
    path('reports/', include('apps.treasury.reports_urls', namespace='reports')),
]
