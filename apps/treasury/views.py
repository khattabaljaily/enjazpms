import json
from decimal import Decimal, InvalidOperation

from apps.accounts.activity_service import log_activity
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_tz
from django.views.decorators.http import require_POST

from .forms import TreasuryForm
from .models import Treasury, TreasuryMovement
from .reports import REFERENCE_TYPE_AR


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


from apps.core.utils import CURRENCY_SYMBOLS as _CURRENCY_SYMBOLS, currency_symbol as _currency_symbol


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

    # Auto-create HC treasury if tenant has HC mode but treasury doesn't exist yet
    if tenant.hard_currency_mode and tenant.hard_currency:
        from apps.core.signals import _ensure_hc_treasury
        _ensure_hc_treasury(tenant)

    hc_treasury = Treasury.objects.for_tenant(tenant).filter(is_hard_currency=True).first()
    other_treasuries = Treasury.objects.for_tenant(tenant).filter(is_active=True)

    local_cur = tenant.currency or 'SDG'
    hc_cur = tenant.hard_currency if tenant.hard_currency_mode else ''

    context = {
        'form': TreasuryForm(),
        'today': dj_tz.localdate().isoformat(),
        'hc_mode': tenant.hard_currency_mode,
        'hc_currency': hc_cur,
        'hc_currency_symbol': _currency_symbol(hc_cur),
        'hc_treasury': hc_treasury,
        'local_currency': local_cur,
        'local_currency_symbol': _currency_symbol(local_cur),
        'transfer_treasuries': list(other_treasuries.values('id', 'name', 'currency', 'is_hard_currency')),
        'currency_symbols_json': {k: v for k, v in _CURRENCY_SYMBOLS.items()},
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
    if not tenant.hard_currency_mode:
        queryset = queryset.filter(is_hard_currency=False)
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

    local_currency = tenant.currency or 'SDG'
    data = [
        {
            'id': treasury.id,
            'name': treasury.name,
            'code': treasury.code or '—',
            'current_balance': str(treasury.current_balance),
            'currency': treasury.currency or local_currency,
            'is_active': treasury.is_active,
            'is_default': treasury.is_default,
            'is_system_default': treasury.is_system_default,
            'is_hard_currency': treasury.is_hard_currency,
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

        # Opening balance
        ob_amount = request.POST.get('opening_balance', '').strip()
        ob_date   = request.POST.get('opening_balance_date', '').strip()
        if ob_amount:
            try:
                ob_amount_dec = Decimal(ob_amount)
                if ob_amount_dec > 0:
                    from .services import set_opening_balance
                    import datetime
                    if not ob_date:
                        ob_date = dj_tz.localdate().isoformat()
                    set_opening_balance(
                        tenant, treasury,
                        amount=ob_amount_dec,
                        date=datetime.date.fromisoformat(ob_date),
                        user=request.user,
                    )
            except Exception:
                pass

        log_activity(request, 'إضافة خزينة جديدة',
                     f"الخزينة: {treasury.name}", 'create')

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

    ob_mv = TreasuryMovement.objects.filter(
        treasury=treasury, reference_type='opening_balance'
    ).first()

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
            'opening_balance': str(ob_mv.amount) if ob_mv else '0',
            'opening_balance_date': ob_mv.movement_date.isoformat() if ob_mv else '',
        }
    })


@login_required
@require_permission('view_treasury_transactions')
def treasury_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)
    qs = (
        TreasuryMovement.objects.for_tenant(tenant)
        .filter(treasury=treasury)
        .order_by('-id')[:200]
    )

    data = [
        {
            'id': m.id,
            'movement_date': m.movement_date.isoformat(),
            'movement_type': m.get_movement_type_display(),
            'movement_type_key': m.movement_type,
            'amount': str(m.amount),
            'running_balance': str(m.running_balance),
            'reference_type': REFERENCE_TYPE_AR.get(m.reference_type, m.reference_type) if m.reference_type else '',
            'description': m.description or '',
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

        # Opening balance update
        ob_amount = request.POST.get('opening_balance', '').strip()
        ob_date   = request.POST.get('opening_balance_date', '').strip()
        try:
            ob_amount_dec = Decimal(ob_amount) if ob_amount else Decimal('0')
            from .services import set_opening_balance
            import datetime
            if not ob_date:
                ob_date = dj_tz.localdate().isoformat()
            set_opening_balance(
                tenant, treasury,
                amount=ob_amount_dec,
                date=datetime.date.fromisoformat(ob_date),
                user=request.user,
            )
        except Exception:
            pass

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

    if treasury.is_hard_currency:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف خزينة العملة الصعبة.'}, status=400)

    if treasury.is_default:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف الخزينة الافتراضية. عيّن خزينة أخرى كافتراضية أولاً.'}, status=400)

    active_count = Treasury.objects.for_tenant(tenant).filter(is_active=True).count()
    if active_count <= 1:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف الخزينة الوحيدة.'}, status=400)

    if treasury.movements.exists():
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف خزينة لها حركات. قم بإيقافها فقط.'}, status=400)

    treasury.delete()
    return JsonResponse({'success': True, 'message': 'تم حذف الخزينة بنجاح'})


@login_required
@require_permission('transfer_treasuries')
@require_POST
def treasury_transfer_api(request):
    """تحويل بين خزينتين مع سعر صرف — يُنشئ خصماً وإيداعاً تلقائياً."""
    from .services import post_treasury_transfer

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    # ── تحقق من الحقول ──
    try:
        from_id = int(payload['from_treasury'])
        to_id = int(payload['to_treasury'])
        from_amount = Decimal(str(payload['from_amount']).replace(',', '.'))
        to_amount = Decimal(str(payload['to_amount']).replace(',', '.'))
        exchange_rate = Decimal(str(payload['exchange_rate']).replace(',', '.'))
        transfer_date = payload['transfer_date']
        notes = str(payload.get('notes', '')).strip()
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return JsonResponse({'success': False, 'message': 'يرجى تعبئة جميع الحقول بشكل صحيح'}, status=400)

    if from_id == to_id:
        return JsonResponse({'success': False, 'message': 'لا يمكن التحويل من الخزينة إلى نفسها'}, status=400)
    if from_amount <= 0 or to_amount <= 0:
        return JsonResponse({'success': False, 'message': 'يجب أن تكون المبالغ أكبر من صفر'}, status=400)

    from_treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=from_id)
    to_treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=to_id)

    # ── قيد: التحويل يجب أن يشمل خزينة العملة الصعبة عندها ──
    hc_treasuries = {from_treasury.is_hard_currency, to_treasury.is_hard_currency}
    if True in hc_treasuries and exchange_rate <= 0:
        return JsonResponse({'success': False, 'message': 'يجب إدخال سعر صرف صحيح عند التحويل مع خزينة العملة الصعبة'}, status=400)

    try:
        transfer = post_treasury_transfer(
            tenant=tenant,
            from_treasury=from_treasury,
            to_treasury=to_treasury,
            from_amount=from_amount,
            to_amount=to_amount,
            exchange_rate=exchange_rate,
            transfer_date=transfer_date,
            notes=notes,
            user=request.user,
        )
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e) or 'تعذر تنفيذ التحويل'}, status=400)

    log_activity(
        request, 'تحويل بين الخزائن',
        f'من: {from_treasury.name} ({from_amount}) → إلى: {to_treasury.name} ({to_amount}) | سعر الصرف: {exchange_rate}',
        'create',
    )
    return JsonResponse({
        'success': True,
        'message': f'تم التحويل بنجاح — {from_treasury.name} ← {to_treasury.name}',
        'transfer_id': transfer.id,
    })


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
        writer.writerow(['التاريخ', 'نوع الحركة', 'الوصف', 'وارد', 'صادر', 'الرصيد بعد'])
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
    treasury_id = request.GET.get('treasury_id') or None

    report = TreasuryReportGenerator(tenant, start_date, end_date).get_movements_summary(treasury_id=treasury_id) if treasury_id else None
    treasuries = Treasury.objects.filter(tenant=tenant, is_active=True).order_by('name')

    return render(request, 'treasury/reports/movements.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'treasuries': treasuries,
        'selected_treasury_id': treasury_id or '',
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
    treasury_id = request.GET.get('treasury_id') or None

    report = TreasuryReportGenerator(tenant, start_date, end_date).get_movements_summary(treasury_id=treasury_id) if treasury_id else None
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="treasury_movements_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['التاريخ', 'الخزينة', 'نوع الحركة', 'الوصف', 'وارد', 'صادر', 'الرصيد بعد'])
    if report:
        for row in report['data']:
            writer.writerow([row['movement_date'], row['treasury_name'], row['movement_type'], row['description'], row['receipt'], row['disbursement'], row['running_balance']])
    return response
