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

    # By Category
    path('by-category/', views.expenses_by_category_report, name='by_category_report'),
    path('by-category/export/', views.expenses_by_category_report_export, name='by_category_report_export'),

    # By Date
    path('by-date/', views.expenses_by_date_report, name='by_date_report'),
    path('by-date/export/', views.expenses_by_date_report_export, name='by_date_report_export'),
]
