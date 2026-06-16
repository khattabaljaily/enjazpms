from django.contrib import admin
from .models import Employee, EmployeeAdvance, EmployeeSalaryPayment, EmployeeIncentive

admin.site.register(Employee)
admin.site.register(EmployeeAdvance)
admin.site.register(EmployeeSalaryPayment)
admin.site.register(EmployeeIncentive)
