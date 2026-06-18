from django.urls import path
from . import views

app_name = 'items'

urlpatterns = [
    # ------- Items APIs -------
    path('api/table/', views.item_table_api, name='table_api'),
    path('api/create/', views.item_create_api, name='create_api'),
    path('api/<int:pk>/detail/', views.item_detail_api, name='detail_api'),
    path('api/<int:pk>/transactions/', views.item_transactions_api, name='transactions_api'),
    path('api/<int:pk>/update/', views.item_update_api, name='update_api'),
    path('api/<int:pk>/delete/', views.item_delete_api, name='delete_api'),
    path('api/search/', views.item_search_api, name='search_api'),

    # ------- Categories APIs -------
    path('categories/api/table/', views.category_table_api, name='category_table_api'),
    path('categories/api/options/', views.category_options_api, name='category_options_api'),
    path('categories/api/create/', views.category_create_api, name='category_create_api'),
    path('categories/api/<int:pk>/detail/', views.category_detail_api, name='category_detail_api'),
    path('categories/api/<int:pk>/update/', views.category_update_api, name='category_update_api'),
    path('categories/api/<int:pk>/delete/', views.category_delete_api, name='category_delete_api'),

    # ------- Units APIs -------
    path('units/api/table/', views.unit_table_api, name='unit_table_api'),
    path('units/api/options/', views.unit_options_api, name='unit_options_api'),
    path('units/api/create/', views.unit_create_api, name='unit_create_api'),
    path('units/api/<int:pk>/detail/', views.unit_detail_api, name='unit_detail_api'),
    path('units/api/<int:pk>/update/', views.unit_update_api, name='unit_update_api'),
    path('units/api/<int:pk>/delete/', views.unit_delete_api, name='unit_delete_api'),

    # ------- Items Import/Export -------
    path('api/export/', views.item_export_api, name='export_api'),
    path('api/import/', views.item_import_api, name='import_api'),
    path('api/template/', views.item_download_template, name='template_api'),

    # ------- Categories Import/Export -------
    path('categories/api/export/', views.category_export_api, name='category_export_api'),
    path('categories/api/import/', views.category_import_api, name='category_import_api'),
    path('categories/api/template/', views.category_download_template, name='category_template_api'),

    # ------- Pages -------
    path('', views.item_list, name='list'),
    path('categories/', views.category_list, name='category_list'),
    path('units/', views.unit_list, name='unit_list'),

    # ------- BOM Recipes -------
    path('bom/', views.bom_recipe_list, name='bom_list'),
    path('bom/api/', views.bom_recipe_api, name='bom_api'),
    path('bom/create/', views.bom_recipe_create_ajax, name='bom_create_ajax'),
    path('bom/<int:pk>/', views.bom_recipe_detail, name='bom_detail'),
    path('bom/<int:pk>/delete/', views.bom_recipe_delete_ajax, name='bom_delete'),

    # ------- Item Meta API -------
    path('api/meta/<int:pk>/', views.item_meta_api, name='item_meta_api'),

    # ------- Item Batches -------
    path('<int:pk>/batches/', views.item_batches, name='item_batches'),

]
