from django.urls import path
from . import views

app_name = 'employees'

urlpatterns = [
    # ── Employees ────────────────────────────────────────────────────────────
    path('',                    views.employee_list,       name='list'),
    path('api/table/',          views.employee_table_api,  name='table_api'),
    path('api/create/',         views.employee_create,     name='create'),
    path('api/<int:pk>/',       views.employee_detail_api, name='detail_api'),
    path('api/<int:pk>/update/',views.employee_update,     name='update'),
    path('api/<int:pk>/delete/',views.employee_delete,     name='delete'),
    path('<int:pk>/statement/', views.employee_statement,  name='statement'),
    path('api/<int:pk>/pending-advances/', views.employee_pending_advances_api, name='pending_advances_api'),
    path('api/<int:pk>/pending-incentives/', views.employee_pending_incentives_api, name='pending_incentives_api'),

    # ── Advances ─────────────────────────────────────────────────────────────
    path('advances/',               views.advance_list,       name='advance_list'),
    path('advances/api/table/',     views.advance_table_api,  name='advance_table_api'),
    path('advances/api/create/',    views.advance_create,     name='advance_create'),
    path('advances/api/<int:pk>/cancel/', views.advance_cancel, name='advance_cancel'),

    # ── Salary Payments ───────────────────────────────────────────────────────
    path('salaries/',                   views.salary_list,        name='salary_list'),
    path('salaries/api/table/',         views.salary_table_api,   name='salary_table_api'),
    path('salaries/api/create/',        views.salary_create,      name='salary_create'),
    path('salaries/api/<int:pk>/',      views.salary_detail_api,  name='salary_detail_api'),
    path('salaries/api/<int:pk>/pay/',  views.salary_pay,         name='salary_pay'),
    path('salaries/api/<int:pk>/cancel/', views.salary_cancel,    name='salary_cancel'),

    # ── Incentives ────────────────────────────────────────────────────────────
    path('incentives/',                    views.incentive_list,       name='incentive_list'),
    path('incentives/api/table/',          views.incentive_table_api,  name='incentive_table_api'),
    path('incentives/api/create/',         views.incentive_create,     name='incentive_create'),
    path('incentives/api/<int:pk>/pay/',   views.incentive_pay,        name='incentive_pay'),
    path('incentives/api/<int:pk>/cancel/',views.incentive_cancel,     name='incentive_cancel'),
]
