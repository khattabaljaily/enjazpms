from django.urls import path

from . import views

app_name = 'customers'

urlpatterns = [
    path('api/table/', views.customer_table_api, name='table_api'),
    path('api/create/', views.customer_create_api, name='create_api'),
    path('api/<int:pk>/detail/', views.customer_detail_api, name='detail_api'),
    path('api/<int:pk>/update/', views.customer_update_api, name='update_api'),
    path('api/<int:pk>/delete/', views.customer_delete_api, name='delete_api'),

    path('', views.customer_list, name='list'),
    path('create/', views.customer_create, name='create'),
    path('<int:pk>/', views.customer_detail, name='detail'),
    path('<int:pk>/edit/', views.customer_update, name='edit'),
    path('<int:pk>/delete/', views.customer_delete, name='delete'),
]
