"""
Employees Views — الموظفون
"""
import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.activity_service import log_activity
from apps.accounts.decorators import require_permission, branch_scope_exempt
from apps.core.utils import convert_arabic_numerals, filter_by_branch_via, enforce_branch_ownership
from apps.treasury.models import Treasury
from apps.bank_accounts.models import BankAccount

from .models import Employee, EmployeeAdvance, EmployeeIncentive, EmployeeSalaryPayment


def _tenant(request):
    return getattr(request, 'tenant', None)


def _err(msg, status=400):
    return JsonResponse({'success': False, 'message': msg}, status=status)


def _dec(val):
    try:
        return Decimal(str(convert_arabic_numerals(str(val or '0').strip()) or '0'))
    except (InvalidOperation, ValueError):
        return Decimal('0')


# ─────────────────────────────────────────────────────────────────────────────
# Employees
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_employees')
def employee_list(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Employee.objects.filter(tenant=tenant).for_branch(getattr(request, 'branch', None))
    context = {
        'stats': {
            'total': qs.count(),
            'active': qs.filter(is_active=True).count(),
            'inactive': qs.filter(is_active=False).count(),
        },
        'treasuries': Treasury.objects.for_tenant(tenant).filter(is_active=True, is_hard_currency=False),
    }
    return render(request, 'employees/employee_list.html', context)


@login_required
@require_permission('view_employees')
def employee_table_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    draw   = int(request.GET.get('draw', 1))
    start  = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    qs = Employee.objects.filter(tenant=tenant).for_branch(getattr(request, 'branch', None))
    records_total = qs.count()

    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)

    if search:
        qs = qs.filter(
            Q(name__icontains=search) |
            Q(employee_id__icontains=search) |
            Q(position__icontains=search) |
            Q(department__icontains=search) |
            Q(phone__icontains=search)
        )

    records_filtered = qs.count()
    qs = qs[start: start + length]

    rows = []
    for emp in qs:
        rows.append({
            'id': emp.pk,
            'employee_id': emp.employee_id,
            'name': emp.name,
            'position': emp.position or '—',
            'department': emp.department or '—',
            'phone': emp.phone or '—',
            'base_salary': str(emp.base_salary),
            'salary_type': emp.get_salary_type_display(),
            'is_active': emp.is_active,
            'hire_date': str(emp.hire_date) if emp.hire_date else '—',
            'pending_advances': str(emp.pending_advances_total),
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': rows,
        'perms': {
            'edit': request.user.has_perm_key('edit_employees'),
            'delete': request.user.has_perm_key('delete_employees'),
        },
    })


@login_required
@require_permission('add_employees')
@require_POST
@branch_scope_exempt('ينشئ موظفاً جديداً يُختم بفرع المنشئ تلقائياً (branch=getattr(request, "branch", None)) — لا قراءة لبيانات فرع آخر')
def employee_create(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')
    try:
        data = json.loads(request.body)
    except ValueError:
        return _err('طلب غير صالح')

    name = (data.get('name') or '').strip()
    if not name:
        return _err('اسم الموظف مطلوب')

    emp = Employee.objects.create(
        tenant=tenant,
        branch=getattr(request, 'branch', None),
        name=name,
        phone=(data.get('phone') or '').strip(),
        position=(data.get('position') or '').strip(),
        department=(data.get('department') or '').strip(),
        salary_type=data.get('salary_type', 'fixed'),
        base_salary=_dec(data.get('base_salary', 0)),
        hire_date=data.get('hire_date') or None,
        notes=(data.get('notes') or '').strip(),
        created_by=request.user,
        updated_by=request.user,
    )
    log_activity(request, 'create', f'إضافة موظف جديد: {emp.name}')
    return JsonResponse({'success': True, 'id': emp.pk, 'employee_id': emp.employee_id, 'name': emp.name})


@login_required
@require_permission('edit_employees')
@require_POST
def employee_update(request, pk):
    tenant = _tenant(request)
    emp = get_object_or_404(Employee, pk=pk, tenant=tenant)
    enforce_branch_ownership(request, emp)
    try:
        data = json.loads(request.body)
    except ValueError:
        return _err('طلب غير صالح')

    name = (data.get('name') or '').strip()
    if not name:
        return _err('اسم الموظف مطلوب')

    emp.name        = name
    emp.phone       = (data.get('phone') or '').strip()
    emp.position    = (data.get('position') or '').strip()
    emp.department  = (data.get('department') or '').strip()
    emp.salary_type = data.get('salary_type', emp.salary_type)
    emp.base_salary = _dec(data.get('base_salary', emp.base_salary))
    emp.hire_date   = data.get('hire_date') or None
    emp.is_active   = bool(data.get('is_active', emp.is_active))
    emp.notes       = (data.get('notes') or '').strip()
    emp.updated_by  = request.user
    emp.save()
    log_activity(request, 'update', f'تعديل موظف: {emp.name}')
    return JsonResponse({'success': True})


@login_required
@require_permission('view_employees')
def employee_detail_api(request, pk):
    tenant = _tenant(request)
    emp = get_object_or_404(Employee, pk=pk, tenant=tenant)
    enforce_branch_ownership(request, emp)
    return JsonResponse({
        'id': emp.pk,
        'employee_id': emp.employee_id,
        'name': emp.name,
        'phone': emp.phone,
        'position': emp.position,
        'department': emp.department,
        'salary_type': emp.salary_type,
        'base_salary': str(emp.base_salary),
        'hire_date': str(emp.hire_date) if emp.hire_date else '',
        'is_active': emp.is_active,
        'notes': emp.notes,
    })


@login_required
@require_permission('delete_employees')
@require_POST
def employee_delete(request, pk):
    tenant = _tenant(request)
    emp = get_object_or_404(Employee, pk=pk, tenant=tenant)
    enforce_branch_ownership(request, emp)
    name = emp.name
    emp.delete()
    log_activity(request, 'delete', f'حذف موظف: {name}')
    return JsonResponse({'success': True})


# ─────────────────────────────────────────────────────────────────────────────
# Employee Statement
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_employees')
def employee_statement(request, pk):
    tenant = _tenant(request)
    emp = get_object_or_404(Employee, pk=pk, tenant=tenant)
    enforce_branch_ownership(request, emp)

    salaries   = emp.salary_payments.all().select_related('treasury')
    advances   = emp.advances.all().select_related('treasury')
    incentives = emp.incentives.all().select_related('treasury')

    total_paid    = salaries.filter(status='paid').aggregate(t=Sum('base_salary'))['t'] or Decimal('0')
    total_bonus   = salaries.filter(status='paid').aggregate(t=Sum('bonus'))['t'] or Decimal('0')
    total_deduct  = salaries.filter(status='paid').aggregate(t=Sum('deductions'))['t'] or Decimal('0')
    total_adv     = advances.filter(status__in=['pending', 'deducted']).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    context = {
        'employee':   emp,
        'salaries':   salaries[:50],
        'advances':   advances[:50],
        'incentives': incentives[:50],
        'total_paid':   total_paid,
        'total_bonus':  total_bonus,
        'total_deduct': total_deduct,
        'total_adv':    total_adv,
    }
    return render(request, 'employees/employee_statement.html', context)


# ─────────────────────────────────────────────────────────────────────────────
# Advances
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_employee_advances')
def advance_list(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = filter_by_branch_via(EmployeeAdvance.objects.filter(tenant=tenant), getattr(request, 'branch', None), field='employee__branch')
    context = {
        'stats': {
            'total':     qs.count(),
            'pending':   qs.filter(status='pending').count(),
            'deducted':  qs.filter(status='deducted').count(),
            'cancelled': qs.filter(status='cancelled').count(),
        },
        'employees':  Employee.objects.filter(tenant=tenant, is_active=True).for_branch(getattr(request, 'branch', None)).order_by('name'),
        'treasuries': Treasury.objects.for_tenant(tenant).filter(is_active=True, is_hard_currency=False),
        'bank_accounts': BankAccount.objects.for_tenant(tenant).filter(is_active=True),
    }
    return render(request, 'employees/advance_list.html', context)


@login_required
@require_permission('view_employee_advances')
def advance_table_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    draw   = int(request.GET.get('draw', 1))
    start  = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()
    emp_id = request.GET.get('employee', '').strip()

    qs = EmployeeAdvance.objects.filter(tenant=tenant).select_related('employee', 'treasury', 'bank_account')
    qs = filter_by_branch_via(qs, getattr(request, 'branch', None), field='employee__branch')
    records_total = qs.count()

    if status:
        qs = qs.filter(status=status)
    if emp_id:
        qs = qs.filter(employee_id=emp_id)
    if search:
        qs = qs.filter(Q(employee__name__icontains=search) | Q(notes__icontains=search))

    records_filtered = qs.count()
    qs = qs[start: start + length]

    rows = []
    for adv in qs:
        rows.append({
            'id': adv.pk,
            'employee': adv.employee.name,
            'employee_id': adv.employee_id,
            'amount': str(adv.amount),
            'date': str(adv.date),
            'treasury': (adv.treasury.name if adv.treasury else (adv.bank_account.name if adv.bank_account else '—')),
            'status': adv.status,
            'status_display': adv.get_status_display(),
            'notes': adv.notes,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': rows,
        'perms': {
            'cancel': request.user.has_perm_key('cancel_employee_advances'),
        },
    })


@login_required
@require_permission('add_employee_advances')
@require_POST
def advance_create(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')
    try:
        data = json.loads(request.body)
    except ValueError:
        return _err('طلب غير صالح')

    emp_id = data.get('employee')
    if not emp_id:
        return _err('يجب اختيار موظف')
    emp = get_object_or_404(Employee, pk=emp_id, tenant=tenant)
    enforce_branch_ownership(request, emp)

    amount = _dec(data.get('amount', 0))
    if amount <= 0:
        return _err('المبلغ يجب أن يكون أكبر من صفر')

    payment_method = data.get('payment_method', 'cash')
    treasury_id    = data.get('treasury')
    treasury       = None
    bank_account_id = data.get('bank_account')
    bank_account    = None
    if payment_method == 'cash':
        if not treasury_id:
            return _err('يجب اختيار الخزينة للدفع النقدي')
        treasury = get_object_or_404(Treasury, pk=treasury_id, tenant=tenant, is_hard_currency=False)
        enforce_branch_ownership(request, treasury)
        current_balance = treasury.current_balance or Decimal('0')
        if current_balance < amount:
            return _err(f"رصيد الخزينة غير كافٍ. الرصيد الحالي: {current_balance:.2f} والمطلوب صرفه: {amount:.2f}.")
    elif payment_method == 'bank':
        if not bank_account_id:
            return _err('يجب اختيار الحساب البنكي للتحويل البنكي')
        bank_account = get_object_or_404(BankAccount, pk=bank_account_id, tenant=tenant, is_active=True)
        enforce_branch_ownership(request, bank_account)
        current_balance = bank_account.current_balance or Decimal('0')
        if current_balance < amount:
            return _err(f"رصيد الحساب البنكي غير كافٍ. الرصيد الحالي: {current_balance:.2f} والمطلوب صرفه: {amount:.2f}.")
    else:
        if treasury_id:
            treasury = get_object_or_404(Treasury, pk=treasury_id, tenant=tenant, is_hard_currency=False)
            enforce_branch_ownership(request, treasury)

    from . import services as employee_services
    try:
        adv = employee_services.create_advance(
            tenant=tenant, employee=emp, amount=amount,
            date=data.get('date') or timezone.localdate(), payment_method=payment_method,
            treasury=treasury, bank_account=bank_account,
            bank_reference=(data.get('bank_reference') or '').strip(),
            notes=(data.get('notes') or '').strip(), user=request.user,
        )
    except ValueError as e:
        return _err(str(e))

    log_activity(request, 'create', f'سلفة موظف: {emp.name} — {amount}')
    return JsonResponse({'success': True, 'id': adv.pk})


@login_required
@require_permission('cancel_employee_advances')
@require_POST
def advance_cancel(request, pk):
    tenant = _tenant(request)
    adv = get_object_or_404(EmployeeAdvance.objects.select_related('employee'), pk=pk, tenant=tenant)
    enforce_branch_ownership(request, adv, field='employee__branch')
    if adv.status == 'deducted':
        return _err('السلفة مخصومة ضمن كشف راتب — لا يمكن إلغاؤها')
    if adv.status == 'cancelled':
        return _err('السلفة ملغاة مسبقاً')
    adv.cancel()
    log_activity(request, 'cancel', f'إلغاء سلفة: {adv.employee.name} — {adv.amount}')
    return JsonResponse({'success': True})


# ─────────────────────────────────────────────────────────────────────────────
# Salary Payments
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_employee_salaries')
def salary_list(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = filter_by_branch_via(EmployeeSalaryPayment.objects.filter(tenant=tenant), getattr(request, 'branch', None), field='employee__branch')
    context = {
        'stats': {
            'total':     qs.count(),
            'draft':     qs.filter(status='draft').count(),
            'paid':      qs.filter(status='paid').count(),
            'cancelled': qs.filter(status='cancelled').count(),
        },
        'employees':  Employee.objects.filter(tenant=tenant, is_active=True).for_branch(getattr(request, 'branch', None)).order_by('name'),
        'treasuries': Treasury.objects.for_tenant(tenant).filter(is_active=True, is_hard_currency=False),
        'bank_accounts': BankAccount.objects.for_tenant(tenant).filter(is_active=True),
    }
    return render(request, 'employees/salary_list.html', context)


@login_required
@require_permission('view_employee_salaries')
def salary_table_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    draw   = int(request.GET.get('draw', 1))
    start  = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()
    emp_id = request.GET.get('employee', '').strip()

    qs = EmployeeSalaryPayment.objects.filter(tenant=tenant).select_related('employee', 'treasury', 'bank_account')
    qs = filter_by_branch_via(qs, getattr(request, 'branch', None), field='employee__branch')
    records_total = qs.count()

    if status:
        qs = qs.filter(status=status)
    if emp_id:
        qs = qs.filter(employee_id=emp_id)
    if search:
        qs = qs.filter(Q(employee__name__icontains=search))

    records_filtered = qs.count()
    qs = qs[start: start + length]

    rows = []
    for sp in qs:
        rows.append({
            'id': sp.pk,
            'employee': sp.employee.name,
            'employee_id': sp.employee_id,
            'period_start': str(sp.period_start),
            'period_end':   str(sp.period_end),
            'base_salary':       str(sp.base_salary),
            'bonus':             str(sp.bonus),
            'advances_deducted': str(sp.advances_deducted),
            'deductions':        str(sp.deductions),
            'total_due':         str(sp.total_due),
            'treasury': (sp.treasury.name if sp.treasury else (sp.bank_account.name if sp.bank_account else '—')),
            'payment_method': sp.payment_method,
            'status': sp.status,
            'status_display': sp.get_status_display(),
            'notes': sp.notes,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': rows,
        'perms': {
            'pay': request.user.has_perm_key('pay_employee_salaries'),
            'cancel': request.user.has_perm_key('cancel_employee_salaries'),
        },
    })


@login_required
@require_permission('add_employee_salaries')
@require_POST
def salary_create(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')
    try:
        data = json.loads(request.body)
    except ValueError:
        return _err('طلب غير صالح')

    emp_id = data.get('employee')
    if not emp_id:
        return _err('يجب اختيار موظف')
    emp = get_object_or_404(Employee, pk=emp_id, tenant=tenant)
    enforce_branch_ownership(request, emp)

    period_start = data.get('period_start')
    period_end   = data.get('period_end')
    if not period_start or not period_end:
        return _err('الفترة مطلوبة')

    payment_method = data.get('payment_method', 'cash')
    treasury_id    = data.get('treasury')
    treasury       = None
    bank_account_id = data.get('bank_account')
    bank_account    = None
    if payment_method == 'cash':
        if not treasury_id:
            return _err('يجب اختيار الخزينة للدفع النقدي')
        treasury = get_object_or_404(Treasury, pk=treasury_id, tenant=tenant, is_hard_currency=False)
        enforce_branch_ownership(request, treasury)
    elif payment_method == 'bank':
        if not bank_account_id:
            return _err('يجب اختيار الحساب البنكي للتحويل البنكي')
        bank_account = get_object_or_404(BankAccount, pk=bank_account_id, tenant=tenant, is_active=True)
        enforce_branch_ownership(request, bank_account)
    else:
        if treasury_id:
            treasury = get_object_or_404(Treasury, pk=treasury_id, tenant=tenant, is_hard_currency=False)
            enforce_branch_ownership(request, treasury)

    base_salary = _dec(data.get('base_salary', emp.base_salary))
    advance_ids = data.get('selected_advance_ids') or []
    incentive_ids = data.get('selected_incentive_ids') or []

    from . import services as employee_services
    try:
        sp = employee_services.create_salary_payment(
            tenant=tenant, employee=emp, period_start=period_start, period_end=period_end,
            base_salary=base_salary, payment_method=payment_method, treasury=treasury,
            bank_account=bank_account, bank_reference=(data.get('bank_reference') or '').strip(),
            notes=(data.get('notes') or '').strip(),
            deductions_notes=(data.get('deductions_notes') or '').strip(),
            advance_ids=advance_ids, incentive_ids=incentive_ids, user=request.user,
        )
    except ValueError as e:
        return _err(str(e))

    log_activity(request, 'create', f'كشف راتب: {emp.name} — {period_start}')
    return JsonResponse({'success': True, 'id': sp.pk})


@login_required
@require_permission('pay_employee_salaries')
@require_POST
def salary_pay(request, pk):
    tenant = _tenant(request)
    sp = get_object_or_404(EmployeeSalaryPayment.objects.select_related('employee'), pk=pk, tenant=tenant)
    enforce_branch_ownership(request, sp, field='employee__branch')
    if sp.status != 'draft':
        return _err('الكشف ليس في حالة مسودة')
    if sp.payment_method == 'cash' and not sp.treasury:
        return _err('يجب تحديد الخزينة قبل الدفع')
    if sp.payment_method == 'bank' and not sp.bank_account:
        return _err('يجب تحديد الحساب البنكي قبل الدفع')
    try:
        sp.pay()
    except ValueError as e:
        return _err(str(e))
    log_activity(request, 'confirm', f'دفع راتب: {sp.employee.name} — {sp.period_start}')
    return JsonResponse({'success': True})


@login_required
@require_permission('cancel_employee_salaries')
@require_POST
def salary_cancel(request, pk):
    tenant = _tenant(request)
    sp = get_object_or_404(EmployeeSalaryPayment.objects.select_related('employee'), pk=pk, tenant=tenant)
    enforce_branch_ownership(request, sp, field='employee__branch')
    if sp.status == 'cancelled':
        return _err('الكشف ملغى مسبقاً')
    sp.cancel()
    log_activity(request, 'cancel', f'إلغاء راتب: {sp.employee.name} — {sp.period_start}')
    return JsonResponse({'success': True})


@login_required
@require_permission('view_employee_salaries')
def salary_detail_api(request, pk):
    tenant = _tenant(request)
    sp = get_object_or_404(EmployeeSalaryPayment.objects.select_related('employee'), pk=pk, tenant=tenant)
    enforce_branch_ownership(request, sp, field='employee__branch')
    deferred_items = [
        {
            'id': inc.pk,
            'type': inc.type,
            'type_display': inc.get_type_display(),
            'description': inc.description,
            'amount': str(inc.amount),
            'date': str(inc.date),
        }
        for inc in sp.get_pending_with_salary_incentives()
    ]
    return JsonResponse({
        'id': sp.pk,
        'employee': sp.employee.name,
        'employee_id': sp.employee_id,
        'period_start': str(sp.period_start),
        'period_end':   str(sp.period_end),
        'base_salary':       str(sp.base_salary),
        'bonus':             str(sp.bonus),
        'advances_deducted': str(sp.advances_deducted),
        'deductions':        str(sp.deductions),
        'deductions_notes':  sp.deductions_notes,
        'total_due':         str(sp.total_due),
        'treasury': (sp.treasury.name if sp.treasury else (sp.bank_account.name if sp.bank_account else '—')),
        'treasury_id': sp.treasury_id,
        'bank_account_id': sp.bank_account_id,
        'status': sp.status,
        'status_display': sp.get_status_display(),
        'notes': sp.notes,
        'deferred_items': deferred_items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Incentives / Deductions
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_employee_incentives')
def incentive_list(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = filter_by_branch_via(EmployeeIncentive.objects.filter(tenant=tenant), getattr(request, 'branch', None), field='employee__branch')
    context = {
        'stats': {
            'total':      qs.count(),
            'bonuses':    qs.filter(type='bonus').count(),
            'deductions': qs.filter(type='deduction').count(),
            'pending':    qs.filter(status='pending').count(),
            'paid':       qs.filter(status='paid').count(),
            'cancelled':  qs.filter(status='cancelled').count(),
        },
        'employees':  Employee.objects.filter(tenant=tenant, is_active=True).for_branch(getattr(request, 'branch', None)).order_by('name'),
        'treasuries': Treasury.objects.for_tenant(tenant).filter(is_active=True, is_hard_currency=False),
        'bank_accounts': BankAccount.objects.for_tenant(tenant).filter(is_active=True),
    }
    return render(request, 'employees/incentive_list.html', context)


@login_required
@require_permission('view_employee_incentives')
def incentive_table_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    draw   = int(request.GET.get('draw', 1))
    start  = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()
    itype  = request.GET.get('type', '').strip()
    emp_id = request.GET.get('employee', '').strip()

    qs = EmployeeIncentive.objects.filter(tenant=tenant).select_related('employee', 'treasury', 'bank_account')
    qs = filter_by_branch_via(qs, getattr(request, 'branch', None), field='employee__branch')
    records_total = qs.count()

    if status:
        qs = qs.filter(status=status)
    if itype:
        qs = qs.filter(type=itype)
    if emp_id:
        qs = qs.filter(employee_id=emp_id)
    if search:
        qs = qs.filter(
            Q(employee__name__icontains=search) | Q(description__icontains=search)
        )

    records_filtered = qs.count()
    qs = qs[start: start + length]

    rows = []
    for inc in qs:
        rows.append({
            'id': inc.pk,
            'employee': inc.employee.name,
            'employee_id': inc.employee_id,
            'type': inc.type,
            'type_display': inc.get_type_display(),
            'amount': str(inc.amount),
            'description': inc.description,
            'payout': inc.payout,
            'payout_display': inc.get_payout_display(),
            'date': str(inc.date),
            'treasury': (inc.treasury.name if inc.treasury else (inc.bank_account.name if inc.bank_account else '—')),
            'status': inc.status,
            'status_display': inc.get_status_display(),
            'notes': inc.notes,
        })

    incentive_perms = {
        'pay': request.user.has_perm_key('pay_employee_incentives'),
        'cancel': request.user.has_perm_key('cancel_employee_incentives'),
    }
    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': rows,
        'perms': incentive_perms,
    })


@login_required
@require_permission('add_employee_incentives')
@require_POST
def incentive_create(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')
    try:
        data = json.loads(request.body)
    except ValueError:
        return _err('طلب غير صالح')

    emp_id = data.get('employee')
    if not emp_id:
        return _err('يجب اختيار موظف')
    emp = get_object_or_404(Employee, pk=emp_id, tenant=tenant)
    enforce_branch_ownership(request, emp)

    amount = _dec(data.get('amount', 0))
    if amount <= 0:
        return _err('المبلغ يجب أن يكون أكبر من صفر')

    description = (data.get('description') or '').strip()
    if not description:
        return _err('الوصف مطلوب')

    itype          = data.get('type', 'bonus')
    payout         = data.get('payout', 'with_salary')
    payment_method = data.get('payment_method', 'cash')
    treasury_id    = data.get('treasury')
    treasury       = None
    bank_account_id = data.get('bank_account')
    bank_account    = None

    if itype == 'deduction':
        payout = 'with_salary'

    if itype == 'bonus' and payout == 'immediate':
        if payment_method == 'cash':
            if not treasury_id:
                return _err('الحوافز الفورية النقدية تتطلب تحديد الخزينة')
            treasury = get_object_or_404(Treasury, pk=treasury_id, tenant=tenant, is_hard_currency=False)
            enforce_branch_ownership(request, treasury)
            current_balance = treasury.current_balance or Decimal('0')
            if current_balance < amount:
                return _err(f"رصيد الخزينة غير كافٍ. الرصيد الحالي: {current_balance:.2f} والمطلوب صرفه: {amount:.2f}.")
        elif payment_method == 'bank':
            if not bank_account_id:
                return _err('الحوافز الفورية البنكية تتطلب تحديد الحساب البنكي')
            bank_account = get_object_or_404(BankAccount, pk=bank_account_id, tenant=tenant, is_active=True)
            enforce_branch_ownership(request, bank_account)
            current_balance = bank_account.current_balance or Decimal('0')
            if current_balance < amount:
                return _err(f"رصيد الحساب البنكي غير كافٍ. الرصيد الحالي: {current_balance:.2f} والمطلوب صرفه: {amount:.2f}.")
        elif treasury_id:
            treasury = get_object_or_404(Treasury, pk=treasury_id, tenant=tenant, is_hard_currency=False)
            enforce_branch_ownership(request, treasury)
    else:
        if treasury_id:
            treasury = get_object_or_404(Treasury, pk=treasury_id, tenant=tenant, is_hard_currency=False)
            enforce_branch_ownership(request, treasury)
        elif bank_account_id:
            bank_account = get_object_or_404(BankAccount, pk=bank_account_id, tenant=tenant, is_active=True)
            enforce_branch_ownership(request, bank_account)

    from . import services as employee_services
    try:
        inc = employee_services.create_incentive(
            tenant=tenant, employee=emp, amount=amount, description=description,
            itype=itype, payout=payout, payment_method=payment_method,
            treasury=treasury, bank_account=bank_account,
            bank_reference=(data.get('bank_reference') or '').strip(),
            date=data.get('date') or timezone.localdate(),
            notes=(data.get('notes') or '').strip(), user=request.user,
        )
    except ValueError as e:
        return _err(str(e))

    log_activity(request, 'create', f'حافز/خصم: {emp.name} — {description}')
    return JsonResponse({'success': True, 'id': inc.pk})


@login_required
@require_permission('pay_employee_incentives')
@require_POST
def incentive_pay(request, pk):
    tenant = _tenant(request)
    inc = get_object_or_404(EmployeeIncentive.objects.select_related('employee'), pk=pk, tenant=tenant)
    enforce_branch_ownership(request, inc, field='employee__branch')
    if inc.status != 'pending':
        return _err('الحافز ليس في حالة معلق')
    if inc.type != 'bonus':
        return _err('الخصومات لا تُصرف من الخزينة')
    if inc.payment_method == 'cash' and not inc.treasury:
        return _err('يجب تحديد الخزينة')
    if inc.payment_method == 'bank' and not inc.bank_account:
        return _err('يجب تحديد الحساب البنكي')
    try:
        inc.pay()
    except ValueError as e:
        return _err(str(e))
    log_activity(request, 'confirm', f'دفع حافز: {inc.employee.name} — {inc.description}')
    return JsonResponse({'success': True})


@login_required
@require_permission('cancel_employee_incentives')
@require_POST
def incentive_cancel(request, pk):
    tenant = _tenant(request)
    inc = get_object_or_404(EmployeeIncentive.objects.select_related('employee'), pk=pk, tenant=tenant)
    enforce_branch_ownership(request, inc, field='employee__branch')
    if inc.status == 'cancelled':
        return _err('الحافز ملغى مسبقاً')
    inc.cancel()
    log_activity(request, 'cancel', f'إلغاء حافز: {inc.employee.name} — {inc.description}')
    return JsonResponse({'success': True})


# ─────────────────────────────────────────────────────────────────────────────
# AJAX helpers
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_employee_advances')
def employee_pending_advances_api(request, pk):
    """إرجاع السلف القائمة للموظف لاستخدامها عند إنشاء كشف راتب"""
    tenant = _tenant(request)
    emp = get_object_or_404(Employee, pk=pk, tenant=tenant)
    enforce_branch_ownership(request, emp)
    advances = emp.advances.filter(status='pending').values('id', 'amount', 'date', 'notes')
    total = sum(a['amount'] for a in advances)
    return JsonResponse({
        'advances': [
            {'id': a['id'], 'amount': str(a['amount']), 'date': str(a['date']), 'notes': a['notes']}
            for a in advances
        ],
        'total': str(total),
    })


@login_required
@require_permission('view_employee_incentives')
def employee_pending_incentives_api(request, pk):
    """إرجاع الحوافز/الخصومات المؤجلة للموظف ضمن فترة الراتب المحددة."""
    tenant = _tenant(request)
    emp = get_object_or_404(Employee, pk=pk, tenant=tenant)
    enforce_branch_ownership(request, emp)
    start = request.GET.get('period_start')
    end = request.GET.get('period_end')

    qs = emp.incentives.filter(status='pending', payout='with_salary')
    if start:
        qs = qs.filter(date__gte=start)
    if end:
        qs = qs.filter(date__lte=end)

    bonus_total = Decimal('0')
    deduction_total = Decimal('0')
    items = []
    for inc in qs.order_by('date', 'pk'):
        items.append({
            'id': inc.pk,
            'type': inc.type,
            'type_display': inc.get_type_display(),
            'amount': str(inc.amount),
            'description': inc.description,
            'date': str(inc.date),
        })
        if inc.type == 'bonus':
            bonus_total += inc.amount
        else:
            deduction_total += inc.amount

    return JsonResponse({
        'items': items,
        'bonus_total': str(bonus_total),
        'deduction_total': str(deduction_total),
    })
