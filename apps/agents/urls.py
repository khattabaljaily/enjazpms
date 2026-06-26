from django.urls import path
from . import views

app_name = 'agents'

urlpatterns = [
    path('api/table/',                     views.agent_table_api,          name='table_api'),
    path('api/create/',                    views.agent_create_api,         name='create_api'),
    path('api/<int:pk>/detail/',           views.agent_detail_api,         name='detail_api'),
    path('api/<int:pk>/transactions/',     views.agent_transactions_api,   name='transactions_api'),
    path('api/<int:pk>/update/',           views.agent_update_api,         name='update_api'),
    path('api/<int:pk>/delete/',           views.agent_delete_api,         name='delete_api'),
    path('api/import/',                    views.agent_import_api,         name='import_api'),
    path('api/export/',                    views.agent_export_api,         name='export_api'),
    path('api/download-template/',         views.download_template,        name='download_template'),

    path('payments/',                      views.agent_payments,               name='payments'),
    path('payments/api/',                  views.agent_payments_table_api,     name='payments_api'),
    path('payments/create/',              views.agent_payment_create_api,     name='payments_create'),
    path('payments/<int:pk>/detail/',     views.agent_payment_detail_api,     name='payment_detail'),
    path('payments/<int:pk>/cancel/',     views.agent_payment_cancel_api,     name='payment_cancel'),

    path('',        views.agent_list,   name='list'),
    path('create/', views.agent_create, name='create'),
]
