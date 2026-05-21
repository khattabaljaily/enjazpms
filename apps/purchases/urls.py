from django.urls import path, include

from . import views

app_name = 'purchases'

urlpatterns = [
    path('', views.order_list, name='order_list'),
    path('api/', views.order_table_api, name='order_api'),
    path('create/', views.order_create, name='order_create'),
    path('<int:pk>/', views.order_detail, name='order_detail'),
    path('<int:pk>/print/', views.order_print, name='order_print'),
    path('<int:pk>/edit/', views.order_edit, name='order_edit'),
    path('<int:pk>/confirm/', views.order_confirm_ajax, name='order_confirm'),
    path('<int:pk>/cancel/', views.order_cancel_ajax, name='order_cancel'),

    path('returns/', views.return_list, name='return_list'),
    path('returns/api/', views.return_table_api, name='return_api'),
    path('returns/<int:return_pk>/lines/', views.return_lines_api, name='return_lines_api'),
    path('<int:invoice_pk>/return/', views.return_create, name='return_create'),
    path('returns/<int:pk>/', views.return_detail, name='return_detail'),
    path('returns/<int:pk>/confirm/', views.return_confirm_ajax, name='return_confirm'),
    path('returns/<int:pk>/cancel/', views.return_cancel_ajax, name='return_cancel'),

    # RFQ
    path('rfq/', views.rfq_list, name='rfq_list'),
    path('rfq/api/', views.rfq_table_api, name='rfq_api'),
    path('rfq/create/', views.rfq_create, name='rfq_create'),
    path('rfq/<int:pk>/', views.rfq_detail, name='rfq_detail'),
    path('rfq/<int:pk>/send/', views.rfq_send_ajax, name='rfq_send'),
    path('rfq/<int:pk>/receive/', views.rfq_receive_ajax, name='rfq_receive'),
    path('rfq/<int:pk>/accept/', views.rfq_accept_ajax, name='rfq_accept'),
    path('rfq/<int:pk>/reject/', views.rfq_reject_ajax, name='rfq_reject'),
    path('rfq/<int:pk>/cancel/', views.rfq_cancel_ajax, name='rfq_cancel'),
    path('rfq/<int:pk>/convert/', views.rfq_convert_ajax, name='rfq_convert'),

    # Reports
    path('reports/', include('apps.purchases.reports_urls')),
]
