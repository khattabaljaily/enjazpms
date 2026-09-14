"""
اختبارات apps/employees/services.py و pay()/cancel() على EmployeeSalaryPayment
بعد الإصلاح الجوهري: كان الربط بين السلفة/الحافز وكشف الراتب (salary_payment
FK) لا يُضبط في أي مكان، فكانت advance_items/incentive_items تُصفَّى دائماً
فارغة — أي سلفة لم تكن تُعلَّم كمخصومة فعلياً رغم خصم مبلغها من الراتب
المصروف، ولا يُعاد ضبطها لو أُلغي الراتب. هذه الاختبارات تُثبت أن الربط
يحدث الآن فعلياً وأن pay()/cancel() متماثلان.
"""
from datetime import date
from decimal import Decimal

from apps.core.test_utils import TenantTestCase, make_employee, make_treasury
from apps.employees import services as employee_services
from apps.employees.models import EmployeeAdvance, EmployeeIncentive, EmployeeSalaryPayment


class CreateSalaryPaymentLinkingTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.treasury = make_treasury(self.tenant, current_balance='10000')
        self.employee = make_employee(self.tenant, base_salary='1000')

    def test_selected_advance_is_linked_and_deducted_amount_derived_server_side(self):
        advance = EmployeeAdvance.objects.create(
            tenant=self.tenant, employee=self.employee, amount=Decimal('200'),
            date=date(2026, 9, 1), payment_method='cash', treasury=self.treasury, status='pending',
        )
        sp = employee_services.create_salary_payment(
            tenant=self.tenant, employee=self.employee,
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            base_salary=Decimal('1000'), payment_method='cash', treasury=self.treasury,
            advance_ids=[advance.id], incentive_ids=[], user=self.user,
        )
        self.assertEqual(sp.advances_deducted, Decimal('200'))
        advance.refresh_from_db()
        self.assertEqual(advance.salary_payment_id, sp.id)
        self.assertEqual(advance.status, 'pending', 'لا يتغيّر إلى deducted إلا عند الدفع الفعلي')

    def test_selected_bonus_and_deduction_incentives_are_linked_and_summed(self):
        bonus = EmployeeIncentive.objects.create(
            tenant=self.tenant, employee=self.employee, type='bonus', amount=Decimal('50'),
            description='مكافأة', payout='with_salary', status='pending', date=date(2026, 9, 5),
        )
        deduction = EmployeeIncentive.objects.create(
            tenant=self.tenant, employee=self.employee, type='deduction', amount=Decimal('20'),
            description='خصم تأخير', payout='with_salary', status='pending', date=date(2026, 9, 10),
        )
        sp = employee_services.create_salary_payment(
            tenant=self.tenant, employee=self.employee,
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            base_salary=Decimal('1000'), payment_method='cash', treasury=self.treasury,
            advance_ids=[], incentive_ids=[bonus.id, deduction.id], user=self.user,
        )
        self.assertEqual(sp.bonus, Decimal('50'))
        self.assertEqual(sp.deductions, Decimal('20'))
        bonus.refresh_from_db()
        deduction.refresh_from_db()
        self.assertEqual(bonus.salary_payment_id, sp.id)
        self.assertEqual(deduction.salary_payment_id, sp.id)

    def test_stale_advance_id_is_rejected(self):
        with self.assertRaises(ValueError):
            employee_services.create_salary_payment(
                tenant=self.tenant, employee=self.employee,
                period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
                base_salary=Decimal('1000'), payment_method='cash', treasury=self.treasury,
                advance_ids=[999999], incentive_ids=[], user=self.user,
            )


class SalaryPaymentPayCancelSymmetryTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.treasury = make_treasury(self.tenant, current_balance='10000')
        self.employee = make_employee(self.tenant, base_salary='1000')
        self.advance = EmployeeAdvance.objects.create(
            tenant=self.tenant, employee=self.employee, amount=Decimal('200'),
            date=date(2026, 9, 1), payment_method='cash', treasury=self.treasury, status='pending',
        )
        self.bonus = EmployeeIncentive.objects.create(
            tenant=self.tenant, employee=self.employee, type='bonus', amount=Decimal('50'),
            description='مكافأة', payout='with_salary', status='pending', date=date(2026, 9, 5),
        )
        self.sp = employee_services.create_salary_payment(
            tenant=self.tenant, employee=self.employee,
            period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
            base_salary=Decimal('1000'), payment_method='cash', treasury=self.treasury,
            advance_ids=[self.advance.id], incentive_ids=[self.bonus.id], user=self.user,
        )

    def test_pay_marks_linked_advance_deducted_and_incentive_paid(self):
        self.sp.pay()
        self.advance.refresh_from_db()
        self.bonus.refresh_from_db()
        self.assertEqual(self.advance.status, 'deducted')
        self.assertEqual(self.bonus.status, 'paid')

        self.treasury.refresh_from_db()
        # total_due = 1000 + 50 (bonus) - 200 (advance) - 0 (deductions) = 850
        self.assertEqual(self.treasury.current_balance, Decimal('9150.00'))

    def test_cancel_reverts_advance_and_incentive_symmetrically(self):
        self.sp.pay()
        self.sp.cancel()

        self.advance.refresh_from_db()
        self.bonus.refresh_from_db()
        self.assertEqual(self.advance.status, 'pending', 'يجب أن تعود السلفة لحالة قائمة')
        self.assertIsNone(self.advance.salary_payment_id)
        self.assertEqual(self.bonus.status, 'pending', 'يجب أن يعود الحافز لحالة معلّق — كان هذا هو العطل المُصلَح')
        self.assertIsNone(self.bonus.salary_payment_id)

        self.treasury.refresh_from_db()
        self.assertEqual(self.treasury.current_balance, Decimal('10000.00'))
        self.sp.refresh_from_db()
        self.assertEqual(self.sp.status, 'cancelled')

    def test_pay_rejects_when_linked_advance_no_longer_pending(self):
        # يحاكي إلغاء السلفة يدوياً من مكان آخر بين إنشاء المسودة ودفعها.
        self.advance.status = 'cancelled'
        self.advance.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            self.sp.pay()
        self.sp.refresh_from_db()
        self.assertEqual(self.sp.status, 'draft')

    def test_advance_can_no_longer_be_double_deducted_by_a_second_payslip(self):
        # قبل الإصلاح: كانت السلفة لا تُعلَّم كمخصومة أبداً (العطل الأساسي)،
        # فكان بالإمكان اختيارها في أكثر من كشف راتب دون أي رفض. الآن تصبح
        # حالتها 'deducted' بعد الدفع، فاختيارها في كشف ثانٍ يُرفض صراحة بدل
        # تمريرها بصمت بقيمة صفر.
        self.sp.pay()
        self.advance.refresh_from_db()
        self.assertEqual(self.advance.status, 'deducted')
        with self.assertRaises(ValueError):
            employee_services.create_salary_payment(
                tenant=self.tenant, employee=self.employee,
                period_start=date(2026, 10, 1), period_end=date(2026, 10, 31),
                base_salary=Decimal('1000'), payment_method='cash', treasury=self.treasury,
                advance_ids=[self.advance.id], incentive_ids=[], user=self.user,
            )
