from django.urls import path
from . import views

app_name = 'expenses'

urlpatterns = [
    path('', views.expense_list, name='list'),
    path('table-api/', views.expense_table_api, name='table_api'),
    path('create/', views.expense_create, name='create'),
    path('<int:pk>/', views.expense_detail, name='detail'),
    path('<int:pk>/edit/', views.expense_edit, name='edit'),
    path('<int:pk>/confirm/', views.expense_confirm_ajax, name='confirm'),
    path('<int:pk>/cancel/', views.expense_cancel_ajax, name='cancel'),
    # Categories
    path('categories/api/', views.category_list_api, name='category_list_api'),
    path('categories/create/', views.category_create_api, name='category_create_api'),
]
