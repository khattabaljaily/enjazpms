"""
Employees — Services

يجمع منطق الأعمال الذي كان مبعثراً مباشرة داخل apps/employees/views.py
(خلافاً لقاعدة "التعديلات المالية عبر services.py فقط" المتّبعة في بقية
التطبيقات) في مكان واحد قابل للاختبار بمعزل عن HTTP.

create_salary_payment هي الإصلاح الجوهري لعطل كان موجوداً فعلياً: كانت
EmployeeSalaryPayment.pay() تُصفّي advance_items/incentive_items (عبر
FK اسمه salary_payment) لتعليمها كمخصومة/مدفوعة، لكن ذلك الـ FK لم يكن
يُضبط في أي مكان في الكود — فكانت هذه الاستعلامات تُرجع دائماً قائمة
فارغة، ولم تكن أي سلفة أو حافز يُعلَّم فعلياً كمخصوم عند دفع الراتب،
رغم خصم مبلغها من صافي الراتب المصروف. الإصلاح: create_salary_payment
تربط صراحة السلف/الحوافز المُختارة (بمعرّفاتها) بكشف الراتب عند إنشائه،
وتشتق advances_deducted/bonus/deductions من مجموع تلك السجلات الفعلية
بدل رقم يُرسله المتصفح — يمنع أي احتمال تناقض بين ما يُعرض وما يُخصم.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.bank_accounts.services import post_bank_account_disbursement
from apps.treasury.services import post_treasury_disbursement


@transaction.atomic
def create_advance(tenant, employee, amount, date, payment_method,
                    treasury=None, bank_account=None, bank_reference='',
                    notes='', user=None):
    from .models import EmployeeAdvance

    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError('المبلغ يجب أن يكون أكبر من صفر')

    adv = EmployeeAdvance.objects.create(
        tenant=tenant, employee=employee, amount=amount,
        date=date or timezone.localdate(), payment_method=payment_method,
        treasury=treasury, bank_account=bank_account, bank_reference=bank_reference,
        notes=notes, created_by=user, updated_by=user,
    )

    if payment_method == 'cash' and treasury:
        mv = post_treasury_disbursement(
            tenant=tenant, amount=amount, date=adv.date,
            reference_type='employee_advance', reference_id=adv.pk,
            description=f'سلفة {employee.name}', user=user, treasury=treasury,
        )
        if mv:
            adv.treasury_movement = mv
            adv.save(update_fields=['treasury_movement', 'updated_at'])
    elif payment_method == 'bank' and bank_account:
        mv = post_bank_account_disbursement(
            tenant=tenant, amount=amount, date=adv.date,
            reference_type='employee_advance', reference_id=adv.pk,
            description=f'سلفة {employee.name}', user=user, bank_account=bank_account,
        )
        if mv:
            adv.bank_account_movement = mv
            adv.save(update_fields=['bank_account_movement', 'updated_at'])

    return adv


@transaction.atomic
def create_incentive(tenant, employee, amount, description, itype, payout,
                      payment_method, treasury=None, bank_account=None,
                      bank_reference='', date=None, notes='', user=None):
    from .models import EmployeeIncentive

    amount = Decimal(str(amount))
    if amount <= 0:
        raise ValueError('المبلغ يجب أن يكون أكبر من صفر')
    if not description:
        raise ValueError('الوصف مطلوب')
    if itype == 'deduction':
        payout = 'with_salary'

    inc = EmployeeIncentive.objects.create(
        tenant=tenant, employee=employee, type=itype, amount=amount,
        description=description, payout=payout, payment_method=payment_method,
        treasury=treasury, bank_account=bank_account, bank_reference=bank_reference,
        date=date or timezone.localdate(), notes=notes, created_by=user, updated_by=user,
    )

    if itype == 'bonus' and payout == 'immediate':
        inc.pay()  # نفس مسار "دفع حافز موجود" — لا تكرار لمنطق ترحيل الخزينة

    return inc


@transaction.atomic
def create_salary_payment(tenant, employee, period_start, period_end, base_salary,
                           payment_method, treasury=None, bank_account=None,
                           bank_reference='', notes='', deductions_notes='', advance_ids=None,
                           incentive_ids=None, user=None):
    from .models import EmployeeAdvance, EmployeeIncentive, EmployeeSalaryPayment

    advance_ids = list(advance_ids or [])
    incentive_ids = list(incentive_ids or [])

    advances = list(
        EmployeeAdvance.objects.select_for_update()
        .filter(tenant=tenant, employee=employee, status='pending', pk__in=advance_ids)
    )
    if len(advances) != len(set(advance_ids)):
        raise ValueError('إحدى السلف المختارة لم تعد قائمة (رُبطت بكشف آخر أو أُلغيت) — يرجى تحديث الصفحة والمحاولة مجدداً.')

    incentives = list(
        EmployeeIncentive.objects.select_for_update()
        .filter(tenant=tenant, employee=employee, status='pending', payout='with_salary', pk__in=incentive_ids)
    )
    if len(incentives) != len(set(incentive_ids)):
        raise ValueError('أحد الحوافز/الخصومات المختارة لم يعد قائماً — يرجى تحديث الصفحة والمحاولة مجدداً.')

    advances_deducted = sum((a.amount for a in advances), Decimal('0'))
    bonus = sum((i.amount for i in incentives if i.type == 'bonus'), Decimal('0'))
    deductions = sum((i.amount for i in incentives if i.type == 'deduction'), Decimal('0'))

    sp = EmployeeSalaryPayment.objects.create(
        tenant=tenant, employee=employee, period_start=period_start, period_end=period_end,
        base_salary=Decimal(str(base_salary)), bonus=bonus, advances_deducted=advances_deducted,
        deductions=deductions, deductions_notes=deductions_notes, payment_method=payment_method,
        treasury=treasury, bank_account=bank_account, bank_reference=bank_reference, notes=notes,
        created_by=user, updated_by=user,
    )

    if advances:
        EmployeeAdvance.objects.filter(pk__in=[a.pk for a in advances]).update(salary_payment=sp)
    if incentives:
        EmployeeIncentive.objects.filter(pk__in=[i.pk for i in incentives]).update(salary_payment=sp)

    return sp
