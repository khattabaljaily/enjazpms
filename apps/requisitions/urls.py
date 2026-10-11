from django.urls import path

from . import views

app_name = 'requisitions'

urlpatterns = [
    path('', views.requisition_list, name='list'),
    path('create/', views.requisition_create, name='create'),
    path('api/items/', views.requisition_items_api, name='items_api'),
    path('<int:pk>/', views.requisition_detail, name='detail'),
    path('<int:pk>/edit/', views.requisition_edit, name='edit'),
    path('<int:pk>/submit/', views.requisition_submit, name='submit'),
    path('<int:pk>/cancel/', views.requisition_cancel, name='cancel'),
    path('<int:pk>/review/', views.requisition_review, name='review'),
]
