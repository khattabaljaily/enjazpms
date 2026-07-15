import csv
import io
import json
from decimal import Decimal
from apps.accounts.activity_service import log_activity

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import DecimalField, Exists, OuterRef, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.decorators import require_permission, require_any_permission
from .forms import CustomerForm
from .models import Customer
from apps.sales.models import CustomerLedger, SalePayment
from apps.sales.services import (
    build_customer_statement_timeline,
    record_customer_payment_allocated,
    reverse_customer_payment_by_ledger_entry,
)
from apps.treasury.models import Treasury, TreasuryMovement


def _ensure_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return None
    return tenant


@login_required
@require_permission('view_customers')
def customer_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Customer.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    inactive = total - active

    context = {
        'form': CustomerForm(),
        'stats': {
            'total': total,
            'active': active,
            'inactive': inactive,
        },
    }
    return render(request, 'customers/customer_list.html', context)


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


def _json_error(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status, json_dumps_params={'ensure_ascii': False})


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data is not None:
        payload['data'] = data
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_customers')
def customer_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    queryset = Customer.objects.for_tenant(tenant).annotate(
        ledger_total=Coalesce(
            Sum('ledger_entries__amount'),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    )
    records_total = queryset.count()

    if status == 'active':
        queryset = queryset.filter(is_active=True)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False)

    if search_value:
        queryset = queryset.filter(
            Q(name__icontains=search_value)
            | Q(code__icontains=search_value)
            | Q(phone__icontains=search_value)
            | Q(email__icontains=search_value)
            | Q(city__icontains=search_value)
        )

    records_filtered = queryset.count()

    order_column_index = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    order_column_name = request.GET.get(f'columns[{order_column_index}][data]', 'created_at')

    allowed_order_fields = {
        'code': 'code',
        'name': 'name',
        'phone': 'phone',
        'city': 'city',
        'opening_balance': 'opening_balance',
        'current_balance': 'opening_balance',
        'is_active': 'is_active',
        'created_at': 'created_at',
    }
    order_field = allowed_order_fields.get(order_column_name, 'created_at')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    queryset = queryset.order_by(order_field)[start:start + length]

    data = [
        {
            'id': customer.id,
            'code': customer.code,
            'name': customer.name,
            'phone': customer.phone or '-',
            'city': customer.city or '-',
            'opening_balance': str(customer.opening_balance),
            'current_balance': str((customer.opening_balance or 0) + (customer.ledger_total or 0)),
            'is_active': customer.is_active,
        }
        for customer in queryset
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
@require_permission('add_customers')
def customer_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = CustomerForm(request.POST)
    if form.is_valid():
        customer = form.save(commit=False)
        customer.tenant = tenant
        customer.created_by = request.user
        customer.updated_by = request.user
        customer.save()
        log_activity(request, 'إضافة عميل جديد',
                     f"العميل: {customer.name}\nرقم الهاتف: {customer.phone or '—'}", 'create')
        return JsonResponse({
            'success': True,
            'message': 'تم إضافة العميل بنجاح',
            'id': customer.id,
        }, json_dumps_params={'ensure_ascii': False})

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_customers')
def generate_portal_token(request, pk):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required'}, status=405, json_dumps_params={'ensure_ascii': False})
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})
    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)
    customer.refresh_portal_token()
    from django.urls import reverse
    portal_url = request.build_absolute_uri(
        reverse('portal:login_via_token', args=[str(customer.portal_token)])
    )
    return JsonResponse({
        'success': True,
        'portal_url': portal_url,
        'expires_at': customer.portal_token_expires.strftime('%Y-%m-%d'),
    }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_customers')
def customer_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)
    ledger_total = (
        CustomerLedger.objects
        .for_tenant(tenant)
        .filter(customer=customer)
        .aggregate(s=Sum('amount'))['s']
        or 0
    )
    current_balance = (customer.opening_balance or 0) + ledger_total

    return JsonResponse({
        'success': True,
        'data': {
            'id': customer.id,
            'name': customer.name,
            'phone': customer.phone,
            'email': customer.email,
            'city': customer.city,
            'address': customer.address,
            'opening_balance': str(customer.opening_balance),
            'current_balance': str(current_balance),
            'credit_limit': str(customer.credit_limit),
            'notes': customer.notes,
            'is_active': customer.is_active,
        }
    })


@login_required
@require_permission('view_customer_transactions')
def customer_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)
    opening = customer.opening_balance or Decimal('0')

    type_labels = {
        'opening': 'مديونية افتتاحية',
        'invoice': 'فاتورة آجل',
        'payment': 'سداد عميل',
        'return': 'مرتجع',
        'adjustment': 'تعديل',
    }

    data = []
    if opening != Decimal('0'):
        entry_date = customer.created_at.date().strftime('%Y-%m-%d') if customer.created_at else ''
        data.append({
            'entry_date': entry_date,
            'entry_type': 'opening',
            'entry_type_label': 'مديونية افتتاحية',
            'amount': str(opening),
            'running_balance': str(opening),
            'notes': 'مديونية افتتاحية للعميل',
            'reference_type': 'customer_opening',
            'reference_id': customer.id,
            'is_edited': False,
        })

    entries = list(
        CustomerLedger.objects
        .for_tenant(tenant)
        .filter(customer=customer)
        .order_by('entry_date', 'id')
    )

    for e in build_customer_statement_timeline(tenant, entries):
        true_balance = (e.running_balance or Decimal('0')) + opening
        data.append({
            'entry_date': e.entry_date.strftime('%Y-%m-%d'),
            'entry_type': e.entry_type,
            'entry_type_label': type_labels.get(e.entry_type, e.entry_type),
            'amount': str(abs(e.amount)),
            'running_balance': str(true_balance),
            'notes': e.notes or '—',
            'reference_type': e.reference_type or '',
            'reference_id': e.reference_id,
            'is_edited': e.is_edited,
            'is_reversal': e.is_reversal,
        })

    data.reverse()
    return JsonResponse({'success': True, 'data': data}, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_customer_payments')
def customer_payments(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    customers = Customer.objects.for_tenant(tenant).filter(is_active=True).annotate(
        ledger_total=Coalesce(
            Sum('ledger_entries__amount', output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).order_by('name')
    treasuries = Treasury.objects.for_tenant(tenant).filter(is_active=True, is_hard_currency=False).order_by('name')
    stats = CustomerLedger.objects.for_tenant(tenant).filter(entry_type='payment').aggregate(
        total=Coalesce(
            Sum('amount', output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        cash=Coalesce(
            Sum('amount', filter=Q(reference_type='customer_payment_cash'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        bank=Coalesce(
            Sum('amount', filter=Q(reference_type='customer_payment_bank'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )

    def positive(value):
        return abs(value) if value is not None else 0

    context = {
        'customers': customers,
        'treasuries': treasuries,
        'stats': {
            'total': CustomerLedger.objects.for_tenant(tenant).filter(entry_type='payment').count(),
            'total_amount': positive(stats['total']),
            'cash_amount': positive(stats['cash']),
            'bank_amount': positive(stats['bank']),
        },
        'today': timezone.localdate().isoformat(),
    }
    return render(request, 'customers/payment_list.html', context)


@login_required
@require_permission('view_customer_payments')
def customer_payments_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    customer_filter = request.GET.get('customer_id', '')
    method_filter = request.GET.get('payment_method', '')

    qs = CustomerLedger.objects.for_tenant(tenant).filter(entry_type='payment')
    total = qs.count()

    if customer_filter:
        qs = qs.filter(customer_id=customer_filter)
    if method_filter:
        qs = qs.filter(reference_type=f'customer_payment_{method_filter}')

    if search_value:
        qs = qs.filter(
            Q(customer__name__icontains=search_value)
            | Q(notes__icontains=search_value)
            | Q(reference_type__icontains=search_value)
        )

    filtered_total = qs.count()

    order_col = request.GET.get('order[0][column]', None)
    order_dir = request.GET.get('order[0][dir]', 'desc')
    col_map = {
        '0': 'entry_date',
        '1': 'customer__name',
        '2': 'amount',
        '3': 'reference_type',
    }
    if order_col is None:
        order_field = '-id'
    else:
        order_field = col_map.get(order_col, 'id')
        if order_dir == 'desc':
            order_field = f'-{order_field}'
    qs = qs.order_by(order_field)

    cancel_qs = CustomerLedger.objects.for_tenant(tenant).filter(
        reference_type='customer_payment_cancel',
        reference_id=OuterRef('pk'),
    )
    qs = qs.annotate(is_canceled=Exists(cancel_qs))

    cancel_qs = CustomerLedger.objects.for_tenant(tenant).filter(
        reference_type='customer_payment_cancel',
        reference_id=OuterRef('pk'),
    )
    qs = qs.annotate(is_canceled=Exists(cancel_qs))

    page_qs = list(qs[start: start + length])
    sale_payment_ids = [entry.reference_id for entry in page_qs
                        if entry.reference_type == 'sale_payment' and entry.reference_id]
    sale_payment_methods = {}
    if sale_payment_ids:
        from apps.sales.models import SalePayment
        sale_payment_methods = {
            p.id: p.payment_method
            for p in SalePayment.objects.for_tenant(tenant).filter(id__in=sale_payment_ids)
        }

    data = []
    for entry in page_qs:
        if entry.reference_type == 'customer_payment_cash':
            method_label = 'نقداً'
        elif entry.reference_type == 'customer_payment_bank':
            method_label = 'بنكي'
        elif entry.reference_type == 'sale_payment':
            payment_method = sale_payment_methods.get(entry.reference_id)
            if payment_method == 'cash':
                method_label = 'نقداً'
            elif payment_method == 'bank':
                method_label = 'بنكي'
            else:
                method_label = '—'
        else:
            method_label = '—'

        if entry.is_canceled:
            method_label += ' — ملغاة'
        data.append({
            'id': entry.id,
            'entry_date': entry.entry_date.strftime('%Y-%m-%d'),
            'customer': entry.customer.name,
            'amount': str(entry.amount),
            'payment_method': method_label,
            'notes': entry.notes or '—',
            'entry_type': entry.entry_type,
            'is_canceled': bool(entry.is_canceled),
        })

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered_total,
        'data': data,
    }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_customer_payments')
def customer_payment_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    payment = get_object_or_404(
        CustomerLedger.objects.for_tenant(tenant).select_related('customer'),
        entry_type='payment',
        pk=pk,
    )

    # لو القيد مرتبط بفاتورة حقيقية (SalePayment)، الأصل عن حالة الإلغاء وحركة
    # الخزينة موجود عند تلك الدفعة نفسها (مُعرَّفة برقمها هي، لا بمعرّف قيد العميل).
    linked_sale_payment = None
    if payment.reference_type in ('customer_payment_cash', 'customer_payment_bank') and payment.reference_id:
        linked_sale_payment = SalePayment.objects.for_tenant(tenant).filter(pk=payment.reference_id).first()

    is_hard = payment.reference_type.endswith('_cash')
    cash_treasury = None
    cancellation = None
    if linked_sale_payment:
        if is_hard:
            treasury_movement = TreasuryMovement.objects.for_tenant(tenant).filter(
                reference_type='sale_payment', reference_id=linked_sale_payment.id,
            ).select_related('treasury').first()
            if treasury_movement:
                cash_treasury = treasury_movement.treasury.name
        if linked_sale_payment.is_reversed:
            cancellation = CustomerLedger.objects.for_tenant(tenant).filter(
                reference_type=payment.reference_type, reference_id=payment.reference_id, is_reversal=True,
            ).first()
    else:
        cancellation = CustomerLedger.objects.for_tenant(tenant).filter(
            reference_type='customer_payment_cancel',
            reference_id=payment.id,
        ).first()
        if is_hard:
            treasury_movement = TreasuryMovement.objects.for_tenant(tenant).filter(
                reference_type=payment.reference_type, reference_id=payment.id,
            ).select_related('treasury').first()
            if treasury_movement:
                cash_treasury = treasury_movement.treasury.name

    response_data = {
        'id': payment.id,
        'entry_date': payment.entry_date.strftime('%Y-%m-%d'),
        'customer': payment.customer.name,
        'amount': str(payment.amount),
        'payment_method': 'نقداً' if is_hard else 'بنكي',
        'notes': payment.notes or '—',
        'is_canceled': bool(cancellation),
        'cancellation_note': cancellation.notes if cancellation else '',
        'cancellation_date': cancellation.entry_date.strftime('%Y-%m-%d') if cancellation else None,
        'cash_treasury': cash_treasury,
    }
    return _json_ok(data=response_data)


@login_required
@require_permission('add_customer_payments')
@require_POST
def customer_payment_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    if request.content_type != 'application/json':
        return _json_error('بيانات غير صالحة', status=400)

    try:
        body = json.loads(request.body)
        customer_id = int(body.get('customer_id'))
        amount = Decimal(str(body.get('amount')))
        payment_date = body.get('payment_date') or timezone.localdate().isoformat()
        method = body.get('method', 'cash')
        treasury_id = body.get('treasury_id')
        reference = str(body.get('reference', '') or '').strip()
        notes = str(body.get('notes', '') or '').strip()
    except (TypeError, ValueError, json.JSONDecodeError) as e:
        return _json_error(f'بيانات الدفعة غير صالحة: {e}')

    if amount <= 0:
        return _json_error('المبلغ يجب أن يكون أكبر من الصفر')

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=customer_id)
    note_text = notes
    if reference:
        note_text = f"{note_text} | مرجع: {reference}" if note_text else f"مرجع: {reference}"

    treasury = None
    if method == 'cash':
        if not treasury_id:
            return _json_error('يجب اختيار الخزينة عند دفع نقداً')
        treasury = get_object_or_404(Treasury.objects.for_tenant(tenant).filter(is_hard_currency=False), pk=int(treasury_id))

    try:
        allocation = record_customer_payment_allocated(
            tenant=tenant, customer=customer, amount=amount, method=method,
            date=payment_date, reference=reference, notes=note_text,
            user=request.user, treasury=treasury,
        )
    except ValueError as e:
        return _json_error(str(e), status=400)
    except Exception:
        return _json_error('تعذر تسجيل الدفعة، حاول مرة أخرى')

    balance = (
        CustomerLedger.objects.for_tenant(tenant)
        .filter(customer=customer)
        .aggregate(s=Sum('amount'))['s'] or 0
    )
    current_balance = (customer.opening_balance or 0) + balance

    parts = []
    for item in allocation['invoices']:
        parts.append(f"فاتورة {item['invoice'].invoice_number} ({item['amount']})")
    if allocation['opening_balance'] > 0:
        parts.append(f"مستحقات افتتاحية ({allocation['opening_balance']})")
    if allocation['credit'] > 0:
        parts.append(f"رصيد دائن ({allocation['credit']})")
    breakdown = ' — '.join(parts) if parts else ''
    success_msg = f'تم تسجيل دفعة العميل بنجاح: {breakdown}' if breakdown else 'تم تسجيل دفعة العميل بنجاح'

    log_activity(request, 'تسجيل دفعة من عميل',
                 f"العميل: {customer.name}\nالمبلغ: {amount}\nطريقة الدفع: {method}\nالتوزيع: {breakdown}", 'create')

    return _json_ok(data={'current_balance': str(current_balance), 'breakdown': breakdown}, msg=success_msg)


@login_required
@require_permission('cancel_customer_payments')
@require_POST
def customer_payment_cancel_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    payment = get_object_or_404(
        CustomerLedger.objects.for_tenant(tenant).filter(entry_type='payment'),
        pk=pk,
    )
    try:
        with transaction.atomic():
            reverse_customer_payment_by_ledger_entry(tenant, payment, user=request.user)
    except ValueError as e:
        return _json_error(str(e), status=400)

    log_activity(request, 'إلغاء دفعة عميل', f'{payment.customer.name}', 'delete')
    return _json_ok(msg='تم إلغاء الدفعة واستعادة مديونية العميل')


@login_required
@require_permission('change_customers')
def customer_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)
    form = CustomerForm(request.POST, instance=customer)

    if form.is_valid():
        customer = form.save(commit=False)
        customer.updated_by = request.user
        customer.save()
        log_activity(request, 'تعديل عميل', customer.name, 'update')
        return JsonResponse({
            'success': True,
            'message': 'تم تعديل بيانات العميل بنجاح',
        }, json_dumps_params={'ensure_ascii': False})

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('delete_customers')
def customer_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)
    cus_name = customer.name
    customer.delete()
    log_activity(request, 'حذف عميل', cus_name, 'delete')
    return JsonResponse({
        'success': True,
        'message': 'تم حذف العميل بنجاح',
    }, json_dumps_params={'ensure_ascii': False})


@login_required
def customer_create(request):
    return redirect('customers:list')


_CUSTOMER_FIELD_SCHEMA = [
    {"field": "name",            "description": "اسم العميل أو الزبون أو الشخص", "required": True},
    {"field": "phone",           "description": "رقم الهاتف أو الجوال أو الموبايل"},
    {"field": "email",           "description": "البريد الإلكتروني"},
    {"field": "city",            "description": "المدينة أو المنطقة أو الموقع"},
    {"field": "address",         "description": "العنوان التفصيلي أو الشارع"},
    {"field": "opening_balance", "description": "المديونية الافتتاحية أو مديونية البداية أو الرصيد الافتتاحي"},
    {"field": "credit_limit",    "description": "حد الائتمان أو سقف الدين أو أقصى دين"},
    {"field": "notes",           "description": "ملاحظات أو تعليقات"},
]


@login_required
@require_permission('import_customers')
def customer_import_api(request):
    """Import customers from Excel/CSV with AI-assisted column mapping."""
    from apps.core.io_utils import parse_uploaded_file, smart_get, safe_decimal, clean_phone, clean_email
    from apps.ai.services import smart_map_headers

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if 'file' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'لم يتم رفع أي ملف'}, status=400, json_dumps_params={'ensure_ascii': False})

    rows, err = parse_uploaded_file(request.FILES['file'])
    if err:
        return JsonResponse({'success': False, 'message': err}, status=400, json_dumps_params={'ensure_ascii': False})

    if not rows:
        return JsonResponse({'success': False, 'message': 'الملف فارغ أو لا يحتوي على بيانات'}, status=400, json_dumps_params={'ensure_ascii': False})

    # AI maps actual headers → canonical field names (one call for the whole import)
    actual_headers = list(rows[0].keys())
    try:
        mapping = smart_map_headers(actual_headers, _CUSTOMER_FIELD_SCHEMA)
    except Exception:
        mapping = {}

    imported_count, errors = 0, []
    for row_num, row in enumerate(rows, start=2):
        try:
            name = smart_get(row, 'name', mapping, 'الاسم', 'اسم العميل', 'العميل', 'name')
            if not name:
                errors.append(f'الصف {row_num}: اسم العميل مطلوب')
                continue

            Customer.objects.create(
                tenant=tenant,
                name=name,
                phone=clean_phone(smart_get(row, 'phone', mapping, 'الهاتف', 'الجوال', 'الموبايل', 'phone')) or '',
                email=clean_email(smart_get(row, 'email', mapping, 'البريد', 'البريد الإلكتروني', 'email')) or '',
                city=smart_get(row, 'city', mapping, 'المدينة', 'المنطقة', 'city'),
                address=smart_get(row, 'address', mapping, 'العنوان', 'address'),
                opening_balance=safe_decimal(smart_get(row, 'opening_balance', mapping, 'المديونية الافتتاحية', 'المديونية', 'مديونية البداية', 'الرصيد الافتتاحي', 'الرصيد', 'opening_balance', default='0')),
                credit_limit=safe_decimal(smart_get(row, 'credit_limit', mapping, 'حد الائتمان', 'حد_الائتمان', 'credit_limit', default='0')),
                notes=smart_get(row, 'notes', mapping, 'الملاحظات', 'ملاحظات', 'notes'),
                is_active=True,
            )
            imported_count += 1
        except Exception as e:
            import logging, traceback
            logging.getLogger('customers').error('import row %d: %s\n%s', row_num, e, traceback.format_exc())
            errors.append(f'الصف {row_num}: {str(e)}')

    message = f'تم استيراد {imported_count} عميل بنجاح'
    if errors:
        message += f'. حدثت {len(errors)} أخطاء'

    return JsonResponse({
        'success': True,
        'message': message,
        'imported': imported_count,
        'errors': errors[:10],
    }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('export_customers')
def customer_export_api(request):
    """Export customers to CSV file"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    # Create CSV response
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="customers.csv"'
    
    # Add BOM for Excel UTF-8 support
    response.write('\ufeff')
    
    writer = csv.writer(response)
    
    # Write headers
    writer.writerow([
        'الاسم', 'الكود', 'الهاتف', 'البريد', 'المدينة', 'العنوان',
        'المديونية الافتتاحية', 'حد الائتمان', 'الملاحظات', 'نشط'
    ])
    
    # Write data
    customers = Customer.objects.for_tenant(tenant).order_by('name')
    for customer in customers:
        writer.writerow([
            customer.name,
            customer.code,
            customer.phone or '',
            customer.email or '',
            customer.city or '',
            customer.address or '',
            customer.opening_balance,
            customer.credit_limit,
            customer.notes or '',
            'نعم' if customer.is_active else 'لا'
        ])
    
    return response


@login_required
@require_permission('import_customers')
def download_template(request):
    """Download CSV template for import"""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="customers_template.csv"'
    
    # Add BOM for Excel UTF-8 support
    response.write('\ufeff')
    
    writer = csv.writer(response)
    
    # Write headers
    writer.writerow([
        'الاسم', 'الهاتف', 'البريد', 'المدينة', 'العنوان',
        'المديونية الافتتاحية', 'حد الائتمان'
    ])
    
    # Write example row
    writer.writerow([
        'أحمد محمد', '0512345678', 'ahmad@example.com', 'الرياض', 'شارع الملك فهد',
        '0', '5000'
    ])
    
    return response
