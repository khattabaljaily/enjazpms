from decimal import Decimal

from django.test import TestCase

from apps.core.models import BusinessType, Tenant
from apps.employees.models import Employee, EmployeeIncentive, EmployeeSalaryPayment


class EmployeeSalaryPaymentTests(TestCase):
    def test_salary_collects_pending_with_salary_incentives(self):
        business_type = BusinessType.objects.create(
            name='retail',
            name_ar='متجر',
            slug='retail',
        )
        tenant = Tenant.objects.create(
            name='Test Tenant',
            slug='test-tenant',
            business_type=business_type,
        )
        employee = Employee.objects.create(
            tenant=tenant,
            name='Test Employee',
            salary_type='fixed',
            base_salary=Decimal('1000.00'),
        )
        incentive = EmployeeIncentive.objects.create(
            tenant=tenant,
            employee=employee,
            type='bonus',
            amount=Decimal('75.00'),
            description='مكافأة شهرية',
            payout='with_salary',
            status='pending',
            date='2026-06-15',
        )
        salary = EmployeeSalaryPayment.objects.create(
            tenant=tenant,
            employee=employee,
            period_start='2026-06-01',
            period_end='2026-06-30',
            base_salary=Decimal('1000.00'),
        )

        self.assertEqual(salary.get_pending_with_salary_incentives().count(), 1)
        self.assertEqual(
            salary.get_pending_with_salary_incentives().first(),
            incentive,
        )

    def test_salary_collects_paid_with_salary_incentives(self):
        business_type = BusinessType.objects.create(
            name='retail',
            name_ar='متجر',
            slug='retail',
        )
        tenant = Tenant.objects.create(
            name='Test Tenant',
            slug='test-tenant',
            business_type=business_type,
        )
        employee = Employee.objects.create(
            tenant=tenant,
            name='Test Employee',
            salary_type='fixed',
            base_salary=Decimal('1000.00'),
        )
        incentive = EmployeeIncentive.objects.create(
            tenant=tenant,
            employee=employee,
            type='deduction',
            amount=Decimal('40.00'),
            description='خصم بعد الدفع',
            payout='with_salary',
            status='paid',
            date='2026-06-20',
        )
        salary = EmployeeSalaryPayment.objects.create(
            tenant=tenant,
            employee=employee,
            period_start='2026-06-01',
            period_end='2026-06-30',
            base_salary=Decimal('1000.00'),
        )

        self.assertEqual(salary.get_pending_with_salary_incentives().count(), 1)
        self.assertEqual(
            salary.get_pending_with_salary_incentives().first(),
            incentive,
        )
