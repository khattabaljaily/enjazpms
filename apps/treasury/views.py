from apps.accounts.activity_service import log_activity
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import TreasuryForm
from .models import Treasury, TreasuryMovement


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


@login_required
@require_permission('view_treasuries')
def treasury_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Treasury.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    default = qs.filter(is_default=True).count()

    context = {
        'form': TreasuryForm(),
        'stats': {
            'total': total,
            'active': active,
            'inactive': total - active,
            'default': default,
        },
    }
    return render(request, 'treasury/treasury_list.html', context)


@login_required
@require_permission('view_treasuries')
def treasury_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    queryset = Treasury.objects.for_tenant(tenant)
    records_total = queryset.count()

    if status == 'active':
        queryset = queryset.filter(is_active=True)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False)

    if search_value:
        queryset = queryset.filter(
            Q(name__icontains=search_value)
            | Q(code__icontains=search_value)
            | Q(notes__icontains=search_value)
        )

    records_filtered = queryset.count()

    order_column_index = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    order_column_name = request.GET.get(f'columns[{order_column_index}][data]', 'created_at')

    allowed_order_fields = {
        'name': 'name',
        'code': 'code',
        'current_balance': 'current_balance',
        'is_active': 'is_active',
        'is_default': 'is_default',
        'created_at': 'created_at',
    }
    order_field = allowed_order_fields.get(order_column_name, 'created_at')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    queryset = queryset.order_by(order_field)[start:start + length]

    data = [
        {
            'id': treasury.id,
            'name': treasury.name,
            'code': treasury.code or '—',
            'current_balance': str(treasury.current_balance),
            'is_active': treasury.is_active,
            'is_default': treasury.is_default,
            'is_system_default': treasury.is_system_default,
        }
        for treasury in queryset
    ]

    return JsonResponse(
        {
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data,
        }
    )


@login_required
@require_permission('add_treasuries')
def treasury_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = TreasuryForm(request.POST)
    if form.is_valid():
        treasury = form.save(commit=False)
        treasury.tenant = tenant
        treasury.created_by = request.user
        treasury.updated_by = request.user

        if treasury.is_default:
            Treasury.objects.for_tenant(tenant).filter(is_default=True).update(is_default=False)

        treasury.save()
        log_activity(request, 'إضافة خزينة جديدة',
                     f"الخزينة: {treasury.name}\nالنوع: {treasury.get_treasury_type_display()}", 'create')

        return JsonResponse({
            'success': True,
            'message': 'تم إضافة الخزينة بنجاح',
            'id': treasury.id,
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
@require_permission('view_treasuries')
def treasury_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)

    return JsonResponse({
        'success': True,
        'data': {
            'id': treasury.id,
            'name': treasury.name,
            'code': treasury.code,
            'notes': treasury.notes,
            'is_active': treasury.is_active,
            'is_default': treasury.is_default,
            'is_system_default': treasury.is_system_default,
            'current_balance': str(treasury.current_balance),
        }
    })


@login_required
@require_permission('view_treasuries')
def treasury_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)
    qs = (
        TreasuryMovement.objects.for_tenant(tenant)
        .filter(treasury=treasury)
        .order_by('-movement_date', '-id')[:200]
    )

    data = [
        {
            'id': m.id,
            'movement_date': m.movement_date.isoformat(),
            'movement_type': m.movement_type,
            'amount': str(m.amount),
            'running_balance': str(m.running_balance),
            'reference_type': m.reference_type or '—',
            'description': m.description or '—',
        }
        for m in qs
    ]

    return JsonResponse({'success': True, 'data': data})


@login_required
@require_permission('change_treasuries')
def treasury_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)
    form = TreasuryForm(request.POST, instance=treasury)

    if form.is_valid():
        treasury = form.save(commit=False)
        treasury.updated_by = request.user

        if treasury.is_system_default:
            treasury.is_default = True
            treasury.is_active = True

        if treasury.is_default:
            Treasury.objects.for_tenant(tenant).exclude(pk=treasury.pk).filter(is_default=True).update(is_default=False)

        treasury.save()
        return JsonResponse({
            'success': True,
            'message': 'تم تعديل الخزينة بنجاح',
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
@require_permission('delete_treasuries')
def treasury_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)

    if treasury.is_system_default:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف الخزينة الافتراضية النظامية.'}, status=400)

    if treasury.is_default:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف الخزينة الافتراضية. عيّن خزينة أخرى كافتراضية أولاً.'}, status=400)

    active_count = Treasury.objects.for_tenant(tenant).filter(is_active=True).count()
    if active_count <= 1:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف الخزينة الوحيدة.'}, status=400)

    if treasury.movements.exists():
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف خزينة لها حركات. قم بإيقافها فقط.'}, status=400)

    treasury.delete()
    return JsonResponse({'success': True, 'message': 'تم حذف الخزينة بنجاح'})



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
@require_permission('view_treasury_balances_report')
def treasury_balances_report(request):
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = TreasuryReportGenerator(tenant).get_balances_report()

    return render(request, 'treasury/reports/balances.html', {
        'report': report,
        'section': 'treasury_reports',
    })


@login_required
@require_permission('view_treasury_balances_report')
def treasury_balances_report_export(request):
    import csv
    from django.http import HttpResponse
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = TreasuryReportGenerator(tenant).get_balances_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="treasury_balances.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الخزينة', 'الكود', 'الرصيد الحالي'])
    for row in report['data']:
        writer.writerow([row['name'], row['code'], row['current_balance']])
    return response


@login_required
@require_permission('view_treasury_statement_report')
def treasury_statement_report(request):
    from datetime import timedelta
    from django.utils import timezone
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    treasury_id = request.GET.get('treasury_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    gen = TreasuryReportGenerator(tenant, start_date, end_date)
    report = gen.get_statement_report(treasury_id) if treasury_id else None
    treasuries = Treasury.objects.filter(tenant=tenant, is_active=True).order_by('name')

    return render(request, 'treasury/reports/statement.html', {
        'report': report,
        'treasuries': treasuries,
        'selected_treasury_id': treasury_id,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'treasury_reports',
    })


@login_required
@require_permission('view_treasury_statement_report')
def treasury_statement_report_export(request):
    import csv
    from datetime import timedelta
    from django.http import HttpResponse
    from django.utils import timezone
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    treasury_id = request.GET.get('treasury_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = TreasuryReportGenerator(tenant, start_date, end_date).get_statement_report(treasury_id) if treasury_id else None
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="treasury_statement_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    if report:
        writer.writerow([f'كشف خزينة: {report["treasury"].name}'])
        writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
        writer.writerow([])
        writer.writerow(['التاريخ', 'نوع الحركة', 'الوصف', 'قبض', 'صرف', 'الرصيد بعد'])
        for row in report['data']:
            writer.writerow([row['movement_date'], row['movement_type'], row['description'], row['receipt'], row['disbursement'], row['running_balance']])
    return response


@login_required
@require_permission('view_treasury_movements_report')
def treasury_movements_report(request):
    from datetime import timedelta
    from django.utils import timezone
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = TreasuryReportGenerator(tenant, start_date, end_date).get_movements_summary()

    return render(request, 'treasury/reports/movements.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'treasury_reports',
    })


@login_required
@require_permission('view_treasury_movements_report')
def treasury_movements_report_export(request):
    import csv
    from datetime import timedelta
    from django.http import HttpResponse
    from django.utils import timezone
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = TreasuryReportGenerator(tenant, start_date, end_date).get_movements_summary()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="treasury_movements_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['التاريخ', 'الخزينة', 'نوع الحركة', 'الوصف', 'قبض', 'صرف', 'الرصيد بعد'])
    for row in report['data']:
        writer.writerow([row['movement_date'], row['treasury_name'], row['movement_type'], row['description'], row['receipt'], row['disbursement'], row['running_balance']])
    return response
