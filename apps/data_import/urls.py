from django.urls import path
from . import views

app_name = 'data_import'

urlpatterns = [
    path('', views.hub, name='hub'),
    path('products/', views.product_import_page, name='product_page'),
    path('products/template/', views.product_template_download, name='product_template'),
    path('products/commit/', views.product_import_commit, name='product_commit'),
]
