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
import io
import json

from apps.accounts.decorators import require_permission
from .forms import SupplierForm
from .models import Supplier
from apps.purchases.models import SupplierLedger
from apps.purchases.services import _apply_supplier_ledger
from apps.treasury.models import Treasury, TreasuryMovement
from apps.treasury.services import post_treasury_disbursement, post_treasury_receipt


def _ensure_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return None
    return tenant


@login_required
@require_permission('view_suppliers')
def supplier_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Supplier.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    inactive = total - active

    context = {
        'form': SupplierForm(),
        'stats': {
            'total': total,
            'active': active,
            'inactive': inactive,
        },
    }
    return render(request, 'suppliers/supplier_list.html', context)


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


def _json_error(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status)


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data is not None:
        payload['data'] = data
    return JsonResponse(payload)


@login_required
@require_permission('view_suppliers')
def supplier_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    queryset = Supplier.objects.for_tenant(tenant)
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
        'is_active': 'is_active',
        'created_at': 'created_at',
    }
    order_field = allowed_order_fields.get(order_column_name, 'created_at')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    queryset = queryset.order_by(order_field)[start:start + length]

    data = [
        {
            'id': supplier.id,
            'code': supplier.code,
            'name': supplier.name,
            'phone': supplier.phone or '-',
            'city': supplier.city or '-',
            'opening_balance': str(supplier.opening_balance),
            'current_balance': str(
                (supplier.opening_balance or Decimal('0')) + (
                    (SupplierLedger.objects.filter(tenant=tenant, supplier=supplier).aggregate(s=Sum('amount'))['s'] or Decimal('0'))
                    if SupplierLedger else Decimal('0')
                )
            ),
            'is_active': supplier.is_active,
        }
        for supplier in queryset
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
@require_permission('add_suppliers')
def supplier_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = SupplierForm(request.POST)
    if form.is_valid():
        supplier = form.save(commit=False)
        supplier.tenant = tenant
        supplier.created_by = request.user
        supplier.updated_by = request.user
        supplier.save()
        log_activity(request, 'إضافة مورد جديد',
                     f"المورد: {supplier.name}\nرقم الهاتف: {supplier.phone or '—'}", 'create')
        return JsonResponse({
            'success': True,
            'message': 'تم إضافة المورد بنجاح',
            'id': supplier.id,
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
@require_permission('view_suppliers')
def supplier_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    supplier = get_object_or_404(Supplier.objects.for_tenant(tenant), pk=pk)

    return JsonResponse({
        'success': True,
        'data': {
            'id': supplier.id,
            'name': supplier.name,
            'phone': supplier.phone,
            'email': supplier.email,
            'city': supplier.city,
            'address': supplier.address,
            'opening_balance': str(supplier.opening_balance),
            'current_balance': str(
                (supplier.opening_balance or Decimal('0')) + (
                    (SupplierLedger.objects.filter(tenant=tenant, supplier=supplier).aggregate(s=Sum('amount'))['s'] or Decimal('0'))
                    if SupplierLedger else Decimal('0')
                )
            ),
            'credit_limit': str(supplier.credit_limit),
            'notes': supplier.notes,
            'is_active': supplier.is_active,
        }
    })


@login_required
@require_permission('view_supplier_transactions')
def supplier_transactions_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    supplier = get_object_or_404(Supplier.objects.for_tenant(tenant), pk=pk)
    opening = supplier.opening_balance or Decimal('0')

    data = []
    running = opening
    if opening != Decimal('0'):
        entry_date = supplier.created_at.date().strftime('%Y-%m-%d') if supplier.created_at else ''
        data.append({
            'entry_date': entry_date,
            'entry_type': 'opening',
            'entry_type_label': 'مديونية افتتاحية',
            'amount': str(opening),
            'running_balance': str(opening),
            'notes': 'مديونية افتتاحية للمورد',
            'reference_type': 'supplier_opening',
            'reference_id': supplier.id,
        })

    if SupplierLedger:
        entries = SupplierLedger.objects.filter(tenant=tenant, supplier=supplier).order_by('entry_date', 'id')
        labels = {
            'invoice': 'فاتورة/أمر شراء آجل',
            'payment': 'سداد مورد',
            'return': 'مرتجع شراء',
            'adjustment': 'تعديل',
            'opening': 'مديونية افتتاحية',
        }
        for e in entries:
            running += (e.amount or Decimal('0'))
            data.append({
                'entry_date': e.entry_date.strftime('%Y-%m-%d') if e.entry_date else '',
                'entry_type': e.entry_type,
                'entry_type_label': labels.get(e.entry_type, e.entry_type),
                'amount': str(e.amount),
                'running_balance': str(running),
                'notes': e.notes or '—',
                'reference_type': e.reference_type or '—',
                'reference_id': e.reference_id,
            })

    return JsonResponse({'success': True, 'data': data})


@login_required
@require_permission('view_supplier_payments')
def supplier_payments(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    suppliers = Supplier.objects.for_tenant(tenant).filter(is_active=True).annotate(
        ledger_total=Coalesce(
            Sum('ledger_entries__amount', output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
    ).order_by('name')
    treasuries = Treasury.objects.for_tenant(tenant).filter(is_active=True).order_by('name')
    stats = SupplierLedger.objects.for_tenant(tenant).filter(entry_type='payment').aggregate(
        total=Coalesce(
            Sum('amount', output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        cash=Coalesce(
            Sum('amount', filter=Q(reference_type='supplier_payment_cash'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
        bank=Coalesce(
            Sum('amount', filter=Q(reference_type='supplier_payment_bank'), output_field=DecimalField(max_digits=14, decimal_places=2)),
            Value(0, output_field=DecimalField(max_digits=14, decimal_places=2)),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        ),
    )

    def positive(value):
        return abs(value) if value is not None else 0

    context = {
        'suppliers': suppliers,
        'treasuries': treasuries,
        'stats': {
            'total': SupplierLedger.objects.for_tenant(tenant).filter(entry_type='payment').count(),
            'total_amount': positive(stats['total']),
            'cash_amount': positive(stats['cash']),
            'bank_amount': positive(stats['bank']),
        },
        'today': timezone.localdate().isoformat(),
    }
    return render(request, 'suppliers/payment_list.html', context)


@login_required
@require_permission('view_supplier_payments')
def supplier_payments_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    supplier_filter = request.GET.get('supplier_id', '')
    method_filter = request.GET.get('payment_method', '')

    qs = SupplierLedger.objects.for_tenant(tenant).filter(entry_type='payment')
    total = qs.count()

    if supplier_filter:
        qs = qs.filter(supplier_id=supplier_filter)
    if method_filter:
        qs = qs.filter(reference_type=f'supplier_payment_{method_filter}')

    if search_value:
        qs = qs.filter(
            Q(supplier__name__icontains=search_value)
            | Q(notes__icontains=search_value)
            | Q(reference_type__icontains=search_value)
        )

    filtered_total = qs.count()

    order_col = request.GET.get('order[0][column]', None)
    order_dir = request.GET.get('order[0][dir]', 'desc')
    col_map = {
        '0': 'entry_date',
        '1': 'supplier__name',
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

    cancel_qs = SupplierLedger.objects.for_tenant(tenant).filter(
        reference_type='supplier_payment_cancel',
        reference_id=OuterRef('pk'),
    )
    qs = qs.annotate(is_canceled=Exists(cancel_qs))

    page_qs = qs[start: start + length]
    data = []
    for entry in page_qs:
        method_label = 'نقداً' if entry.reference_type == 'supplier_payment_cash' else 'بنكي'
        if entry.is_canceled:
            method_label += ' — ملغاة'
        data.append({
            'id': entry.id,
            'entry_date': entry.entry_date.strftime('%Y-%m-%d'),
            'supplier': entry.supplier.name,
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
    })


@login_required
@require_permission('view_supplier_payments')
def supplier_payment_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    payment = get_object_or_404(
        SupplierLedger.objects.for_tenant(tenant).select_related('supplier'),
        entry_type='payment',
        pk=pk,
    )

    cancellation = SupplierLedger.objects.for_tenant(tenant).filter(
        reference_type='supplier_payment_cancel',
        reference_id=payment.id,
    ).first()
    cash_treasury = None
    if payment.reference_type == 'supplier_payment_cash':
        treasury_movement = TreasuryMovement.objects.for_tenant(tenant).filter(
            reference_type='supplier_payment_cash',
            reference_id=payment.id,
        ).select_related('treasury').first()
        if treasury_movement:
            cash_treasury = treasury_movement.treasury.name

    response_data = {
        'id': payment.id,
        'entry_date': payment.entry_date.strftime('%Y-%m-%d'),
        'supplier': payment.supplier.name,
        'amount': str(payment.amount),
        'payment_method': 'نقداً' if payment.reference_type == 'supplier_payment_cash' else 'بنكي',
        'notes': payment.notes or '—',
        'is_canceled': bool(cancellation),
        'cancellation_note': cancellation.notes if cancellation else '',
        'cancellation_date': cancellation.entry_date.strftime('%Y-%m-%d') if cancellation else None,
        'cash_treasury': cash_treasury,
    }
    return _json_ok(data=response_data)


@login_required
@require_permission('add_supplier_payments')
@require_POST
def supplier_payment_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    if not request.content_type or 'application/json' not in request.content_type:
        return _json_error('بيانات غير صالحة', status=400)

    try:
        body = json.loads(request.body.decode('utf-8') if isinstance(request.body, bytes) else request.body)
        supplier_id = int(body.get('supplier_id'))
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

    supplier = get_object_or_404(Supplier.objects.for_tenant(tenant), pk=supplier_id)
    reference_type = 'supplier_payment_bank' if method == 'bank' else 'supplier_payment_cash'
    note_text = notes
    if reference:
        note_text = f"{note_text} | مرجع: {reference}" if note_text else f"مرجع: {reference}"
    if not note_text:
        note_text = 'سداد مورد'

    try:
        with transaction.atomic():
            payment_entry = _apply_supplier_ledger(
                tenant=tenant,
                supplier=supplier,
                amount=-amount,
                entry_type='payment',
                reference_type=reference_type,
                reference_id=None,
                date=payment_date,
                notes=note_text,
            )

            if method == 'cash':
                if not treasury_id:
                    raise ValueError('يجب اختيار الخزينة عند دفع نقداً')
                treasury = get_object_or_404(Treasury.objects.for_tenant(tenant), pk=int(treasury_id))
                movement = post_treasury_disbursement(
                    tenant=tenant,
                    amount=amount,
                    date=payment_date,
                    reference_type='supplier_payment_cash',
                    reference_id=payment_entry.id if payment_entry else None,
                    description=f'دفعة مورد {supplier.name}',
                    user=request.user,
                    treasury=treasury,
                )
                if not movement:
                    raise ValueError('تعذر تسجيل حركة الخزينة')
    except ValueError as e:
        return _json_error(str(e), status=400)
    except Exception:
        return _json_error('تعذر تسجيل الدفعة، حاول مرة أخرى')

    balance = (
        SupplierLedger.objects.for_tenant(tenant)
        .filter(supplier=supplier)
        .aggregate(s=Sum('amount'))['s'] or 0
    )
    current_balance = (supplier.opening_balance or 0) + balance

    log_activity(request, 'تسجيل دفعة للمورد',
                 f"المورد: {supplier.name}\nالمبلغ: {amount}\nطريقة الدفع: {method}", 'create')

    return _json_ok(data={'current_balance': str(current_balance)}, msg='تم تسجيل دفعة المورد بنجاح')


@login_required
@require_permission('cancel_supplier_payments')
@require_POST
def supplier_payment_cancel_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    payment = get_object_or_404(
        SupplierLedger.objects.for_tenant(tenant).filter(entry_type='payment'),
        pk=pk,
    )
    reverse_notes = f"إلغاء دفعة مورد — {payment.notes or ''}".strip()
    with transaction.atomic():
        if payment.reference_type == 'supplier_payment_cash':
            treasury_movement = TreasuryMovement.objects.for_tenant(tenant).filter(
                reference_type='supplier_payment_cash',
                reference_id=payment.id,
            ).first()
            if treasury_movement:
                post_treasury_receipt(
                    tenant=tenant,
                    amount=abs(payment.amount),
                    date=timezone.localdate(),
                    reference_type='supplier_payment_cash_cancel',
                    reference_id=payment.id,
                    description=f'إلغاء دفعة مورد {payment.supplier.name}',
                    user=request.user,
                    treasury=treasury_movement.treasury,
                )

        _apply_supplier_ledger(
            tenant=tenant,
            supplier=payment.supplier,
            amount=-payment.amount,
            entry_type='adjustment',
            reference_type='supplier_payment_cancel',
            reference_id=payment.id,
            date=timezone.localdate(),
            notes=reverse_notes,
        )

    return _json_ok(msg='تم إلغاء الدفعة وتحديث مديونية المورد')


@login_required
@require_permission('change_suppliers')
def supplier_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    supplier = get_object_or_404(Supplier.objects.for_tenant(tenant), pk=pk)
    form = SupplierForm(request.POST, instance=supplier)

    if form.is_valid():
        supplier = form.save(commit=False)
        supplier.updated_by = request.user
        supplier.save()
        return JsonResponse({
            'success': True,
            'message': 'تم تعديل بيانات المورد بنجاح',
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
@require_permission('delete_suppliers')
def supplier_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    supplier = get_object_or_404(Supplier.objects.for_tenant(tenant), pk=pk)
    try:
        supplier.delete()
    except ProtectedError:
        return JsonResponse({
            'success': False,
            'message': 'لا يمكن حذف المورد لوجود فواتير أو حركات مرتبطة به.',
        }, status=400)
    return JsonResponse({
        'success': True,
        'message': 'تم حذف المورد بنجاح',
    })


@login_required
@require_permission('add_suppliers')
def supplier_create(request):
    return redirect('suppliers:list')


_SUPPLIER_FIELD_SCHEMA = [
    {"field": "name",            "description": "اسم المورد أو الشركة أو المصنع", "required": True},
    {"field": "phone",           "description": "رقم الهاتف أو الجوال أو الموبايل"},
    {"field": "email",           "description": "البريد الإلكتروني"},
    {"field": "city",            "description": "المدينة أو المنطقة أو الموقع"},
    {"field": "address",         "description": "العنوان التفصيلي أو الشارع"},
    {"field": "opening_balance", "description": "المديونية الافتتاحية أو مديونية البداية أو الرصيد الافتتاحي"},
    {"field": "credit_limit",    "description": "حد الائتمان أو سقف الدين"},
    {"field": "notes",           "description": "ملاحظات أو تعليقات"},
]


@login_required
@require_permission('import_suppliers')
def supplier_import_api(request):
    """Import suppliers from Excel/CSV with AI-assisted column mapping."""
    from apps.core.io_utils import parse_uploaded_file, smart_get, safe_decimal, clean_phone, clean_email
    from apps.ai.services import smart_map_headers

    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if 'file' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'لم يتم رفع أي ملف'}, status=400)

    rows, err = parse_uploaded_file(request.FILES['file'])
    if err:
        return JsonResponse({'success': False, 'message': err}, status=400)

    if not rows:
        return JsonResponse({'success': False, 'message': 'الملف فارغ أو لا يحتوي على بيانات'}, status=400)

    actual_headers = list(rows[0].keys())
    try:
        mapping = smart_map_headers(actual_headers, _SUPPLIER_FIELD_SCHEMA)
    except Exception:
        mapping = {}

    imported_count, errors = 0, []
    for row_num, row in enumerate(rows, start=2):
        try:
            name = smart_get(row, 'name', mapping, 'الاسم', 'اسم المورد', 'المورد', 'الشركة', 'name')
            if not name:
                errors.append(f'الصف {row_num}: اسم المورد مطلوب')
                continue

            Supplier.objects.create(
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
            logging.getLogger('suppliers').error('import row %d: %s\n%s', row_num, e, traceback.format_exc())
            errors.append(f'الصف {row_num}: {str(e)}')

    message = f'تم استيراد {imported_count} مورد بنجاح'
    if errors:
        message += f'. حدثت {len(errors)} أخطاء'

    return JsonResponse({
        'success': True,
        'message': message,
        'imported': imported_count,
        'errors': errors[:10],
    })


@login_required
@require_permission('export_suppliers')
def supplier_export_api(request):
    """Export suppliers to CSV file"""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    # Create CSV response
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="suppliers.csv"'
    
    # Add BOM for Excel UTF-8 support
    response.write('\ufeff')
    
    writer = csv.writer(response)
    
    # Write headers
    writer.writerow([
        'الاسم', 'الكود', 'الهاتف', 'البريد', 'المدينة', 'العنوان',
        'المديونية الافتتاحية', 'حد الائتمان', 'الملاحظات', 'نشط'
    ])
    
    # Write data
    suppliers = Supplier.objects.for_tenant(tenant).order_by('name')
    for supplier in suppliers:
        writer.writerow([
            supplier.name,
            supplier.code,
            supplier.phone or '',
            supplier.email or '',
            supplier.city or '',
            supplier.address or '',
            supplier.opening_balance,
            supplier.credit_limit,
            supplier.notes or '',
            'نعم' if supplier.is_active else 'لا'
        ])
    
    return response


@login_required
@require_permission('import_suppliers')
def download_template(request):
    """Download CSV template for import"""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="suppliers_template.csv"'
    
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
        'شركة التوريدات المحدودة', '0512345678', 'supplier@example.com', 'الرياض', 'شارع الملك فهد',
        '0', '10000'
    ])
    
    return response
