from apps.accounts.activity_service import log_activity
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import DecimalField, Exists, OuterRef, Q, Sum, Value
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from decimal import Decimal
import csv
import json

from apps.accounts.decorators import require_permission
from .forms import AgentForm
from .models import Agent, AgentLedger
from .services import _apply_agent_ledger, agent_ledger_display_label
from apps.treasury.models import Treasury, TreasuryMovement
from apps.treasury.services import post_treasury_disbursement


def _ensure_tenant(request):
    tenant = getattr(request, 'tenant', None)
    return tenant or None


def _json_error(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status, json_dumps_params={'ensure_ascii': False})


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data is not None:
        payload['data'] = data
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


def _serialize_form_errors(form):
    return {field: [str(e) for e in errors] for field, errors in form.errors.items()}


def _agent_balance(tenant, agent):
    agg = AgentLedger.objects.filter(tenant=tenant, agent=agent).aggregate(s=Sum('amount'))
    opening = agent.opening_balance or Decimal('0')
    return opening + (agg.get('s') or Decimal('0'))


# ─── List ──────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_agents')
def agent_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    if not tenant.plan_allows('agents'):
        return render(request, 'agents/plan_upgrade.html', {
            'feature': 'إدارة المناديب',
            'required_plan': 'Pro أو Enterprise',
        })

    qs = Agent.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    context = {
        'form': AgentForm(),
        'stats': {'total': total, 'active': active, 'inactive': total - active},
    }
    return render(request, 'agents/agent_list.html', context)


# ─── Table API ─────────────────────────────────────────────────────────────

@login_required
@require_permission('view_agents')
def agent_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw   = int(request.GET.get('draw', 1))
    start  = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    qs = Agent.objects.for_tenant(tenant)
    records_total = qs.count()

    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)

    if search:
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(phone__icontains=search)
            | Q(email__icontains=search)
            | Q(city__icontains=search)
        )

    records_filtered = qs.count()

    order_col   = request.GET.get('order[0][column]', '0')
    order_dir   = request.GET.get('order[0][dir]', 'asc')
    col_name    = request.GET.get(f'columns[{order_col}][data]', 'created_at')
    allowed     = {'code': 'code', 'name': 'name', 'phone': 'phone', 'city': 'city',
                   'commission_type': 'commission_type', 'is_active': 'is_active', 'created_at': 'created_at'}
    order_field = allowed.get(col_name, 'created_at')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    qs = qs.order_by(order_field)[start:start + length]

    data = []
    for agent in qs:
        balance = _agent_balance(tenant, agent)
        commission_label = dict(Agent.COMMISSION_TYPE_CHOICES).get(agent.commission_type, '—')
        commission_basis_label = dict(Agent.COMMISSION_BASIS_CHOICES).get(agent.commission_basis, '—')
        data.append({
            'id':                        agent.id,
            'code':                      agent.code,
            'name':                      agent.name,
            'phone':                     agent.phone or '-',
            'city':                      agent.city or '-',
            'commission_type':           agent.commission_type,
            'commission_label':          commission_label,
            'commission_basis':          agent.commission_basis,
            'commission_basis_label':    commission_basis_label,
            'commission_rate':           str(agent.commission_rate),
            'commission_rate_collection': str(agent.commission_rate_collection),
            'current_balance':           str(balance.quantize(Decimal('0.01'))),
            'is_active':                 agent.is_active,
            'has_user':                  agent.user_id is not None,
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': data,
    })


# ─── Create ────────────────────────────────────────────────────────────────

@login_required
@require_permission('add_agents')
def agent_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = AgentForm(request.POST)
    if form.is_valid():
        agent = form.save(commit=False)
        agent.tenant = tenant
        agent.created_by = request.user
        agent.updated_by = request.user
        agent.save()
        log_activity(request, 'إضافة مندوب جديد',
                     f"المندوب: {agent.name}\nرقم الهاتف: {agent.phone or '—'}", 'create')
        return _json_ok(data={'id': agent.id}, msg='تم إضافة المندوب بنجاح')

    return JsonResponse({'success': False, 'message': 'يرجى التحقق من الحقول المطلوبة',
                         'errors': _serialize_form_errors(form)}, status=400, json_dumps_params={'ensure_ascii': False})


# ─── Detail ────────────────────────────────────────────────────────────────

@login_required
@require_permission('view_agents')
def agent_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    agent = get_object_or_404(Agent.objects.for_tenant(tenant), pk=pk)
    balance = _agent_balance(tenant, agent)

    return _json_ok(data={
        'id':              agent.id,
        'code':            agent.code,
        'name':            agent.name,
        'phone':           agent.phone,
        'email':           agent.email,
        'city':            agent.city,
        'address':         agent.address,
        'commission_type': agent.commission_type,
        'commission_basis': agent.commission_basis,
        'commission_rate': str(agent.commission_rate),
        'commission_rate_collection': str(agent.commission_rate_collection),
        'opening_balance': str(agent.opening_balance),
        'current_balance': str(balance.quantize(Decimal('0.01'))),
        'notes':           agent.notes,
        'is_active':       agent.is_active,
        'has_user':          agent.user_id is not None,
        'username':          agent.user.username if agent.user_id else None,
        'portal_password':   agent.portal_password if agent.user_id else None,
    })


# ─── Transactions ──────────────────────────────────────────────────────────

@login_required
@require_permission('view_agent_transactions')
def agent_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    agent = get_object_or_404(Agent.objects.for_tenant(tenant), pk=pk)
    opening = agent.opening_balance or Decimal('0')

    data = []
    running = opening
    if opening != Decimal('0'):
        entry_date = agent.created_at.date().strftime('%Y-%m-%d') if agent.created_at else ''
        data.append({
            'entry_date':        entry_date,
            'entry_type':        'opening',
            'entry_type_label':  'مستحقات افتتاحية',
            'amount':            str(opening),
            'running_balance':   str(opening),
            'notes':             'مستحقات افتتاحية للمندوب',
            'reference_type':    'agent_opening',
            'reference_id':      agent.id,
            'is_reversal':       False,
        })

    entries = AgentLedger.objects.filter(tenant=tenant, agent=agent).order_by('entry_date', 'id')
    for e in entries:
        running += (e.amount or Decimal('0'))
        data.append({
            'entry_date':       e.entry_date.strftime('%Y-%m-%d') if e.entry_date else '',
            'entry_type':       e.entry_type,
            'entry_type_label': agent_ledger_display_label(e.entry_type, e.reference_type),
            'amount':           str(e.amount),
            'running_balance':  str(running),
            'notes':            e.notes or '—',
            'reference_type':   e.reference_type or '—',
            'reference_id':     e.reference_id,
            'is_reversal':      e.is_reversal,
        })

    data.reverse()
    return JsonResponse({'success': True, 'data': data}, json_dumps_params={'ensure_ascii': False})


# ─── Update ────────────────────────────────────────────────────────────────

@login_required
@require_permission('change_agents')
def agent_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    agent = get_object_or_404(Agent.objects.for_tenant(tenant), pk=pk)
    form = AgentForm(request.POST, instance=agent)
    if form.is_valid():
        agent = form.save(commit=False)
        agent.updated_by = request.user
        agent.save()
        log_activity(request, 'تعديل مندوب', agent.name, 'update')
        return _json_ok(msg='تم تعديل بيانات المندوب بنجاح')

    return JsonResponse({'success': False, 'message': 'يرجى التحقق من الحقول المطلوبة',
                         'errors': _serialize_form_errors(form)}, status=400, json_dumps_params={'ensure_ascii': False})


# ─── Delete ────────────────────────────────────────────────────────────────

@login_required
@require_permission('delete_agents')
def agent_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    agent = get_object_or_404(Agent.objects.for_tenant(tenant), pk=pk)
    name = agent.name
    try:
        agent.delete()
    except ProtectedError:
        return _json_error('لا يمكن حذف المندوب لوجود فواتير أو حركات مرتبطة به.', status=400)
    log_activity(request, 'حذف مندوب', name, 'delete')
    return _json_ok(msg='تم حذف المندوب بنجاح')


@login_required
@require_permission('add_agents')
def agent_create(request):
    return redirect('agents:list')


# ─── Payments list ─────────────────────────────────────────────────────────

@login_required
@require_permission('view_agent_payments')
def agent_payments(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    agents = Agent.objects.for_tenant(tenant).filter(is_active=True).annotate(
        ledger_total=Coalesce(
            Sum('ledger_entries__amount', output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
        ),
    ).order_by('name')

    treasuries = Treasury.objects.for_tenant(tenant).filter(is_active=True, is_hard_currency=False).order_by('name')

    stats_qs = AgentLedger.objects.for_tenant(tenant).filter(entry_type='payment')
    stats = stats_qs.aggregate(
        total=Coalesce(
            Sum('amount', output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
        ),
        cash=Coalesce(
            Sum('amount', filter=Q(reference_type='agent_payment_cash'),
                output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
        ),
        bank=Coalesce(
            Sum('amount', filter=Q(reference_type='agent_payment_bank'),
                output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
        ),
    )

    def pos(v):
        return abs(v) if v is not None else 0

    context = {
        'agents': agents,
        'treasuries': treasuries,
        'stats': {
            'total':        stats_qs.count(),
            'total_amount': pos(stats['total']),
            'cash_amount':  pos(stats['cash']),
            'bank_amount':  pos(stats['bank']),
        },
        'today': timezone.localdate().isoformat(),
    }
    return render(request, 'agents/payment_list.html', context)


# ─── Payments table API ────────────────────────────────────────────────────

@login_required
@require_permission('view_agent_payments')
def agent_payments_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw   = int(request.GET.get('draw', 1))
    start  = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    agent_filter  = request.GET.get('agent_id', '')
    method_filter = request.GET.get('payment_method', '')

    qs = AgentLedger.objects.for_tenant(tenant).filter(entry_type='payment')
    total = qs.count()

    if agent_filter:
        qs = qs.filter(agent_id=agent_filter)
    if method_filter:
        qs = qs.filter(reference_type=f'agent_payment_{method_filter}')
    if search:
        qs = qs.filter(Q(agent__name__icontains=search) | Q(notes__icontains=search))

    filtered_total = qs.count()

    order_col = request.GET.get('order[0][column]', None)
    order_dir = request.GET.get('order[0][dir]', 'desc')
    col_map = {'0': 'entry_date', '1': 'agent__name', '2': 'amount', '3': 'reference_type'}
    order_field = col_map.get(order_col, 'id') if order_col else '-id'
    if order_col and order_dir == 'desc':
        order_field = f'-{order_field}'
    qs = qs.order_by(order_field)

    cancel_qs = AgentLedger.objects.for_tenant(tenant).filter(
        reference_type='agent_payment_cancel', reference_id=OuterRef('pk')
    )
    qs = qs.annotate(is_canceled=Exists(cancel_qs))

    page_qs = qs[start:start + length]
    data = []
    for entry in page_qs:
        method_label = 'نقداً' if entry.reference_type == 'agent_payment_cash' else 'بنكي'
        if entry.is_canceled:
            method_label += ' — ملغاة'
        data.append({
            'id':             entry.id,
            'entry_date':     entry.entry_date.strftime('%Y-%m-%d'),
            'agent':          entry.agent.name,
            'amount':         str(entry.amount),
            'payment_method': method_label,
            'notes':          entry.notes or '—',
            'is_canceled':    bool(entry.is_canceled),
        })

    return JsonResponse({
        'draw': draw, 'recordsTotal': total, 'recordsFiltered': filtered_total, 'data': data,
    }, json_dumps_params={'ensure_ascii': False})


# ─── Payment detail ────────────────────────────────────────────────────────

@login_required
@require_permission('view_agent_payments')
def agent_payment_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    payment = get_object_or_404(
        AgentLedger.objects.for_tenant(tenant).select_related('agent'),
        entry_type='payment', pk=pk,
    )
    cancellation = AgentLedger.objects.for_tenant(tenant).filter(
        reference_type='agent_payment_cancel', reference_id=payment.id,
    ).first()

    cash_treasury = None
    if payment.reference_type == 'agent_payment_cash':
        tm = TreasuryMovement.objects.for_tenant(tenant).filter(
            reference_type='agent_payment_cash', reference_id=payment.id,
        ).select_related('treasury').first()
        if tm:
            cash_treasury = tm.treasury.name

    return _json_ok(data={
        'id':                payment.id,
        'entry_date':        payment.entry_date.strftime('%Y-%m-%d'),
        'agent':             payment.agent.name,
        'amount':            str(payment.amount),
        'payment_method':    'نقداً' if payment.reference_type == 'agent_payment_cash' else 'بنكي',
        'notes':             payment.notes or '—',
        'is_canceled':       bool(cancellation),
        'cancellation_note': cancellation.notes if cancellation else '',
        'cancellation_date': cancellation.entry_date.strftime('%Y-%m-%d') if cancellation else None,
        'cash_treasury':     cash_treasury,
    })


# ─── Payment create ────────────────────────────────────────────────────────

@login_required
@require_permission('add_agent_payments')
@require_POST
def agent_payment_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    if not request.content_type or 'application/json' not in request.content_type:
        return _json_error('بيانات غير صالحة')

    try:
        body       = json.loads(request.body.decode('utf-8') if isinstance(request.body, bytes) else request.body)
        agent_id   = int(body.get('agent_id'))
        amount     = Decimal(str(body.get('amount')))
        pay_date   = body.get('payment_date') or timezone.localdate().isoformat()
        method     = body.get('method', 'cash')
        treasury_id = body.get('treasury_id')
        reference  = str(body.get('reference', '') or '').strip()
        notes      = str(body.get('notes', '') or '').strip()
    except (TypeError, ValueError, json.JSONDecodeError) as e:
        return _json_error(f'بيانات الدفعة غير صالحة: {e}')

    if amount <= 0:
        return _json_error('المبلغ يجب أن يكون أكبر من الصفر')

    agent = get_object_or_404(Agent.objects.for_tenant(tenant), pk=agent_id)
    reference_type = 'agent_payment_bank' if method == 'bank' else 'agent_payment_cash'
    note_text = notes
    if reference:
        note_text = f"{note_text} | مرجع: {reference}" if note_text else f"مرجع: {reference}"
    if not note_text:
        note_text = f'دفعة مندوب'

    try:
        with transaction.atomic():
            payment_entry = _apply_agent_ledger(
                tenant=tenant,
                agent=agent,
                amount=-amount,
                entry_type='payment',
                reference_type=reference_type,
                reference_id=None,
                date=pay_date,
                notes=note_text,
            )

            if method == 'cash':
                if not treasury_id:
                    raise ValueError('يجب اختيار الخزينة عند الدفع نقداً')
                treasury = get_object_or_404(
                    Treasury.objects.for_tenant(tenant).filter(is_hard_currency=False),
                    pk=int(treasury_id),
                )
                movement = post_treasury_disbursement(
                    tenant=tenant,
                    amount=amount,
                    date=pay_date,
                    reference_type='agent_payment_cash',
                    reference_id=payment_entry.id if payment_entry else None,
                    description=f'دفعة مندوب {agent.name}',
                    user=request.user,
                    treasury=treasury,
                )
                if not movement:
                    raise ValueError('تعذر تسجيل حركة الخزينة')
    except ValueError as e:
        return _json_error(str(e))
    except Exception:
        return _json_error('تعذر تسجيل الدفعة، حاول مرة أخرى')

    current_balance = _agent_balance(tenant, agent)
    log_activity(request, 'تسجيل دفعة للمندوب',
                 f"المندوب: {agent.name}\nالمبلغ: {amount}\nطريقة الدفع: {method}", 'create')
    return _json_ok(data={'current_balance': str(current_balance)}, msg='تم تسجيل دفعة المندوب بنجاح')


# ─── Payment cancel ────────────────────────────────────────────────────────

@login_required
@require_permission('cancel_agent_payments')
@require_POST
def agent_payment_cancel_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    payment = get_object_or_404(
        AgentLedger.objects.for_tenant(tenant).filter(entry_type='payment'), pk=pk,
    )
    reverse_notes = f"إلغاء دفعة مندوب — {payment.notes or ''}".strip()

    with transaction.atomic():
        if payment.reference_type == 'agent_payment_cash':
            tm = TreasuryMovement.objects.for_tenant(tenant).filter(
                reference_type='agent_payment_cash', reference_id=payment.id,
            ).first()
            if tm:
                from apps.treasury.services import post_treasury_receipt
                post_treasury_receipt(
                    tenant=tenant,
                    amount=abs(payment.amount),
                    date=timezone.localdate(),
                    reference_type='agent_payment_cash_cancel',
                    reference_id=payment.id,
                    description=f'إلغاء دفعة مندوب {payment.agent.name}',
                    user=request.user,
                    treasury=tm.treasury,
                )

        _apply_agent_ledger(
            tenant=tenant,
            agent=payment.agent,
            amount=-payment.amount,
            entry_type='adjustment',
            reference_type='agent_payment_cancel',
            reference_id=payment.id,
            date=timezone.localdate(),
            notes=reverse_notes,
        )

    log_activity(request, 'إلغاء دفعة مندوب', payment.agent.name, 'delete')
    return _json_ok(msg='تم إلغاء الدفعة وتحديث مستحقات المندوب')


# ─── Export ────────────────────────────────────────────────────────────────

@login_required
@require_permission('export_agents')
def agent_export_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="agents.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الاسم', 'الكود', 'الهاتف', 'البريد', 'المدينة', 'نوع العمولة', 'أساس العمولة', 'معدل العمولة', 'معدل عمولة التحصيل', 'الملاحظات', 'نشط'])
    for agent in Agent.objects.for_tenant(tenant).order_by('name'):
        writer.writerow([
            agent.name, agent.code, agent.phone or '', agent.email or '',
            agent.city or '', agent.get_commission_type_display(),
            agent.get_commission_basis_display(),
            agent.commission_rate, agent.commission_rate_collection, agent.notes or '',
            'نعم' if agent.is_active else 'لا',
        ])
    return response


# ─── Import ────────────────────────────────────────────────────────────────

_AGENT_FIELD_SCHEMA = [
    {'field': 'name',            'description': 'اسم المندوب أو الوكيل', 'required': True},
    {'field': 'phone',           'description': 'رقم الهاتف أو الجوال'},
    {'field': 'email',           'description': 'البريد الإلكتروني'},
    {'field': 'city',            'description': 'المدينة أو المنطقة'},
    {'field': 'address',         'description': 'العنوان التفصيلي'},
    {'field': 'opening_balance', 'description': 'المستحقات الافتتاحية أو رصيد البداية'},
    {'field': 'notes',           'description': 'ملاحظات'},
]


@login_required
@require_permission('import_agents')
def agent_import_api(request):
    from apps.core.io_utils import parse_uploaded_file, smart_get, safe_decimal, clean_phone, clean_email
    from apps.ai.services import smart_map_headers

    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    if 'file' not in request.FILES:
        return _json_error('لم يتم رفع أي ملف')

    rows, err = parse_uploaded_file(request.FILES['file'])
    if err:
        return _json_error(err)
    if not rows:
        return _json_error('الملف فارغ أو لا يحتوي على بيانات')

    headers = list(rows[0].keys())
    try:
        mapping = smart_map_headers(headers, _AGENT_FIELD_SCHEMA)
    except Exception:
        mapping = {}

    imported, errors = 0, []
    for row_num, row in enumerate(rows, start=2):
        try:
            name = smart_get(row, 'name', mapping, 'الاسم', 'اسم المندوب', 'المندوب', 'name')
            if not name:
                errors.append(f'الصف {row_num}: اسم المندوب مطلوب')
                continue
            Agent.objects.create(
                tenant=tenant,
                name=name,
                phone=clean_phone(smart_get(row, 'phone', mapping, 'الهاتف', 'الجوال', 'phone')) or '',
                email=clean_email(smart_get(row, 'email', mapping, 'البريد', 'email')) or '',
                city=smart_get(row, 'city', mapping, 'المدينة', 'city') or '',
                address=smart_get(row, 'address', mapping, 'العنوان', 'address') or '',
                opening_balance=safe_decimal(smart_get(row, 'opening_balance', mapping,
                    'المستحقات الافتتاحية', 'الرصيد الافتتاحي', 'opening_balance', default='0')),
                notes=smart_get(row, 'notes', mapping, 'الملاحظات', 'ملاحظات', 'notes') or '',
                is_active=True,
            )
            imported += 1
        except Exception as e:
            errors.append(f'الصف {row_num}: {e}')

    message = f'تم استيراد {imported} مندوب بنجاح'
    if errors:
        message += f'. حدثت {len(errors)} أخطاء'
    return JsonResponse({'success': True, 'message': message, 'imported': imported, 'errors': errors[:10]},
                        json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('import_agents')
def download_template(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="agents_template.csv"'
    response.write('﻿')
    writer = csv.writer(response)
    writer.writerow(['الاسم', 'الهاتف', 'البريد', 'المدينة', 'العنوان', 'المستحقات الافتتاحية'])
    writer.writerow(['أحمد محمد', '0912345678', 'ahmed@example.com', 'الخرطوم', 'شارع النيل', '0'])
    return response


# ─────────────────────────────────────────────
#   كشف حساب المندوب
# ─────────────────────────────────────────────

@login_required
@require_permission('view_agent_statement_report')
def agent_statement(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    from datetime import timedelta
    agent_id = request.GET.get('agent_id', '')
    start_date = request.GET.get('start_date') or (timezone.localdate() - timedelta(days=30)).isoformat()
    end_date = request.GET.get('end_date') or timezone.localdate().isoformat()

    agents = Agent.objects.filter(tenant=tenant, is_active=True).order_by('name')
    report = None

    if agent_id:
        try:
            agent = Agent.objects.get(pk=agent_id, tenant=tenant)
        except Agent.DoesNotExist:
            agent = None

        if agent:
            # Opening balance = running_balance of last entry before start_date
            pre_entry = AgentLedger.objects.filter(
                tenant=tenant, agent=agent,
                entry_date__lt=start_date,
            ).order_by('entry_date', 'id').last()
            if pre_entry:
                opening_balance = float(pre_entry.running_balance)
            else:
                opening_balance = float(agent.opening_balance or 0)

            entries = list(AgentLedger.objects.filter(
                tenant=tenant,
                agent=agent,
                entry_date__gte=start_date,
                entry_date__lte=end_date,
            ).order_by('entry_date', 'id'))
            for e in entries:
                e.display_label = agent_ledger_display_label(e.entry_type, e.reference_type)

            total_debit = sum(float(e.amount) for e in entries if float(e.amount) > 0)
            total_credit = abs(sum(float(e.amount) for e in entries if float(e.amount) < 0))
            last_entry = entries[-1] if entries else None
            closing_balance = float(last_entry.running_balance) if last_entry else opening_balance

            report = {
                'agent': agent,
                'period': {'start': start_date, 'end': end_date},
                'summary': {
                    'opening_balance': opening_balance,
                    'total_debit': total_debit,
                    'total_credit': total_credit,
                    'closing_balance': closing_balance,
                },
                'opening_balance': opening_balance,
                'entries': entries,
            }

    return render(request, 'agents/statement.html', {
        'agents': agents,
        'selected_agent_id': agent_id,
        'start_date': start_date,
        'end_date': end_date,
        'report': report,
    })


# ─────────────────────────────────────────────
#   مستحقات المناديب
# ─────────────────────────────────────────────

@login_required
@require_permission('view_agent_balances_report')
def agent_balances(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    agents = Agent.objects.filter(tenant=tenant).order_by('name')
    rows = []
    total_dues = Decimal('0')
    for agent in agents:
        bal = _agent_balance(tenant, agent)
        rows.append({'agent': agent, 'balance': bal})
        if bal > 0:
            total_dues += bal

    return render(request, 'agents/balances.html', {
        'rows': rows,
        'total_dues': total_dues,
    })


# ═════════════════════════════════════════════════════
#   إنشاء / ربط مستخدم بالمندوب
# ═════════════════════════════════════════════════════

@login_required
@require_permission('change_agents')
@require_POST
def agent_create_user_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    agent = get_object_or_404(Agent, pk=pk, tenant=tenant)

    if agent.user_id:
        return _json_error('المندوب مرتبط بمستخدم بالفعل')

    from django.contrib.auth import get_user_model
    import secrets, string

    User = get_user_model()

    base = agent.code.lower().replace('-', '')
    username = base
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f'{base}{counter}'
        counter += 1

    alphabet = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(alphabet) for _ in range(12))

    user = User.objects.create_user(
        username=username,
        password=password,
        first_name=agent.name,
        email=agent.email or '',
        is_active=True,
        is_staff=False,
    )
    user.tenant = tenant
    user.save(update_fields=['tenant'])

    agent.user = user
    agent.portal_password = password
    agent.save(update_fields=['user', 'portal_password', 'updated_at'])

    log_activity(request, 'إنشاء حساب مستخدم للمندوب',
                 f"المندوب: {agent.name}\nاسم المستخدم: {username}", 'create')

    return _json_ok(data={'username': username, 'password': password}, msg='تم إنشاء حساب المندوب بنجاح')


@login_required
@require_permission('change_agents')
@require_POST
def agent_reset_password_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    agent = get_object_or_404(Agent, pk=pk, tenant=tenant)
    if not agent.user_id:
        return _json_error('لا يوجد حساب مرتبط بهذا المندوب')

    import secrets, string
    alphabet = string.ascii_letters + string.digits
    password = ''.join(secrets.choice(alphabet) for _ in range(12))
    agent.user.set_password(password)
    agent.user.save(update_fields=['password'])
    agent.portal_password = password
    agent.save(update_fields=['portal_password', 'updated_at'])

    log_activity(request, 'إعادة ضبط كلمة مرور المندوب',
                 f"المندوب: {agent.name}\nاسم المستخدم: {agent.user.username}", 'update')

    return _json_ok(data={'username': agent.user.username, 'password': password},
                    msg='تم إعادة ضبط كلمة المرور بنجاح')


# ═════════════════════════════════════════════════════
#   AGENT PORTAL — مصادقة وجلسة
# ═════════════════════════════════════════════════════

_SESS_AGENT  = 'agent_portal_id'
_SESS_TENANT = 'agent_portal_tenant_id'


def _portal_required(view_fn):
    """Decorator: ensures agent is logged into portal."""
    from functools import wraps
    @wraps(view_fn)
    def wrapper(request, *args, **kwargs):
        if not request.session.get(_SESS_AGENT):
            return redirect('agents:portal_login')
        return view_fn(request, *args, **kwargs)
    return wrapper


def _get_portal_ctx(request):
    """Return (agent, tenant) from portal session, or (None, None)."""
    from apps.core.models import Tenant
    aid = request.session.get(_SESS_AGENT)
    tid = request.session.get(_SESS_TENANT)
    if not aid or not tid:
        return None, None
    try:
        tenant = Tenant.objects.get(pk=tid)
        agent  = Agent.objects.get(pk=aid, tenant=tenant, is_active=True)
        return agent, tenant
    except Exception:
        return None, None


def agent_portal_login(request):
    from django.contrib.auth import authenticate
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user and hasattr(user, 'agent_profile') and user.agent_profile:
            agent = user.agent_profile
            if agent.is_active and agent.tenant_id:
                request.session[_SESS_AGENT]  = agent.pk
                request.session[_SESS_TENANT] = agent.tenant_id
                request.session.set_expiry(60 * 60 * 24 * 30)
                return redirect('agents:portal_dashboard')
        error = 'اسم المستخدم أو كلمة المرور غير صحيحة'

    return render(request, 'agents/portal/login.html', {'error': error})


def agent_portal_logout(request):
    request.session.pop(_SESS_AGENT,  None)
    request.session.pop(_SESS_TENANT, None)
    return redirect('agents:portal_login')


# ═════════════════════════════════════════════════════
#   AGENT PORTAL — الصفحات
# ═════════════════════════════════════════════════════

@_portal_required
def agent_portal_dashboard(request):
    agent, tenant = _get_portal_ctx(request)
    if not agent:
        return redirect('agents:portal_login')

    from apps.sales.models import SaleInvoice
    from .models import AgentInvoiceRequest

    invoices = SaleInvoice.objects.filter(tenant=tenant, agent=agent).exclude(status='cancelled')
    total_invoices = invoices.count()
    balance = _agent_balance(tenant, agent)

    pending_requests = AgentInvoiceRequest.objects.filter(
        tenant=tenant, agent=agent, status='pending'
    ).count()

    return render(request, 'agents/portal/dashboard.html', {
        'agent': agent, 'tenant': tenant,
        'total_invoices': total_invoices,
        'balance': balance,
        'pending_requests': pending_requests,
    })


@_portal_required
def agent_portal_invoices(request):
    agent, tenant = _get_portal_ctx(request)
    if not agent:
        return redirect('agents:portal_login')

    from apps.sales.models import SaleInvoice
    invoices = (
        SaleInvoice.objects
        .filter(tenant=tenant, agent=agent)
        .exclude(status='cancelled')
        .select_related('customer')
        .order_by('-invoice_date', '-id')
    )
    return render(request, 'agents/portal/invoices.html', {
        'agent': agent, 'tenant': tenant, 'invoices': invoices,
    })


@_portal_required
def agent_portal_statement(request):
    agent, tenant = _get_portal_ctx(request)
    if not agent:
        return redirect('agents:portal_login')

    from datetime import timedelta
    start_date = request.GET.get('start_date') or (timezone.localdate() - timedelta(days=30)).isoformat()
    end_date   = request.GET.get('end_date') or timezone.localdate().isoformat()

    entries = list(AgentLedger.objects.filter(
        tenant=tenant, agent=agent,
        entry_date__gte=start_date, entry_date__lte=end_date,
    ).order_by('entry_date', 'id'))
    for e in entries:
        e.display_label = agent_ledger_display_label(e.entry_type, e.reference_type)

    balance          = _agent_balance(tenant, agent)
    total_commission = sum(e.amount for e in entries if e.amount > 0)
    total_paid       = abs(sum(e.amount for e in entries if e.amount < 0))

    return render(request, 'agents/portal/statement.html', {
        'agent': agent, 'tenant': tenant,
        'entries': entries, 'balance': balance,
        'total_commission': total_commission,
        'total_paid': total_paid,
        'start_date': start_date, 'end_date': end_date,
    })


@_portal_required
def agent_portal_payments(request):
    agent, tenant = _get_portal_ctx(request)
    if not agent:
        return redirect('agents:portal_login')

    payments = AgentLedger.objects.filter(
        tenant=tenant, agent=agent, entry_type='payment',
    ).order_by('-entry_date', '-id')

    return render(request, 'agents/portal/payments.html', {
        'agent': agent, 'tenant': tenant, 'payments': payments,
    })


@_portal_required
def agent_portal_requests(request):
    agent, tenant = _get_portal_ctx(request)
    if not agent:
        return redirect('agents:portal_login')

    from .models import AgentInvoiceRequest
    requests_qs = AgentInvoiceRequest.objects.filter(
        tenant=tenant, agent=agent,
    ).order_by('-created_at')

    return render(request, 'agents/portal/requests.html', {
        'agent': agent, 'tenant': tenant, 'requests': requests_qs,
    })


@_portal_required
def agent_portal_request_new(request):
    agent, tenant = _get_portal_ctx(request)
    if not agent:
        return redirect('agents:portal_login')

    from apps.items.models import Item
    from .models import AgentInvoiceRequest, AgentInvoiceRequestLine

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _json_error('بيانات غير صالحة')

        customer_id    = body.get('customer_id', '')
        notes          = body.get('notes', '').strip()
        lines_raw      = body.get('lines', [])

        # resolve customer
        customer_obj   = None
        customer_name  = ''
        customer_phone = ''
        if customer_id:
            from apps.customers.models import Customer as CustomerModel
            try:
                customer_obj   = CustomerModel.objects.get(pk=customer_id, tenant=tenant)
                customer_name  = customer_obj.name
                customer_phone = customer_obj.phone or ''
            except CustomerModel.DoesNotExist:
                return _json_error('العميل غير موجود')
        else:
            return _json_error('يرجى اختيار عميل من القائمة')

        if not lines_raw:
            return _json_error('لا يمكن إرسال طلب بدون منتجات')

        with transaction.atomic():
            req = AgentInvoiceRequest.objects.create(
                tenant=tenant, agent=agent,
                customer=customer_obj,
                customer_name=customer_name,
                customer_phone=customer_phone,
                notes=notes,
                status='pending',
            )
            subtotal = Decimal('0')
            for lr in lines_raw:
                item = Item.objects.get(id=lr['item_id'], tenant=tenant)
                qty  = Decimal(str(lr['quantity']))
                price = item.selling_price
                line = AgentInvoiceRequestLine.objects.create(
                    tenant=tenant, request=req,
                    item=item, quantity=qty, unit_price=price,
                )
                subtotal += line.line_total
            req.subtotal     = subtotal
            req.total_amount = subtotal
            req.save(update_fields=['subtotal', 'total_amount', 'updated_at'])

        # إشعار لجميع مديري النظام في المؤسسة
        try:
            from apps.notifications.models import Notification
            from django.urls import reverse
            admin_users = tenant.users.filter(is_active=True, is_tenant_admin=True)
            detail_url = reverse('agents:request_detail', args=[req.pk])
            for u in admin_users:
                Notification.objects.create(
                    tenant=tenant,
                    user=u,
                    notification_type='agent_request',
                    priority='medium',
                    title=f'طلب فاتورة جديد من {agent.name}',
                    message=f'المندوب {agent.name} أرسل طلب فاتورة جديد ({req.request_number}) للعميل {req.customer_name}.',
                    link=detail_url,
                )
        except Exception:
            pass

        return _json_ok(data={'id': req.id, 'number': req.request_number},
                        msg='تم إرسال الطلب بنجاح وسيصلك الرد قريباً')

    from apps.customers.models import Customer as CustomerModel
    items     = Item.objects.filter(tenant=tenant, is_active=True, is_sellable=True).order_by('name')
    customers = CustomerModel.objects.filter(tenant=tenant, is_active=True).order_by('name')
    return render(request, 'agents/portal/request_new.html', {
        'agent': agent, 'tenant': tenant, 'items': items, 'customers': customers,
    })


@_portal_required
def agent_portal_request_detail(request, pk):
    agent, tenant = _get_portal_ctx(request)
    if not agent:
        return redirect('agents:portal_login')

    from .models import AgentInvoiceRequest
    req = get_object_or_404(AgentInvoiceRequest, pk=pk, tenant=tenant, agent=agent)
    lines = req.lines.select_related('item').all()

    return render(request, 'agents/portal/request_detail.html', {
        'agent': agent, 'tenant': tenant, 'req': req, 'lines': lines,
    })


# ═════════════════════════════════════════════════════
#   ADMIN — إدارة طلبات المناديب
# ═════════════════════════════════════════════════════

@login_required
@require_permission('change_sales')
def agent_requests_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    from .models import AgentInvoiceRequest
    status_filter = request.GET.get('status', 'pending')
    qs = AgentInvoiceRequest.objects.filter(tenant=tenant).select_related('agent', 'sale_invoice')
    if status_filter:
        qs = qs.filter(status=status_filter)
    qs = qs.order_by('-created_at')

    pending_count  = AgentInvoiceRequest.objects.filter(tenant=tenant, status='pending').count()
    approved_count = AgentInvoiceRequest.objects.filter(tenant=tenant, status='approved').count()
    rejected_count = AgentInvoiceRequest.objects.filter(tenant=tenant, status='rejected').count()

    return render(request, 'agents/manage_requests.html', {
        'requests': qs,
        'status_filter': status_filter,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'rejected_count': rejected_count,
    })


@login_required
@require_permission('change_sales')
def agent_request_detail(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    from .models import AgentInvoiceRequest
    from apps.stocks.models import Stock
    req   = get_object_or_404(AgentInvoiceRequest, pk=pk, tenant=tenant)
    lines = req.lines.select_related('item').all()
    stocks = Stock.objects.filter(tenant=tenant, is_active=True).order_by('name')

    return render(request, 'agents/manage_request_detail.html', {
        'req': req, 'lines': lines, 'stocks': stocks,
    })


@login_required
@require_permission('change_sales')
@require_POST
def agent_request_approve(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    from .models import AgentInvoiceRequest
    req = get_object_or_404(AgentInvoiceRequest, pk=pk, tenant=tenant, status='pending')

    try:
        with transaction.atomic():
            from apps.sales.models import SaleInvoice, SaleInvoiceLine
            from apps.stocks.models import Stock

            stock_id = request.POST.get('stock_id')
            if stock_id:
                stock = get_object_or_404(Stock, pk=stock_id, tenant=tenant)
            else:
                stock = Stock.objects.filter(tenant=tenant, is_default=True).first() \
                    or Stock.objects.filter(tenant=tenant).first()
            if not stock:
                raise ValueError('لا يوجد مخزن مُعرَّف في النظام')

            customer = req.customer  # FK set when agent picks from customer list

            invoice = SaleInvoice.objects.create(
                tenant=tenant,
                customer=customer,
                agent=req.agent,
                stock=stock,
                invoice_date=timezone.localdate(),
                payment_method='credit',
                status='draft',
                notes=req.notes or f'طلب مندوب {req.request_number}',
            )

            for line in req.lines.select_related('item').all():
                inv_line = SaleInvoiceLine(
                    tenant=tenant, invoice=invoice,
                    item=line.item,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    discount_percent=Decimal('0'),
                    tax_rate=line.item.tax_rate,
                    cost_price_snapshot=line.item.cost_price,
                )
                inv_line.calculate()
                inv_line.save()

            invoice.recalculate_totals()
            invoice.save()

            req.status       = 'approved'
            req.sale_invoice = invoice
            req.save(update_fields=['status', 'sale_invoice', 'updated_at'])

        log_activity(request, 'اعتماد طلب فاتورة مندوب',
                     f"الطلب: {req.request_number}\nالمندوب: {req.agent.name}\nالفاتورة: {invoice.invoice_number}", 'create')
        from django.contrib import messages
        messages.success(request, f'تم اعتماد الطلب وإنشاء الفاتورة {invoice.invoice_number}')
    except Exception as e:
        from django.contrib import messages
        messages.error(request, str(e))
    return redirect('agents:request_detail', pk=pk)


@login_required
@require_permission('change_sales')
@require_POST
def agent_request_reject(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    from .models import AgentInvoiceRequest
    req = get_object_or_404(AgentInvoiceRequest, pk=pk, tenant=tenant, status='pending')

    comment = request.POST.get('comment', '').strip()
    req.status        = 'rejected'
    req.admin_comment = comment
    req.save(update_fields=['status', 'admin_comment', 'updated_at'])

    log_activity(request, 'رفض طلب فاتورة مندوب',
                 f"الطلب: {req.request_number}\nالمندوب: {req.agent.name}", 'delete')
    from django.contrib import messages
    messages.success(request, 'تم رفض الطلب')
    return redirect('agents:request_detail', pk=pk)
