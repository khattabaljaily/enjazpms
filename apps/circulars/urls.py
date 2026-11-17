from django.urls import path

from . import views

app_name = 'circulars'

urlpatterns = [
    path('', views.circular_list, name='list'),
    path('read-all/', views.mark_all_read, name='mark_all_read'),
]
