from django.urls import path
from . import views

app_name = 'data_import'

urlpatterns = [
    path('', views.hub, name='hub'),
    path('products/', views.product_import_page, name='product_page'),
    path('products/template/', views.product_template_download, name='product_template'),
    path('products/commit/', views.product_import_commit, name='product_commit'),

    path('customers/', views.customer_import_page, name='customer_page'),
    path('customers/template/', views.customer_template_download, name='customer_template'),
    path('customers/commit/', views.customer_import_commit, name='customer_commit'),

    path('suppliers/', views.supplier_import_page, name='supplier_page'),
    path('suppliers/template/', views.supplier_template_download, name='supplier_template'),
    path('suppliers/commit/', views.supplier_import_commit, name='supplier_commit'),
]
