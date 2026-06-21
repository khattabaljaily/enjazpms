from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.notification_list, name='list'),
    path('<int:pk>/', views.notification_detail, name='detail'),
    path('api/', views.notification_api, name='api'),
    path('api/<int:pk>/read/', views.mark_read_ajax, name='mark_read'),
    path('api/read-all/', views.mark_all_read_ajax, name='mark_all_read'),
    path('api/generate/', views.generate_notifications_ajax, name='generate'),
    path('api/<int:pk>/ai-analyze/', views.ai_analyze_notification, name='ai_analyze'),
]
