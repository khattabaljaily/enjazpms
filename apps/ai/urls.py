from django.urls import path
from . import views

app_name = 'ai'

urlpatterns = [
    path('chat/', views.chat_api, name='chat'),
    path('insights/', views.insights_api, name='insights'),
    path('advices/', views.advices_api, name='advices'),
    path('open/', views.track_open_api, name='track_open'),
]
