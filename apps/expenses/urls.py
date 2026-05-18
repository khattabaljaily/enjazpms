from django.urls import path, include
from . import views

app_name = 'expenses'

urlpatterns = [
    path('', views.expense_list, name='list'),
    path('table-api/', views.expense_table_api, name='table_api'),
    path('api/create/', views.expense_create, name='create_api'),
    path('api/<int:pk>/', views.expense_detail_api, name='detail_api'),
    path('api/<int:pk>/update/', views.expense_edit, name='edit_api'),
    path('<int:pk>/confirm/', views.expense_confirm_ajax, name='confirm'),
    path('<int:pk>/cancel/', views.expense_cancel_ajax, name='cancel'),
    # Categories
    path('categories/api/', views.category_list_api, name='category_list_api'),
    path('categories/create/', views.category_create_api, name='category_create_api'),
    # Reports
    path('reports/', include('apps.expenses.reports_urls', namespace='reports')),
]
