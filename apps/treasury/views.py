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

from .forms import TreasuryForm
from .models import Treasury, TreasuryMovement, TreasuryTransfer
from .reports import REFERENCE_TYPE_AR


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


from apps.core.utils import CURRENCY_SYMBOLS as _CURRENCY_SYMBOLS, currency_symbol as _currency_symbol, enforce_branch_ownership, enforce_transfer_branch_ownership, resolve_report_scope


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


def _visible_treasuries_qs(request, tenant):
    """
    الخزائن التي يديرها هذا المستخدم في شاشة "الخزائن" (جدول القائمة
    الرئيسي) — منفصلة عن other_treasuries (وجهات التحويل)، التي تبقى تشمل
    خزينة الإدارة المركزية دائماً عبر for_branch الحالية (راجع treasury_list).

    مستخدم فرع (view_treasuries): خزائن فرعه فقط، بدون خزينة الإدارة
    المركزية (لا تظهر كصف قابل للتعديل في جدوله — فقط كوجهة تحويل).
    مدير النشاط (view_head_office_treasury فقط): خزينتا الإدارة المركزية
    فقط (محلية + عملة صعبة إن وُجدت).
    مستخدم يملك الصلاحيتين معاً (نادر: عضو في مجموعتي فرع وإدارة): كل شيء،
    بلا استبعاد.
    """
    can_view_branch = request.user.has_perm_key('view_treasuries')
    can_view_head_office = request.user.has_perm_key('view_head_office_treasury')

    qs = Treasury.objects.for_tenant(tenant)
    if can_view_branch:
        qs = qs.for_branch(getattr(request, 'branch', None))
        if not can_view_head_office:
            qs = qs.exclude(is_head_office=True)
        return qs
    return qs.filter(is_head_office=True)


@login_required
@require_any_permission('view_treasuries', 'view_head_office_treasury')
def treasury_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = _visible_treasuries_qs(request, tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    default = qs.filter(is_default=True).count()

    # Auto-create HC treasury if tenant has HC mode but treasury doesn't exist yet
    if tenant.hard_currency_mode and tenant.hard_currency:
        from apps.core.signals import _ensure_hc_treasury, _ensure_head_office_treasuries
        _ensure_hc_treasury(tenant)
        _ensure_head_office_treasuries(tenant)

    # خزينة العملة الصعبة "الخاصة بهذا المستخدم" لبانر التحويل أعلى الصفحة —
    # من qs المفلترة أصلاً (فرعه فقط، أو خزينتا الإدارة المركزية له وحده)
    # لا من كل خزائن الـ tenant، وإلا قد تُختار خزينة العملة الصعبة الخاصة
    # بالإدارة المركزية أو بفرع آخر بالخطأ (نفس فئة الخلل الأصلية).
    hc_treasury = qs.filter(is_hard_currency=True).first()
    other_treasuries = Treasury.objects.for_tenant(tenant).for_branch(getattr(request, 'branch', None)).filter(is_active=True)

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
        'transfer_treasuries': list(other_treasuries.values('id', 'name', 'currency', 'is_hard_currency', 'is_head_office')),
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
@require_any_permission('view_treasuries', 'view_head_office_treasury')
def treasury_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    queryset = _visible_treasuries_qs(request, tenant)
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
@branch_scope_exempt('ينشئ خزينة جديدة تُختم بفرع المنشئ تلقائياً (treasury.branch = request.branch) — لا قراءة لبيانات فرع آخر')
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
        branch = getattr(request, 'branch', None)
        treasury.branch = branch
        treasury.created_by = request.user
        treasury.updated_by = request.user

        if treasury.is_default:
            # مقفول على فرع المنشئ (أو تنانت بأكمله لمستخدم مركزي) — لا يُسقِط
            # علم "افتراضية" عن خزينة فرع آخر عند إنشاء خزينة افتراضية جديدة
            # لفرع مختلف (خطة تنفيذ Enterprise، أُلحقت أثناء تدقيق check_branch_scoping).
            Treasury.objects.for_tenant(tenant).for_branch(branch).filter(is_default=True).update(is_default=False)

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
@require_any_permission('view_treasuries', 'view_head_office_treasury')
def treasury_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)
    enforce_branch_ownership(request, treasury)

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
@require_any_permission('view_treasury_transactions', 'view_head_office_treasury')
def treasury_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)
    enforce_branch_ownership(request, treasury)
    qs = (
        TreasuryMovement.objects.for_tenant(tenant)
        .filter(treasury=treasury)
        .order_by('-id')[:200]
    )

    can_cancel = request.user.has_perm_key('transfer_treasuries') or request.user.has_perm_key('transfer_head_office_treasury')

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
@require_permission('change_treasuries')
def treasury_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=pk)
    if treasury.is_head_office:
        return JsonResponse({'success': False, 'message': 'لا يمكن تعديل خزينة الإدارة المركزية من هنا.'}, status=403)
    enforce_branch_ownership(request, treasury)
    form = TreasuryForm(request.POST, instance=treasury)

    if form.is_valid():
        treasury = form.save(commit=False)
        treasury.updated_by = request.user

        if treasury.is_system_default:
            treasury.is_default = True
            treasury.is_active = True

        if treasury.is_default:
            Treasury.objects.for_tenant(tenant).exclude(pk=treasury.pk).for_branch(treasury.branch).filter(is_default=True).update(is_default=False)

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
    if treasury.is_head_office:
        return JsonResponse({'success': False, 'message': 'لا يمكن حذف خزينة الإدارة المركزية.'}, status=403)
    enforce_branch_ownership(request, treasury)

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
@require_any_permission('transfer_treasuries', 'transfer_head_office_treasury')
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
    # لا يُشترط أن to_treasury يخص فرع المستخدم — إرسال أموال لفرع آخر عملية
    # مشروعة (كالتحويل المخزني بين الفروع)؛ لكن لا يمكنه السحب من خزينة لا
    # يملكها (from_treasury تحديداً).
    enforce_branch_ownership(request, from_treasury)

    # مستخدم لا يملك صلاحية تحويل خزائن الفروع (فقط صلاحية خزينة الإدارة
    # المركزية — مدير النشاط عادةً) لا يبدأ التحويل إلا من خزينته المركزية
    # هو نفسها، لا من خزينة فرع مباشرة.
    if not request.user.has_perm_key('transfer_treasuries') and not from_treasury.is_head_office:
        return JsonResponse({'success': False, 'message': 'لا يمكنك التحويل إلا من خزينة الإدارة المركزية.'}, status=403)

    # لا تحويل مباشر بين فرعين — أي تواصل مالي بين الفروع يمر عبر خزينة
    # الإدارة المركزية فقط (قرار منتج: راجع خطة "خزينة الإدارة المركزية").
    if from_treasury.branch_id and to_treasury.branch_id and from_treasury.branch_id != to_treasury.branch_id:
        return JsonResponse({'success': False, 'message': 'التحويل المباشر بين الفروع غير مسموح — حوّل عبر الخزينة المركزية للإدارة.'}, status=400)

    # طرف واحد فقط من نوع "إدارة مركزية" (فرع ↔ إدارة) — القيد الصارم
    # (تطابق العملة، بلا سعر صرف) لا يطبَّق إطلاقاً لو كان الطرفان معاً
    # للإدارة المركزية (تحويل داخلي لمدير النشاط بين خزينته المحلية وخزينة
    # العملة الصعبة — نفس تحويل التحويل العادي داخل أي فرع، بسعر صرف).
    involves_head_office = from_treasury.is_head_office != to_treasury.is_head_office
    if involves_head_office:
        from_currency = from_treasury.currency or tenant.currency
        to_currency = to_treasury.currency or tenant.currency
        if from_currency != to_currency:
            return JsonResponse({'success': False, 'message': 'يجب أن تكون عملة الخزينتين متطابقة عند التحويل مع الخزينة المركزية للإدارة.'}, status=400)
        # لا تحويل عملة هنا — مبلغ واحد بلا سعر صرف، بصرف النظر عمّا أُرسل.
        exchange_rate = Decimal('1')
        to_amount = from_amount

    # ── قيد: التحويل يجب أن يشمل خزينة العملة الصعبة عندها ──
    hc_treasuries = {from_treasury.is_hard_currency, to_treasury.is_hard_currency}
    if not involves_head_office and True in hc_treasuries and exchange_rate <= 0:
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


@login_required
@require_any_permission('transfer_treasuries', 'transfer_head_office_treasury')
@require_POST
def treasury_transfer_cancel_api(request, pk):
    """إلغاء موثّق لتحويل قائم — يسجّل حركتين عكسيتين جديدتين، لا يحذف الأصل."""
    from .services import cancel_treasury_transfer

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    transfer = get_object_or_404(TreasuryTransfer.objects.for_tenant(tenant), pk=pk)
    # فحص مخصَّص (لا enforce_branch_ownership متعدد المسارات — يتجاوز الفحص
    # لو أي طرف NULL، وهنا الإدارة المركزية دائماً NULL بالتصميم لا بالمصادفة).
    enforce_transfer_branch_ownership(request, transfer.from_treasury.branch_id, transfer.to_treasury.branch_id)

    try:
        cancel_treasury_transfer(transfer, user=request.user)
    except ValueError as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)

    log_activity(
        request, 'إلغاء تحويل بين الخزائن',
        f'من: {transfer.from_treasury.name} ({transfer.from_amount}) → إلى: {transfer.to_treasury.name} ({transfer.to_amount})',
        'cancel',
    )
    return JsonResponse({'success': True, 'message': 'تم إلغاء التحويل بنجاح'})


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
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل TreasuryReportGenerator عبر self.branch (راجع apps/treasury/reports.py)')
def treasury_balances_report(request):
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    report = TreasuryReportGenerator(tenant, branch=branch).get_balances_report()

    return render(request, 'treasury/reports/balances.html', {
        'report': report,
        'section': 'treasury_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
    })


@login_required
@require_permission('view_treasury_balances_report')
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل TreasuryReportGenerator عبر self.branch (راجع apps/treasury/reports.py)')
def treasury_balances_report_export(request):
    import csv
    from django.http import HttpResponse
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    report = TreasuryReportGenerator(tenant, branch=branch).get_balances_report()
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
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    treasury_id = request.GET.get('treasury_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    branch = branch
    gen = TreasuryReportGenerator(tenant, start_date, end_date, branch=branch)
    report = gen.get_statement_report(treasury_id) if treasury_id else None
    treasuries = Treasury.objects.filter(tenant=tenant, is_active=True).for_branch(branch).order_by('name')

    return render(request, 'treasury/reports/statement.html', {
        'report': report,
        'treasuries': treasuries,
        'selected_treasury_id': treasury_id,
        'start_date': start_date,
        'end_date': end_date,
        'section': 'treasury_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
    })


@login_required
@require_permission('view_treasury_statement_report')
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل TreasuryReportGenerator.get_statement_report عبر self.branch')
def treasury_statement_report_export(request):
    import csv
    from datetime import timedelta
    from django.http import HttpResponse
    from django.utils import timezone
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    treasury_id = request.GET.get('treasury_id')
    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()

    report = TreasuryReportGenerator(tenant, start_date, end_date, branch=branch).get_statement_report(treasury_id) if treasury_id else None
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
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    treasury_id = request.GET.get('treasury_id') or None

    branch = branch
    report = TreasuryReportGenerator(tenant, start_date, end_date, branch=branch).get_movements_summary(treasury_id=treasury_id) if treasury_id else None
    treasuries = Treasury.objects.filter(tenant=tenant, is_active=True).for_branch(branch).order_by('name')

    return render(request, 'treasury/reports/movements.html', {
        'report': report,
        'start_date': start_date,
        'end_date': end_date,
        'treasuries': treasuries,
        'selected_treasury_id': treasury_id or '',
        'section': 'treasury_reports',
        'is_central_admin': is_central_admin,
        'branches_for_filter': branches_for_filter,
        'selected_branch': branch,
    })


@login_required
@require_permission('view_treasury_movements_report')
@branch_scope_exempt('الفلترة حسب الفرع تتم داخل TreasuryReportGenerator.get_movements_summary عبر filter_by_branch_via')
def treasury_movements_report_export(request):
    import csv
    from datetime import timedelta
    from django.http import HttpResponse
    from django.utils import timezone
    from .reports import TreasuryReportGenerator
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    branch, is_central_admin, branches_for_filter = resolve_report_scope(request)

    start_date = _parse_date(request.GET.get('start_date')) or (timezone.localdate() - timedelta(days=30))
    end_date = _parse_date(request.GET.get('end_date')) or timezone.localdate()
    treasury_id = request.GET.get('treasury_id') or None

    report = TreasuryReportGenerator(tenant, start_date, end_date, branch=branch).get_movements_summary(treasury_id=treasury_id) if treasury_id else None
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="treasury_movements_{end_date}.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['التاريخ', 'الخزينة', 'نوع الحركة', 'الوصف', 'وارد', 'صادر', 'الرصيد بعد'])
    if report:
        for row in report['data']:
            writer.writerow([row['movement_date'], row['treasury_name'], row['movement_type'], row['description'], row['receipt'], row['disbursement'], row['running_balance']])
    return response
