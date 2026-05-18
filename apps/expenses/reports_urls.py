from django.urls import path
from . import views

app_name = 'reports'

urlpatterns = [
    # Expenses Summary
    path('summary/', views.expenses_summary_report, name='summary_report'),
    path('summary/export/', views.expenses_summary_report_export, name='summary_report_export'),

    # Expenses Details
    path('details/', views.expenses_details_report, name='details_report'),
    path('details/export/', views.expenses_details_report_export, name='details_report_export'),
]
