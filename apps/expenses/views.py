import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

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
def expense_list(request):
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
    return render(request, 'expenses/expense_list.html', {'stats': stats})


@login_required
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
def expense_create(request):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    if request.method == 'POST':
        return _process_expense_post(request, tenant, None)

    categories = ExpenseCategory.objects.filter(tenant=tenant, is_active=True).values('id', 'name')
    treasuries = Treasury.objects.filter(tenant=tenant, is_active=True).values('id', 'name', 'current_balance')
    return render(request, 'expenses/expense_form.html', {
        'categories': list(categories),
        'treasuries': list(treasuries),
        'today': timezone.localdate().isoformat(),
        'mode': 'create',
    })


@login_required
def expense_edit(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    expense = get_object_or_404(Expense, pk=pk, tenant=tenant)
    if expense.status not in ('draft',):
        return redirect('expenses:detail', pk=pk)

    if request.method == 'POST':
        return _process_expense_post(request, tenant, expense)

    categories = ExpenseCategory.objects.filter(tenant=tenant, is_active=True).values('id', 'name')
    treasuries = Treasury.objects.filter(tenant=tenant, is_active=True).values('id', 'name', 'current_balance')
    return render(request, 'expenses/expense_form.html', {
        'expense': expense,
        'categories': list(categories),
        'treasuries': list(treasuries),
        'today': timezone.localdate().isoformat(),
        'mode': 'edit',
    })


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
        amount = Decimal(str(data.get('amount', '0')))
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

    treasury_id = data.get('treasury_id')
    treasury = None
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
    expense.save()

    return JsonResponse({
        'success': True,
        'redirect': f'/expenses/{expense.pk}/',
    })


# ─────────────────────────────────────────────
# Expense detail
# ─────────────────────────────────────────────

@login_required
def expense_detail(request, pk):
    tenant = _tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    expense = get_object_or_404(Expense, pk=pk, tenant=tenant)
    return render(request, 'expenses/expense_detail.html', {'expense': expense})


# ─────────────────────────────────────────────
# Confirm / Cancel AJAX
# ─────────────────────────────────────────────

@login_required
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
