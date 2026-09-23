import json
from decimal import Decimal, InvalidOperation

from apps.accounts.activity_service import log_activity
from django.contrib.auth.decorators import login_required
from apps.accounts.decorators import require_permission, require_any_permission, branch_scope_exempt
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_tz
from django.views.decorators.http import require_POST

from apps.treasury.models import Treasury

from .forms import BankAccountForm
from .models import BankAccount, BankAccountMovement, BankAccountTransfer
from .reports import REFERENCE_TYPE_AR


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


from apps.core.utils import CURRENCY_SYMBOLS as _CURRENCY_SYMBOLS, currency_symbol as _currency_symbol, enforce_branch_ownership, enforce_transfer_branch_ownership, resolve_report_scope


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


def _visible_bank_accounts_qs(request, tenant):
    """نفس منطق _visible_treasuries_qs (apps/treasury/views.py) لكن للحسابات البنكية."""
    can_view_branch = request.user.has_perm_key('view_bank_accounts')
    can_view_head_office = request.user.has_perm_key('view_head_office_bank_accounts')

    qs = BankAccount.objects.for_tenant(tenant)
    if can_view_branch:
        qs = qs.for_branch(getattr(request, 'branch', None))
        if not can_view_head_office:
            qs = qs.exclude(is_head_office=True)
        return qs
    return qs.filter(is_head_office=True)


@login_required
@require_any_permission('view_bank_accounts', 'view_head_office_bank_accounts')
def bank_account_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = _visible_bank_accounts_qs(request, tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    default = qs.filter(is_default=True).count()

    local_cur = tenant.currency or 'SDG'
    treasuries = Treasury.objects.for_tenant(tenant).for_branch(getattr(request, 'branch', None)).filter(is_active=True)
    # وجهات التحويل (منفصلة عن qs الجدول الرئيسي): تشمل حساب الإدارة المركزية
    # دائماً كوجهة، حتى لو استُبعد من جدول "حساباتي" — نفس منطق other_treasuries
    # في apps/treasury/views.py.
    transfer_accounts_qs = BankAccount.objects.for_tenant(tenant).for_branch(getattr(request, 'branch', None)).filter(is_active=True)

    context = {
        'form': BankAccountForm(),
        'today': dj_tz.localdate().isoformat(),
        'local_currency': local_cur,
        'local_currency_symbol': _currency_symbol(local_cur),
        'transfer_bank_accounts': list(transfer_accounts_qs.values('id', 'name', 'currency', 'is_head_office')),
        'transfer_treasuries': list(treasuries.values('id', 'name', 'currency')),
        'currency_symbols_json': {k: v for k, v in _CURRENCY_SYMBOLS.items()},
        'stats': {
            'total': total,
            'active': active,
            'inactive': total - active,
            'default': default,
        },
    }
    return render(request, 'bank_accounts/bank_account_list.html', context)


@login_required
@require_any_permission('view_bank_accounts', 'view_head_office_bank_accounts')
def bank_account_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    queryset = _visible_bank_accounts_qs(request, tenant)
    records_total = queryset.count()

    if status == 'active':
        queryset = queryset.filter(is_active=True)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False)

    if search_value:
        queryset = queryset.filter(
            Q(name__icontains=search_value)
            | Q(bank_name__icontains=search_value)
            | Q(account_number__icontains=search_value)
            | Q(iban__icontains=search_value)
            | Q(notes__icontains=search_value)
        )

    records_filtered = queryset.count()

    order_column_index = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    order_column_name = request.GET.get(f'columns[{order_column_index}][data]', 'created_at')

    allowed_order_fields = {
        'name': 'name',
        'bank_name': 'bank_name',
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
            'id': account.id,
            'name': account.name,
            'bank_name': account.bank_name or '—',
            'account_number': account.account_number or '—',
            'iban': account.iban or '—',
            'current_balance': str(account.current_balance),
            'currency': account.currency or local_currency,
            'is_active': account.is_active,
            'is_default': account.is_default,
        }
        for account in queryset
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
@require_any_permission('add_bank_accounts', 'add_head_office_bank_accounts')
@branch_scope_exempt('ينشئ حساباً بنكياً جديداً يُختم بفرع المنشئ تلقائياً — لا قراءة لبيانات فرع آخر')
def bank_account_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = BankAccountForm(request.POST)
    if form.is_valid():
        account = form.save(commit=False)
        account.tenant = tenant
        branch = getattr(request, 'branch', None)
        account.branch = branch
        # مستخدم بلا صلاحية إدارة حسابات الفروع (فقط صلاحية الحسابات
        # المركزية — مدير النشاط) ينشئ دائماً حساب إدارة مركزية.
        if not request.user.has_perm_key('add_bank_accounts'):
            account.is_head_office = True
        account.created_by = request.user
        account.updated_by = request.user

        if account.is_default:
            BankAccount.objects.for_tenant(tenant).for_branch(branch).filter(is_default=True).update(is_default=False)

        account.save()

        ob_amount = request.POST.get('opening_balance', '').strip()
        ob_date = request.POST.get('opening_balance_date', '').strip()
        if ob_amount:
            try:
                ob_amount_dec = Decimal(ob_amount)
                if ob_amount_dec > 0:
                    from .services import set_opening_balance
                    import datetime
                    if not ob_date:
                        ob_date = dj_tz.localdate().isoformat()
                    set_opening_balance(
                        tenant, account,
                        amount=ob_amount_dec,
                        date=datetime.date.fromisoformat(ob_date),
                        user=request.user,
                    )
            except Exception:
                pass

        log_activity(request, 'إضافة حساب بنكي جديد',
                     f"الحساب: {account.name}", 'create')

        return JsonResponse({
            'success': True,
            'message': 'تم إضافة الحساب البنكي بنجاح',
            'id': account.id,
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
@require_any_permission('view_bank_accounts', 'view_head_office_bank_accounts')
def bank_account_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    account = get_object_or_404(BankAccount.objects.for_tenant(tenant), pk=pk)
    enforce_branch_ownership(request, account)

    ob_mv = BankAccountMovement.objects.filter(
        bank_account=account, reference_type='opening_balance'
    ).first()

    return JsonResponse({
        'success': True,
        'data': {
            'id': account.id,
            'name': account.name,
            'bank_name': account.bank_name,
            'account_number': account.account_number,
            'iban': account.iban,
            'notes': account.notes,
            'is_active': account.is_active,
            'is_default': account.is_default,
            'current_balance': str(account.current_balance),
            'opening_balance': str(ob_mv.amount) if ob_mv else '0',
            'opening_balance_date': ob_mv.movement_date.isoformat() if ob_mv else '',
        }
    })


@login_required
@require_any_permission('view_bank_account_transactions', 'view_head_office_bank_accounts')
def bank_account_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    account = get_object_or_404(BankAccount.objects.for_tenant(tenant), pk=pk)
    enforce_branch_ownership(request, account)
    qs = (
        BankAccountMovement.objects.for_tenant(tenant)
        .filter(bank_account=account)
        .order_by('-id')[:200]
    )

    can_cancel = request.user.has_perm_key('transfer_bank_accounts') or request.user.has_perm_key('transfer_head_office_bank_accounts')

    def _transfer_info(movement):
        transfer = getattr(movement, 'transfer_as_source', None) or getattr(movement, 'transfer_as_dest', None)
        if not transfer:
            return None, False
        return transfer.id, transfer.is_cancelled

    data = []
    for m in qs:
        transfer_id, is_cancelled = _transfer_info(m) if m.reference_type in ('transfer', 'transfer_cancel') else (None, False)
        data.append({
            'id': m.id,
            'movement_date': m.movement_date.isoformat(),
            'movement_type': m.get_movement_type_display(),
            'movement_type_key': m.movement_type,
            'amount': str(m.amount),
            'running_balance': str(m.running_balance),
            'reference_type': REFERENCE_TYPE_AR.get(m.reference_type, m.reference_type) if m.reference_type else '',
            'description': m.description or '',
            'transfer_id': transfer_id if (transfer_id and can_cancel and m.reference_type == 'transfer' and not is_cancelled) else None,
        })

    return JsonResponse({'success': True, 'data': data})


@login_required
@require_any_permission('change_bank_accounts', 'change_head_office_bank_accounts')
def bank_account_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    account = get_object_or_404(BankAccount.objects.for_tenant(tenant), pk=pk)
    if account.is_head_office and not request.user.has_perm_key('change_head_office_bank_accounts'):
        return JsonResponse({'success': False, 'message': 'لا يمكنك تعديل حساب الإدارة المركزية.'}, status=403)
    if not account.is_head_office and not request.user.has_perm_key('change_bank_accounts'):
        return JsonResponse({'success': False, 'message': 'ليس لديك صلاحية تعديل حسابات الفروع.'}, status=403)
    enforce_branch_ownership(request, account)
    form = BankAccountForm(request.POST, instance=account)

    if form.is_valid():
        account = form.save(commit=False)
        account.updated_by = request.user

        if account.is_default:
            BankAccount.objects.for_tenant(tenant).exclude(pk=account.pk).for_branch(account.branch).filter(is_default=True).update(is_default=False)

        account.save()

        ob_amount = request.POST.get('opening_balance', '').strip()
        ob_date = request.POST.get('opening_balance_date', '').strip()
        try:
            ob_amount_dec = Decimal(ob_amount) if ob_amount else Decimal('0')
            from .services import set_opening_balance
            import datetime
            if not ob_date:
                ob_date = dj_tz.localdate().isoformat()
            set_opening_balance(
                tenant, account,
                amount=ob_amount_dec,
                date=datetime.date.fromisoformat(ob_date),
                user=request.user,
            )
        except Exception:
            pass

        return JsonResponse({
            'success': True,
            'message': 'تم تعديل الحساب البنكي بنجاح',
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
@require_any_permission('delete_bank_accounts', 'delete_head_office_bank_accounts')
def bank_account_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    account = get_object_or_404(BankAccount.objects.for_tenant(tenant), pk=pk)
    if account.is_head_office and not request.user.has_perm_key('delete_head_office_bank_accounts'):
        return JsonResponse({'success': False, 'message': 'لا يمكنك حذف حساب الإدارة المركزية.'}, status=403)
    if not account.is_head_office and not request.user.has_perm_key('delete_bank_accounts'):
        return JsonResponse({'success': False, 'message': 'ليس لديك صلاحية حذف حسابات الفروع.'}, status=403)
    enforce_branch_ownership(request, account)

    if account.is_default:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف الحساب الافتراضي. عيّن حساباً آخر كافتراضي أولاً.'}, status=400)

    if account.movements.exists():
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف حساب بنكي له حركات. قم بإيقافه فقط.'}, status=400)

    account.delete()
    return JsonResponse({'success': True, 'message': 'تم حذف الحساب البنكي بنجاح'})


@login_required
@require_any_permission('transfer_bank_accounts', 'transfer_head_office_bank_accounts')
@require_POST
def bank_account_transfer_api(request):
    """تحويل بين حسابين بنكيين مع سعر صرف — يُنشئ خصماً وإيداعاً تلقائياً."""
    from .services import post_bank_account_transfer

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    try:
        from_id = int(payload['from_bank_account'])
        to_id = int(payload['to_bank_account'])
        from_amount = Decimal(str(payload['from_amount']).replace(',', '.'))
        to_amount = Decimal(str(payload['to_amount']).replace(',', '.'))
        exchange_rate = Decimal(str(payload['exchange_rate']).replace(',', '.'))
        transfer_date = payload['transfer_date']
        notes = str(payload.get('notes', '')).strip()
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return JsonResponse({'success': False, 'message': 'يرجى تعبئة جميع الحقول بشكل صحيح'}, status=400)

    if from_id == to_id:
        return JsonResponse({'success': False, 'message': 'لا يمكن التحويل من الحساب إلى نفسه'}, status=400)
    if from_amount <= 0 or to_amount <= 0:
        return JsonResponse({'success': False, 'message': 'يجب أن تكون المبالغ أكبر من صفر'}, status=400)

    from_account = get_object_or_404(BankAccount.objects.for_tenant(tenant), pk=from_id)
    to_account = get_object_or_404(BankAccount.objects.for_tenant(tenant), pk=to_id)
    enforce_branch_ownership(request, from_account)

    if not request.user.has_perm_key('transfer_bank_accounts') and not from_account.is_head_office:
        return JsonResponse({'success': False, 'message': 'لا يمكنك التحويل إلا من حساب الإدارة المركزية.'}, status=403)

    if from_account.branch_id and to_account.branch_id and from_account.branch_id != to_account.branch_id:
        return JsonResponse({'success': False, 'message': 'التحويل المباشر بين الفروع غير مسموح — حوّل عبر حساب الإدارة المركزية.'}, status=400)

    # طرف واحد فقط للإدارة المركزية — القيد الصارم لا يطبَّق لو كان الحسابان
    # معاً للإدارة المركزية (تحويل داخلي بين حسابين بنكيين لمدير النشاط).
    involves_head_office = from_account.is_head_office != to_account.is_head_office
    if involves_head_office:
        from_currency = from_account.currency or tenant.currency
        to_currency = to_account.currency or tenant.currency
        if from_currency != to_currency:
            return JsonResponse({'success': False, 'message': 'يجب أن تكون عملة الحسابين متطابقة عند التحويل مع حساب الإدارة المركزية.'}, status=400)
        exchange_rate = Decimal('1')
        to_amount = from_amount

    try:
        transfer = post_bank_account_transfer(
            tenant=tenant,
            from_account=from_account,
            to_account=to_account,
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
        request, 'تحويل بين حسابات بنكية',
        f'من: {from_account.name} ({from_amount}) → إلى: {to_account.name} ({to_amount}) | سعر الصرف: {exchange_rate}',
        'create',
    )
    return JsonResponse({
        'success': True,
        'message': f'تم التحويل بنجاح — {from_account.name} ← {to_account.name}',
        'transfer_id': transfer.id,
    })


@login_required
@require_any_permission('transfer_bank_accounts', 'transfer_head_office_bank_accounts')
@require_POST
def bank_account_transfer_cancel_api(request, pk):
    """إلغاء موثّق لتحويل قائم بين حسابين بنكيين — يسجّل حركتين عكسيتين جديدتين."""
    from .services import cancel_bank_account_transfer

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    transfer = get_object_or_404(BankAccountTransfer.objects.for_tenant(tenant), pk=pk)
    # فحص مخصَّص — راجع تعليق enforce_transfer_branch_ownership (apps/core/utils.py)
    # لسبب عدم استخدام enforce_branch_ownership متعدد المسارات هنا.
    enforce_transfer_branch_ownership(request, transfer.from_bank_account.branch_id, transfer.to_bank_account.branch_id)

    try:
        cancel_bank_account_transfer(transfer, user=request.user)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)

    log_activity(
        request, 'إلغاء تحويل بين حسابات بنكية',
        f'من: {transfer.from_bank_account.name} ({transfer.from_amount}) → إلى: {transfer.to_bank_account.name} ({transfer.to_amount})',
        'cancel',
    )
    return JsonResponse({'success': True, 'message': 'تم إلغاء التحويل بنجاح'})


@login_required
@require_permission('transfer_bank_accounts')
@require_POST
def treasury_bank_transfer_api(request):
    """تحويل بين الخزينة وحساب بنكي (بالاتجاهين)."""
    from .services import post_treasury_to_bank_transfer, post_bank_to_treasury_transfer

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    try:
        direction = payload['direction']
        if direction not in ('treasury_to_bank', 'bank_to_treasury'):
            raise ValueError
        treasury_id = int(payload['treasury_id'])
        bank_account_id = int(payload['bank_account_id'])
        amount = Decimal(str(payload['amount']).replace(',', '.'))
        transfer_date = payload['transfer_date']
        notes = str(payload.get('notes', '')).strip()
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return JsonResponse({'success': False, 'message': 'يرجى تعبئة جميع الحقول بشكل صحيح'}, status=400)

    if amount <= 0:
        return JsonResponse({'success': False, 'message': 'يجب أن يكون المبلغ أكبر من صفر'}, status=400)

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=treasury_id)
    bank_account = get_object_or_404(BankAccount.objects.for_tenant(tenant), pk=bank_account_id)
    # يتحقق من ملكية طرف "المصدر" فقط حسب الاتجاه — نفس منطق تحويل الخزائن/الحسابات
    enforce_branch_ownership(request, treasury if direction == 'treasury_to_bank' else bank_account)

    try:
        if direction == 'treasury_to_bank':
            transfer = post_treasury_to_bank_transfer(
                tenant=tenant, treasury=treasury, bank_account=bank_account,
                amount=amount, transfer_date=transfer_date, notes=notes, user=request.user,
            )
            msg = f'تم تحويل {amount} من الخزينة {treasury.name} إلى الحساب البنكي {bank_account.name}'
        else:
            transfer = post_bank_to_treasury_transfer(
                tenant=tenant, bank_account=bank_account, treasury=treasury,
                amount=amount, transfer_date=transfer_date, notes=notes, user=request.user,
            )
            msg = f'تم تحويل {amount} من الحساب البنكي {bank_account.name} إلى الخزينة {treasury.name}'
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e) or 'تعذر تنفيذ التحويل'}, status=400)

    log_activity(request, 'تحويل بين الخزينة وحساب بنكي', msg, 'create')
    return JsonResponse({'success': True, 'message': msg, 'transfer_id': transfer.id})


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
@require_permission('view_bank_account_balances_report')
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل BankAccountReportGenerator عبر self.branch')
def bank_account_balances_report(request):
    from .reports import BankAccountReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    report = BankAccountReportGenerator(tenant, branch=branch).get_balances_report()

    return render(request, 'bank_accounts/reports/balances.html', {
        'report': report,
        'section': 'bank_account_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
    })


@login_required
@require_permission('view_bank_account_balances_report')
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل BankAccountReportGenerator عبر self.branch')
def bank_account_balances_report_export(request):
    import csv
    from django.http import HttpResponse
    from .reports import BankAccountReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    report = BankAccountReportGenerator(tenant, branch=branch).get_balances_report()
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="bank_account_balances.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الحساب البنكي', 'البنك', 'الرصيد الحالي'])
    for row in report['data']:
        writer.writerow([row['name'], row['bank_name'], row['current_balance']])
    return response


@login_required
@require_permission('view_bank_account_statement_report')
def bank_account_statement_report(request):
    from datetime import timedelta
    from django.utils import timezone
    from .reports import BankAccountReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    bank_account_id = request.GET.get('bank_account_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    branch = branch
    gen = BankAccountReportGenerator(tenant, start_date, end_date, branch=branch)
    report = gen.get_statement_report(bank_account_id) if bank_account_id else None
    accounts = BankAccount.objects.filter(tenant=tenant, is_active=True).for_branch(branch).order_by('name')

    return render(request, 'bank_accounts/reports/statement.html', {
        'report': report,
        'accounts': accounts,
        'selected_bank_account_id': bank_account_id,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'bank_account_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
    })


@login_required
@require_permission('view_bank_account_statement_report')
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل BankAccountReportGenerator.get_statement_report عبر self.branch')
def bank_account_statement_report_export(request):
    import csv
    from datetime import timedelta
    from django.http import HttpResponse
    from django.utils import timezone
    from .reports import BankAccountReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    bank_account_id = request.GET.get('bank_account_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = BankAccountReportGenerator(tenant, start_date, end_date, branch=branch).get_statement_report(bank_account_id) if bank_account_id else None
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="bank_account_statement_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    if report:
        writer.writerow([f'كشف حساب بنكي: {report["bank_account"].name}'])
        writer.writerow([f'الفترة: {start_date} إلى {end_date}'])
        writer.writerow([])
        writer.writerow(['التاريخ', 'نوع الحركة', 'الوصف', 'وارد', 'صادر', 'الرصيد بعد'])
        for row in report['data']:
            writer.writerow([row['movement_date'], row['movement_type'], row['description'], row['receipt'], row['disbursement'], row['running_balance']])
    return response


@login_required
@require_permission('view_bank_account_movements_report')
def bank_account_movements_report(request):
    from datetime import timedelta
    from django.utils import timezone
    from .reports import BankAccountReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    bank_account_id = request.GET.get('bank_account_id') or None

    branch = branch
    report = BankAccountReportGenerator(tenant, start_date, end_date, branch=branch).get_movements_summary(bank_account_id=bank_account_id) if bank_account_id else None
    accounts = BankAccount.objects.filter(tenant=tenant, is_active=True).for_branch(branch).order_by('name')

    return render(request, 'bank_accounts/reports/movements.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'accounts': accounts,
        'selected_bank_account_id': bank_account_id or '',
        'section': 'bank_account_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
    })


@login_required
@require_permission('view_bank_account_movements_report')
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل BankAccountReportGenerator.get_movements_summary عبر filter_by_branch_via')
def bank_account_movements_report_export(request):
    import csv
    from datetime import timedelta
    from django.http import HttpResponse
    from django.utils import timezone
    from .reports import BankAccountReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    bank_account_id = request.GET.get('bank_account_id') or None

    report = BankAccountReportGenerator(tenant, start_date, end_date, branch=branch).get_movements_summary(bank_account_id=bank_account_id) if bank_account_id else None
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="bank_account_movements_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['التاريخ', 'الحساب البنكي', 'نوع الحركة', 'الوصف', 'وارد', 'صادر', 'الرصيد بعد'])
    if report:
        for row in report['data']:
            writer.writerow([row['movement_date'], row['bank_account_name'], row['movement_type'], row['description'], row['receipt'], row['disbursement'], row['running_balance']])
    return response
