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

    path('statement/',  views.agent_statement, name='statement'),
    path('balances/',   views.agent_balances,  name='balances'),

    # ── Admin: manage agent invoice requests ──
    path('requests/',                   views.agent_requests_list,    name='requests_list'),
    path('requests/<int:pk>/',          views.agent_request_detail,   name='request_detail'),
    path('requests/<int:pk>/approve/',  views.agent_request_approve,  name='request_approve'),
    path('requests/<int:pk>/reject/',   views.agent_request_reject,   name='request_reject'),

    # ── Agent user creation / reset ──
    path('api/<int:pk>/create-user/',   views.agent_create_user_api,    name='create_user_api'),
    path('api/<int:pk>/reset-password/', views.agent_reset_password_api, name='reset_password_api'),

    # ── Agent Portal ──
    path('portal/login/',                       views.agent_portal_login,           name='portal_login'),
    path('portal/logout/',                      views.agent_portal_logout,          name='portal_logout'),
    path('portal/',                             views.agent_portal_dashboard,       name='portal_dashboard'),
    path('portal/invoices/',                    views.agent_portal_invoices,        name='portal_invoices'),
    path('portal/statement/',                   views.agent_portal_statement,       name='portal_statement'),
    path('portal/payments/',                    views.agent_portal_payments,        name='portal_payments'),
    path('portal/requests/',                    views.agent_portal_requests,        name='portal_requests'),
    path('portal/requests/new/',                views.agent_portal_request_new,     name='portal_request_new'),
    path('portal/requests/<int:pk>/',           views.agent_portal_request_detail,  name='portal_request_detail'),

    path('',        views.agent_list,   name='list'),
    path('create/', views.agent_create, name='create'),
]
