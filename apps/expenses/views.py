import json
from decimal import Decimal, InvalidOperation

from apps.accounts.activity_service import log_activity
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from django.db import transaction
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.utils import convert_arabic_numerals
from apps.treasury.models import Treasury

from .models import Expense, ExpenseCategory
from .services import cancel_expense, confirm_expense


def _tenant(request):
    return getattr(request, 'tenant', None)


def _err(msg, status=400):
    return JsonResponse({'success': False, 'message': msg}, status=status)


# ─────────────────────────────────────────────
# Categories AJAX API
# ─────────────────────────────────────────────

@login_required
@require_permission('view_expense_categories')
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
@require_permission('add_expense_categories')
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

@require_permission('view_expenses')
def expense_list(request):
    try:
        tenant = _tenant(request)
        if not tenant:
            return redirect('core:no_tenant')

        qs = Expense.objects.filter(tenant=tenant)
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
        treasuries = [
            {
                'id': t.id,
                'name': t.name,
                'current_balance': str(t.current_balance or Decimal('0'))
            }
            for t in Treasury.objects.filter(tenant=tenant, is_active=True).only('id', 'name', 'current_balance')
        ]
        
        return render(request, 'expenses/expense_list.html', {
            'stats': stats,
            'categories': categories,
            'treasuries': treasuries,
            'categories_json': json.dumps(categories),
            'treasuries_json': json.dumps(treasuries),
        })
    except Exception as e:
        print(f"Error in expense_list: {e}")
        import traceback
        traceback.print_exc()
        raise


@login_required
@require_permission('view_expenses')
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

    qs = Expense.objects.filter(tenant=tenant).select_related('category', 'treasury')
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
    qs = qs.order_by('-expense_date', '-id')[start: start + length]

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
            'treasury': exp.treasury.name if exp.treasury else '—',
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
@require_permission('add_expenses')
@require_POST
def expense_create(request):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    return _process_expense_post(request, tenant, None)


@login_required
@require_permission('change_expenses')
@require_POST
def expense_edit(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(Expense, pk=pk, tenant=tenant)
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
    if payment_method == 'cash':
        treasury_id = data.get('treasury_id')
        if treasury_id:
            try:
                treasury = Treasury.objects.get(pk=int(treasury_id), tenant=tenant, is_active=True)
            except (Treasury.DoesNotExist, ValueError):
                return _err('الخزينة غير صالحة')

    reference_number = (data.get('reference_number') or '').strip()
    notes = (data.get('notes') or '').strip()

    if expense is None:
        expense = Expense(tenant=tenant, created_by=request.user)

    expense.category = category
    expense.description = description
    expense.amount = amount
    expense.expense_date = expense_date
    expense.payment_method = payment_method
    expense.treasury = treasury
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
        log_activity(request, 'إضافة مصروف جديد',
                     f"الوصف: {expense.description}\nالفئة: {expense.category.name}\nالمبلغ: {expense.amount}", 'create')

    return JsonResponse({
        'success': True,
        'redirect': '/expenses/',
    })


# ─────────────────────────────────────────────
# Expense detail
# ─────────────────────────────────────────────

@login_required
@require_permission('view_expenses')
def expense_detail_api(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(Expense.objects.select_related('category', 'treasury'), pk=pk, tenant=tenant)
    return JsonResponse({'success': True, 'data': {
        'id': expense.pk,
        'code': expense.code,
        'expense_date': expense.expense_date.isoformat(),
        'category_id': expense.category_id,
        'description': expense.description,
        'amount': str(expense.amount),
        'payment_method': expense.payment_method,
        'treasury_id': expense.treasury_id,
        'reference_number': expense.reference_number or '',
        'notes': expense.notes or '',
        'status_raw': expense.status,
    }})


# ─────────────────────────────────────────────
# Confirm / Cancel AJAX
# ─────────────────────────────────────────────

@login_required
@require_permission('change_expenses')
@require_POST
def expense_confirm_ajax(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(Expense, pk=pk, tenant=tenant)
    try:
        confirm_expense(expense, user=request.user)
    except ValueError as e:
        return _err(str(e))

    return JsonResponse({'success': True, 'message': 'تم تأكيد المصروف بنجاح'})


@login_required
@require_permission('delete_expenses')
@require_POST
def expense_cancel_ajax(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return _err('لا يوجد نشاط تجاري')

    expense = get_object_or_404(Expense, pk=pk, tenant=tenant)
    try:
        cancel_expense(expense, user=request.user)
    except ValueError as e:
        return _err(str(e))

    return JsonResponse({'success': True, 'message': 'تم إلغاء المصروف'})


# ─────────────────────────────────────────────────────────────────
#   REPORTS
# ─────────────────────────────────────────────────────────────────

def _parse_date(value):
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None


@login_required
@require_permission('view_expenses_summary_report')
def expenses_summary_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    gen = ExpensesReportGenerator(tenant, start_date, end_date)
    report = gen.get_summary_report()
    by_cat = gen.get_by_category_report()

    return render(request, 'expenses/reports/summary.html', {
        'report': report,
        'by_category': by_cat,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'expenses_reports',
    })


@login_required
@require_permission('view_expenses_summary_report')
def expenses_summary_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date).get_by_category_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="expenses_summary_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الفئة', 'عدد المصروفات', 'الإجمالي'])
    for row in report['data']:
        writer.writerow([row['category_name'], row['expense_count'], row['total_amount']])
    return response


@login_required
@require_permission('view_expenses_details_report')
def expenses_details_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    category_id = request.GET.get('category_id') or None
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date).get_details_report(category_id=category_id)

    return render(request, 'expenses/reports/details.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'selected_category_id': category_id,
        'section': 'expenses_reports',
    })


@login_required
@require_permission('view_expenses_details_report')
def expenses_details_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    category_id = request.GET.get('category_id') or None
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date).get_details_report(category_id=category_id)
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
@require_permission('view_expenses_by_category_report')
def expenses_by_category_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date).get_by_category_report()

    return render(request, 'expenses/reports/by_category.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'expenses_reports',
    })


@login_required
@require_permission('view_expenses_by_category_report')
def expenses_by_category_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = ExpensesReportGenerator(tenant, start_date, end_date).get_by_category_report()
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
@require_permission('view_expenses_by_date_report')
def expenses_by_date_report(request):
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    group_by = request.GET.get('group_by', 'day')

    report = ExpensesReportGenerator(tenant, start_date, end_date).get_by_date_report(group_by=group_by)

    return render(request, 'expenses/reports/by_date.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'group_by': group_by,
        'section': 'expenses_reports',
    })


@login_required
@require_permission('view_expenses_by_date_report')
def expenses_by_date_report_export(request):
    import csv
    from django.http import HttpResponse
    from datetime import timedelta
    from .reports import ExpensesReportGenerator
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    group_by = request.GET.get('group_by', 'day')

    report = ExpensesReportGenerator(tenant, start_date, end_date).get_by_date_report(group_by=group_by)
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
