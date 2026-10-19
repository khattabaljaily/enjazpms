import json
from decimal import Decimal, InvalidOperation

from apps.accounts.activity_service import log_activity
from django.contrib.auth.decorators import login_required
from functools import wraps

from apps.accounts.decorators import require_permission, require_any_permission, branch_scope_exempt, _deny
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.utils import convert_arabic_numerals, enforce_branch_ownership, resolve_report_scope, operational_money_accounts
from apps.treasury.models import Treasury
from apps.bank_accounts.models import BankAccount

from .models import Expense, ExpenseCategory
from .services import cancel_expense, confirm_expense


def _tenant(request):
    return getattr(request, 'tenant', None)


def _err(msg, status=400):
    return JsonResponse({'success': False, 'message': msg}, status=status)


def expense_access(std_key, ho_key):
    """
    صلاحية المصروفات بنطاقين (نسخة المؤسسات):
      - std_key: مصروفات الفرع (أو المشترك العادي) — request.expense_scope = 'standard'.
      - ho_key: مصروفات الإدارة المركزية (بلا فرع، من خزائنها وحساباتها البنكية) لمستخدم بلا فرع لا يملك
        مفتاح الفرع — request.expense_scope = 'ho'.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            user = request.user
            holds_std = bool(user.has_perm_key(std_key))
            holds_ho = bool(ho_key and user.has_perm_key(ho_key))
            tenant = getattr(request, 'tenant', None)
            if holds_std:
                request.expense_scope = 'standard'
            elif holds_ho and tenant is not None and tenant.is_enterprise() and getattr(request, 'branch', None) is None:
                request.expense_scope = 'ho'
            else:
                return _deny(request)
            return view_func(request, *args, **kwargs)
        wrapper._required_permission_keys = tuple(k for k in (ho_key, std_key) if k)
        wrapper._permission_check_mode = 'any'
        return wrapper
    return decorator


def _is_ho(request):
    return getattr(request, 'expense_scope', 'standard') == 'ho'


def _expenses(request, tenant):
    """مصروفات نطاق الطلب: الإدارة = مصروفات بلا فرع فقط؛ غيره = مصروفات الفرع/المعتاد."""
    if _is_ho(request):
        return Expense.unscoped.filter(tenant=tenant, branch__isnull=True)
    return Expense.objects.filter(tenant=tenant).for_branch(getattr(request, 'branch', None))


def _ho_money_accounts(model, tenant):
    """حسابات الإدارة المركزية بالعملة المحلية (المصروفات تُسجَّل بالعملة المحلية فقط)."""
    rows = model.objects.for_tenant(tenant).filter(is_active=True, is_head_office=True)
    local = (tenant.currency or '').strip()
    return [a for a in rows if (a.currency or tenant.currency or '').strip() == local and not getattr(a, 'is_hard_currency', False)]


# ─────────────────────────────────────────────
# Categories AJAX API
# ─────────────────────────────────────────────

@login_required
@expense_access('view_expense_categories', 'view_head_office_expenses')
def category_list_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')
    categories = (
        ExpenseCategory.objects.filter(tenant=tenant, is_active=True)
        .values('id', 'name')
        .order_by('name')
    )
    return JsonResponse({'results': list(categories)})


@login_required
@expense_access('add_expense_categories', 'add_head_office_expenses')
@require_POST
def category_create_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')
    try:
        data = json.loads(request.body)
    except (ValueError, KeyError):
        return _err('طلب غير صالح')

    name = (data.get('name') or '').strip()
    if not name:
        return _err('اسم التصنيف مطلوب')

    if ExpenseCategory.objects.filter(tenant=tenant, name=name).exists():
        return _err('التصنيف موجود مسبقاً')

    cat = ExpenseCategory.objects.create(
        tenant=tenant,
        name=name,
        created_by=request.user,
        updated_by=request.user,
    )
    return JsonResponse({'success': True, 'id': cat.pk, 'name': cat.name})


# ─────────────────────────────────────────────
# Expense list
# ─────────────────────────────────────────────

@login_required
@expense_access('view_expenses', 'view_head_office_expenses')
def expense_list(request):
    try:
        tenant = _tenant(request)
        if not tenant:
            return redirect('core:no_tenant')

        qs = _expenses(request, tenant)
        today = timezone.localdate()
        stats = {
            'total': qs.count(),
            'draft': qs.filter(status='draft').count(),
            'confirmed': qs.filter(status='confirmed').count(),
            'cancelled': qs.filter(status='cancelled').count(),
            'total_confirmed': qs.filter(status='confirmed').aggregate(s=Sum('amount'))['s'] or Decimal('0'),
            'this_month': qs.filter(
                status='confirmed',
                expense_date__year=today.year,
                expense_date__month=today.month,
            ).aggregate(s=Sum('amount'))['s'] or Decimal('0'),
        }
        categories = list(ExpenseCategory.objects.filter(tenant=tenant, is_active=True).values('id', 'name'))
        local_currency = (tenant.currency or '').strip()
        if _is_ho(request):
            treasury_rows = _ho_money_accounts(Treasury, tenant)
            bank_rows = _ho_money_accounts(BankAccount, tenant)
        else:
            treasury_rows = list(operational_money_accounts(Treasury.objects.for_tenant(tenant), request).filter(is_active=True, is_hard_currency=False))
            bank_rows = list(operational_money_accounts(BankAccount.objects.for_tenant(tenant), request).filter(is_active=True))
        # المصروفات بالعملة المحلية: لا تُعرض حسابات بعملة أخرى كي لا يُخصم مبلغ بعملة خاطئة.
        in_local = lambda a: (a.currency or tenant.currency or '').strip() == local_currency
        treasuries = [{'id': t.id, 'name': t.name, 'current_balance': str(t.current_balance or Decimal('0'))}
                      for t in treasury_rows if in_local(t)]
        bank_accounts = [{'id': b.id, 'name': b.name, 'current_balance': str(b.current_balance or Decimal('0'))}
                         for b in bank_rows if in_local(b)]

        user = request.user
        std = not _is_ho(request)
        return render(request, 'expenses/expense_list.html', {
            'ho_scope': _is_ho(request),
            'local_currency': local_currency,
            'can_add': user.has_perm_key('add_expenses' if std else 'add_head_office_expenses'),
            'can_change': user.has_perm_key('change_expenses' if std else 'change_head_office_expenses'),
            'can_cancel': user.has_perm_key('delete_expenses' if std else 'delete_head_office_expenses'),
            'stats': stats,
            'categories': categories,
            'treasuries': treasuries,
            'bank_accounts': bank_accounts,
            'categories_json': json.dumps(categories),
            'treasuries_json': json.dumps(treasuries),
            'bank_accounts_json': json.dumps(bank_accounts),
        })
    except Exception as e:
        print(f"Error in expense_list: {e}")
        import traceback
        traceback.print_exc()
        raise


@login_required
@expense_access('view_expenses', 'view_head_office_expenses')
def expense_table_api(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status_filter = request.GET.get('status', '').strip()
    category_filter = request.GET.get('category', '').strip()

    qs = _expenses(request, tenant).select_related('category', 'treasury', 'bank_account')
    total = qs.count()

    if status_filter:
        qs = qs.filter(status=status_filter)
    if category_filter:
        qs = qs.filter(category_id=category_filter)
    if search:
        qs = qs.filter(
            Q(code__icontains=search) |
            Q(description__icontains=search) |
            Q(category__name__icontains=search)
        )

    filtered_count = qs.count()
    qs = qs.order_by('-id')[start: start + length]

    STATUS_LABELS = {
        'draft': '<span class="badge bg-secondary">مسودة</span>',
        'confirmed': '<span class="badge bg-success">مؤكد</span>',
        'cancelled': '<span class="badge bg-danger">ملغي</span>',
    }
    
    METHOD_LABELS = {
        'cash': '<i class="fas fa-coins text-warning me-1"></i>نقدي',
        'bank': '<i class="fas fa-building-columns text-info me-1"></i>بنكي',
    }

    rows = []
    for exp in qs:
        rows.append({
            'DT_RowId': f'row_{exp.pk}',
            'code': exp.code,
            'description': exp.description,
            'category': exp.category.name,
            'expense_date': exp.expense_date.strftime('%Y-%m-%d'),
            'amount': str(exp.amount),
            'payment_method': METHOD_LABELS.get(exp.payment_method, exp.payment_method),
            'treasury': (exp.treasury.name if exp.treasury else (exp.bank_account.name if exp.bank_account else '—')),
            'status': STATUS_LABELS.get(exp.status, exp.status),
            'status_raw': exp.status,
            'id': exp.pk,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered_count,
        'data': rows,
    })


# ─────────────────────────────────────────────
# Expense create / edit
# ─────────────────────────────────────────────

@login_required
@expense_access('add_expenses', 'add_head_office_expenses')
@require_POST
def expense_create(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    return _process_expense_post(request, tenant, None)


@login_required
@expense_access('change_expenses', 'change_head_office_expenses')
@require_POST
def expense_edit(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(_expenses(request, tenant), pk=pk)
    enforce_branch_ownership(request, expense)
    if expense.status not in ('draft',):
        return _err('لا يمكن تعديل مصروف غير مسودة')

    return _process_expense_post(request, tenant, expense)


def _process_expense_post(request, tenant, expense):
    try:
        data = json.loads(request.body)
    except ValueError:
        return _err('طلب غير صالح')

    try:
        category_id = int(data.get('category_id', 0))
        category = ExpenseCategory.objects.get(pk=category_id, tenant=tenant, is_active=True)
    except (ExpenseCategory.DoesNotExist, ValueError):
        return _err('التصنيف غير صالح')

    try:
        amount_str = convert_arabic_numerals(data.get('amount', '0'))
        amount = Decimal(str(amount_str))
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        return _err('المبلغ غير صالح')

    description = (data.get('description') or '').strip()
    if not description:
        return _err('الوصف مطلوب')

    expense_date = data.get('expense_date', '')
    if not expense_date:
        return _err('التاريخ مطلوب')

    payment_method = data.get('payment_method', 'cash')
    if payment_method not in ('cash', 'bank'):
        payment_method = 'cash'

    treasury = None
    bank_account = None
    ho = _is_ho(request)
    if payment_method == 'cash':
        treasury_id = data.get('treasury_id')
        if ho and not treasury_id:
            return _err('اختر خزينة من خزائن الإدارة المركزية')
        if treasury_id:
            try:
                if ho:
                    treasury = Treasury.objects.for_tenant(tenant).get(pk=int(treasury_id), is_active=True, is_head_office=True, is_hard_currency=False)
                else:
                    treasury = operational_money_accounts(Treasury.objects.for_tenant(tenant), request).get(pk=int(treasury_id), is_active=True, is_hard_currency=False)
                    enforce_branch_ownership(request, treasury)
            except (Treasury.DoesNotExist, ValueError):
                return _err('الخزينة غير صالحة')
    elif payment_method == 'bank':
        bank_account_id = data.get('bank_account_id')
        if not bank_account_id:
            return _err('يجب اختيار الحساب البنكي')
        try:
            if ho:
                bank_account = BankAccount.objects.for_tenant(tenant).get(pk=int(bank_account_id), is_active=True, is_head_office=True)
            else:
                bank_account = operational_money_accounts(BankAccount.objects.for_tenant(tenant), request).get(pk=int(bank_account_id), is_active=True)
                enforce_branch_ownership(request, bank_account)
        except (BankAccount.DoesNotExist, ValueError):
            return _err('الحساب البنكي غير صالح')

    reference_number = (data.get('reference_number') or '').strip()
    notes = (data.get('notes') or '').strip()

    if expense is None:
        expense = Expense(tenant=tenant, branch=None if ho else getattr(request, 'branch', None), created_by=request.user)

    expense.category = category
    expense.description = description
    expense.amount = amount
    expense.expense_date = expense_date
    expense.payment_method = payment_method
    expense.treasury = treasury
    expense.bank_account = bank_account
    expense.reference_number = reference_number
    expense.notes = notes
    expense.updated_by = request.user
    is_new = expense.pk is None

    try:
        with transaction.atomic():
            expense.save()
            confirm_expense(expense, user=request.user)
    except ValueError as e:
        return _err(str(e))

    if is_new:
        log_activity(request, 'إضافة مصروف إدارة مركزية' if ho else 'إضافة مصروف جديد',
                     f"الوصف: {expense.description}\nالفئة: {expense.category.name}\nالمبلغ: {expense.amount}", 'create')

    return JsonResponse({
        'success': True,
        'redirect': '/expenses/',
    })


# ─────────────────────────────────────────────
# Expense detail
# ─────────────────────────────────────────────

@login_required
@expense_access('view_expenses', 'view_head_office_expenses')
def expense_detail_api(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(_expenses(request, tenant).select_related('category', 'treasury', 'bank_account'), pk=pk)
    enforce_branch_ownership(request, expense)
    return JsonResponse({'success': True, 'data': {
        'id': expense.pk,
        'code': expense.code,
        'expense_date': expense.expense_date.isoformat(),
        'category_id': expense.category_id,
        'description': expense.description,
        'amount': str(expense.amount),
        'payment_method': expense.payment_method,
        'treasury_id': expense.treasury_id,
        'bank_account_id': expense.bank_account_id,
        'reference_number': expense.reference_number or '',
        'notes': expense.notes or '',
        'status_raw': expense.status,
    }})


# ─────────────────────────────────────────────
# Confirm / Cancel AJAX
# ─────────────────────────────────────────────

@login_required
@expense_access('change_expenses', 'change_head_office_expenses')
@require_POST
def expense_confirm_ajax(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(_expenses(request, tenant), pk=pk)
    enforce_branch_ownership(request, expense)
    try:
        confirm_expense(expense, user=request.user)
    except ValueError as e:
        return _err(str(e))

    return JsonResponse({'success': True, 'message': 'تم تأكيد المصروف بنجاح'})


@login_required
@expense_access('delete_expenses', 'delete_head_office_expenses')
@require_POST
def expense_cancel_ajax(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(_expenses(request, tenant), pk=pk)
    enforce_branch_ownership(request, expense)
    try:
        cancel_expense(expense, user=request.user)
    except ValueError as e:
        return _err(str(e))

    return JsonResponse({'success': True, 'message': 'تم إلغاء المصروف'})


# ─────────────────────────────────────────────────────────────────
#   REPORTS
# ─────────────────────────────────────────────────────────────────

def _report_scope(request, std_key):
    """
    نطاق تقارير المصروفات: مثل resolve_report_scope، ويضيف لمدير النشاط خيار «الإدارة المركزية»
    (?branch=head_office) لمصروفاتها وحدها. مستخدم الإدارة الذي لا يملك مفتاح التقرير العادي
    (std_key) لا يرى إلا مصروفات الإدارة. يُرجع (branch, is_central_admin, branches, ho_scope).
    """
    branch, is_central_admin, branches = resolve_report_scope(request)
    ho = False
    tenant = getattr(request, 'tenant', None)
    can_ho = bool(is_central_admin and tenant is not None and tenant.is_enterprise()
                  and request.user.has_perm_key('view_head_office_expenses_report'))
    request.report_head_office_option = can_ho
    if can_ho and (request.GET.get('branch') == 'head_office' or not request.user.has_perm_key(std_key)):
        ho, branch = True, None
    request.report_head_office = ho
    return branch, is_central_admin, branches, ho


def _parse_date(value):
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@login_required
@require_any_permission('view_expenses_summary_report', 'view_head_office_expenses_report')
def expenses_summary_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_summary_report')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    gen = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope)
    report = gen.get_summary_report()
    by_cat = gen.get_by_category_report()

    return render(request, 'expenses/reports/summary.html', {
        'report': report,
        'by_category': by_cat,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'expenses_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
        'ho_scope': ho_scope,
    })


@login_required
@require_any_permission('view_expenses_summary_report', 'view_head_office_expenses_report')
def expenses_summary_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_summary_report')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope).get_by_category_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="expenses_summary_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الفئة', 'عدد المصروفات', 'الإجمالي'])
    for row in report['data']:
        writer.writerow([row['category_name'], row['expense_count'], row['total_amount']])
    return response


@login_required
@require_any_permission('view_expenses_details_report', 'view_head_office_expenses_report')
def expenses_details_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_details_report')

    category_id = request.GET.get('category_id') or None
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope).get_details_report(category_id=category_id)

    return render(request, 'expenses/reports/details.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'selected_category_id': category_id,
        'section': 'expenses_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
        'ho_scope': ho_scope,
    })


@login_required
@require_any_permission('view_expenses_details_report', 'view_head_office_expenses_report')
def expenses_details_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_details_report')

    category_id = request.GET.get('category_id') or None
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope).get_details_report(category_id=category_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="expenses_details_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['التاريخ', 'الرقم', 'الوصف', 'الفئة', 'طريقة الدفع', 'الخزينة', 'المبلغ'])
    for row in report['data']:
        writer.writerow([row['expense_date'], row['code'], row['description'], row['category_name'], row['payment_method'], row['treasury_name'], row['amount']])
    return response


# ─────────────────────────────────────────────────────────────────
#   EXPENSES BY CATEGORY
# ─────────────────────────────────────────────────────────────────

@login_required
@require_any_permission('view_expenses_by_category_report', 'view_head_office_expenses_report')
def expenses_by_category_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_by_category_report')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope).get_by_category_report()

    return render(request, 'expenses/reports/by_category.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'expenses_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
        'ho_scope': ho_scope,
    })


@login_required
@require_any_permission('view_expenses_by_category_report', 'view_head_office_expenses_report')
def expenses_by_category_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_by_category_report')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope).get_by_category_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="expenses_by_category_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])
    writer.writerow(['الفئة', 'عدد المصروفات', 'الإجمالي'])
    for row in report['data']:
        writer.writerow([row['category_name'], row['expense_count'], row['total_amount']])
    writer.writerow([])
    writer.writerow(['الإجمالي الكلي', '', report['grand_total']])
    return response


# ─────────────────────────────────────────────────────────────────
#   EXPENSES BY DATE
# ─────────────────────────────────────────────────────────────────

@login_required
@require_any_permission('view_expenses_by_date_report', 'view_head_office_expenses_report')
def expenses_by_date_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_by_date_report')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    group_by = request.GET.get('group_by', 'day')

    report = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope).get_by_date_report(group_by=group_by)

    return render(request, 'expenses/reports/by_date.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'group_by': group_by,
        'section': 'expenses_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
        'ho_scope': ho_scope,
    })


@login_required
@require_any_permission('view_expenses_by_date_report', 'view_head_office_expenses_report')
def expenses_by_date_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter, ho_scope = _report_scope(request, 'view_expenses_by_date_report')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    group_by = request.GET.get('group_by', 'day')

    report = ExpensesReportGenerator(tenant, start_date, end_date, branch=branch, head_office=ho_scope).get_by_date_report(group_by=group_by)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="expenses_by_date_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
    writer.writerow([])
    writer.writerow(['الفترة', 'عدد المصروفات', 'الإجمالي'])
    for row in report['data']:
        writer.writerow([row['label'], row['expense_count'], row['total_amount']])
    return response
