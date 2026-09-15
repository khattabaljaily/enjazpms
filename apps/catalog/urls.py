from django.urls import path
from . import views

app_name = 'catalog'

urlpatterns = [
    path('import/', views.catalog_import_upload, name='import_upload'),
    path('import/<int:batch_id>/progress/', views.catalog_import_progress, name='import_progress'),
    path('import/<int:batch_id>/process-chunk/', views.catalog_import_process_chunk_api, name='import_process_chunk_api'),
    path('import/<int:batch_id>/review/', views.catalog_import_review, name='import_review'),
    path('import/<int:batch_id>/commit/', views.catalog_import_commit_api, name='import_commit_api'),

    path('search-api/', views.master_drug_search_api, name='master_drug_search_api'),

    path('clear-all/', views.catalog_clear_all, name='clear_all'),
    path('new/', views.master_drug_create, name='master_drug_create'),
    path('fix-missing-generic-names/', views.catalog_fix_missing_generic_names_api, name='fix_missing_generic_names_api'),

    path('', views.master_drug_list, name='master_drug_list'),
    path('<int:pk>/', views.master_drug_detail, name='master_drug_detail'),
    path('<int:pk>/delete/', views.master_drug_delete_api, name='master_drug_delete_api'),
    path('<int:pk>/aliases/<int:alias_id>/delete/', views.master_drug_alias_delete_api, name='master_drug_alias_delete_api'),
]
