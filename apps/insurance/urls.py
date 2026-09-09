from django.urls import path

from . import views

app_name = 'insurance'

urlpatterns = [
    # Insurance companies
    path('companies/', views.company_list, name='company_list'),
    path('companies/api/table/', views.company_table_api, name='company_table_api'),
    path('companies/api/create/', views.company_create_api, name='company_create_api'),
    path('companies/api/<int:pk>/detail/', views.company_detail_api, name='company_detail_api'),
    path('companies/api/<int:pk>/update/', views.company_update_api, name='company_update_api'),
    path('companies/api/<int:pk>/delete/', views.company_delete_api, name='company_delete_api'),

    # Customer insurance policies
    path('policies/api/customer/<int:customer_id>/', views.customer_policies_api, name='customer_policies_api'),
    path('policies/api/create/', views.policy_create_api, name='policy_create_api'),
    path('policies/api/<int:pk>/delete/', views.policy_delete_api, name='policy_delete_api'),

    # Claims
    path('claims/', views.claim_list, name='claim_list'),
    path('claims/api/table/', views.claim_table_api, name='claim_table_api'),
    path('claims/api/create/', views.claim_create_api, name='claim_create_api'),
    path('claims/api/<int:pk>/detail/', views.claim_detail_api, name='claim_detail_api'),
    path('claims/api/<int:pk>/submit/', views.claim_submit_api, name='claim_submit_api'),
    path('claims/api/<int:pk>/respond/', views.claim_respond_api, name='claim_respond_api'),
    path('claims/api/<int:pk>/settle/', views.claim_settle_api, name='claim_settle_api'),
    path('claims/api/<int:pk>/cancel/', views.claim_cancel_api, name='claim_cancel_api'),
    path('claims/api/invoice/<int:invoice_id>/eligible-lines/', views.invoice_eligible_lines_api, name='invoice_eligible_lines_api'),

    # Statement
    path('statement/', views.insurance_statement, name='statement'),

    # Reports
    path('claims/aging/', views.claims_aging_report, name='claims_aging'),
]
