from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.accounts.activity_service import log_activity
from apps.accounts.decorators import require_permission
from apps.sales.models import SaleInvoice

from .forms import InsuranceCompanyForm
from .models import InsuranceCompany, InsuranceMember, InsuranceClaim, InsuranceClaimSettlement
from .reports import InsuranceReportGenerator
from . import services


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


def _json_error(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status, json_dumps_params={'ensure_ascii': False})


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data is not None:
        payload['data'] = data
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


def _serialize_form_errors(form):
    return {field: [str(e) for e in errors] for field, errors in form.errors.items()}


# ════════════════════════════════════════════════════════════
# Insurance companies
# ════════════════════════════════════════════════════════════

@login_required
@require_permission('view_insurance_companies')
def company_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    return render(request, 'insurance/company_list.html', {
        'form': InsuranceCompanyForm(),
        'section': 'insurance_companies',
    })


@login_required
@require_permission('view_insurance_companies')
def company_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    qs = InsuranceCompany.objects.filter(tenant=tenant)
    records_total = qs.count()

    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(code__icontains=search) | Q(phone__icontains=search))
    records_filtered = qs.count()

    qs = qs.order_by('-created_at')[start:start + length]

    data = [
        {
            'id': c.id, 'code': c.code, 'name': c.name,
            'phone': c.phone or '-', 'contact_person': c.contact_person or '-',
            'default_coverage_percent': str(c.default_coverage_percent),
            'is_active': c.is_active,
        }
        for c in qs
    ]
    return JsonResponse({'draw': draw, 'recordsTotal': records_total,
                          'recordsFiltered': records_filtered, 'data': data})


@login_required
@require_permission('add_insurance_companies')
def company_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)

    form = InsuranceCompanyForm(request.POST)
    if form.is_valid():
        company = form.save(commit=False)
        company.tenant = tenant
        company.created_by = request.user
        company.updated_by = request.user
        company.save()
        log_activity(request, 'إضافة شركة تأمين', company.name, 'create')
        return _json_ok({'id': company.id}, 'تم إضافة شركة التأمين بنجاح')
    return JsonResponse(
        {'success': False, 'message': 'يرجى التحقق من الحقول', 'errors': _serialize_form_errors(form)},
        status=400, json_dumps_params={'ensure_ascii': False}
    )


@login_required
@require_permission('view_insurance_companies')
def company_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    try:
        c = InsuranceCompany.objects.get(tenant=tenant, pk=pk)
    except InsuranceCompany.DoesNotExist:
        return _json_error('شركة التأمين غير موجودة', status=404)
    return _json_ok({
        'id': c.id, 'name': c.name, 'code': c.code, 'contact_person': c.contact_person,
        'phone': c.phone, 'email': c.email, 'address': c.address,
        'default_coverage_percent': str(c.default_coverage_percent),
        'settlement_period_days': c.settlement_period_days,
        'is_active': c.is_active, 'notes': c.notes,
    })


@login_required
@require_permission('change_insurance_companies')
def company_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)
    try:
        c = InsuranceCompany.objects.get(tenant=tenant, pk=pk)
    except InsuranceCompany.DoesNotExist:
        return _json_error('شركة التأمين غير موجودة', status=404)

    form = InsuranceCompanyForm(request.POST, instance=c)
    if form.is_valid():
        updated = form.save(commit=False)
        updated.updated_by = request.user
        updated.save()
        log_activity(request, 'تعديل شركة تأمين', updated.name, 'update')
        return _json_ok(msg='تم تحديث شركة التأمين بنجاح')
    return JsonResponse({'success': False, 'errors': _serialize_form_errors(form)}, status=400, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('change_insurance_companies')
def company_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)
    try:
        c = InsuranceCompany.objects.get(tenant=tenant, pk=pk)
    except InsuranceCompany.DoesNotExist:
        return _json_error('شركة التأمين غير موجودة', status=404)
    if c.claims.exclude(status='cancelled').exists():
        return _json_error('لا يمكن حذف شركة تأمين لها مطالبات نشطة — يمكنك تعطيلها بدلاً من ذلك.')
    name = c.name
    c.delete()
    log_activity(request, 'حذف شركة تأمين', name, 'delete')
    return _json_ok(msg=f'تم حذف "{name}" بنجاح')


@login_required
@require_permission('view_insurance_companies')
def active_companies_api(request):
    """قائمة شركات التأمين النشطة — تُستخدم لتسجيل بطاقة جديدة أول مرة."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    companies = InsuranceCompany.objects.filter(tenant=tenant, is_active=True).order_by('name')
    data = [
        {'id': c.id, 'name': c.name, 'default_coverage_percent': str(c.default_coverage_percent)}
        for c in companies
    ]
    return _json_ok(data)


# ════════════════════════════════════════════════════════════
# Insurance card lookup / quick registration
# ════════════════════════════════════════════════════════════
# لا توجد شاشة إدارة "بوليصات" مستقلة — البوليصة والعضوية بيانات شركة
# التأمين نفسها، لا تديرها الصيدلية. الصيدلية فقط تتعرّف على المشترك ببطاقته
# وقت البيع، وتسجّلها تلقائياً لتسريع الزيارات القادمة لنفس البطاقة.

@login_required
@require_permission('view_insurance_companies')
def card_lookup_api(request):
    """
    البحث عن مشترك تأمين برقم بطاقته والتحقق من صلاحيته — نقطة الدخول
    الأساسية لبيع بالتأمين من POS/الفاتورة اليدوية.
    """
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    card_number = request.GET.get('card_number', '')
    try:
        result = services.lookup_insurance_card(tenant, card_number)
    except ValueError as e:
        return _json_error(str(e), status=404)
    return _json_ok({
        'member_id': result['member'].id, 'member_name': result['member'].full_name,
        'card_number': result['member'].card_number,
        'insurance_company_id': result['insurance_company'].id,
        'insurance_company_name': result['insurance_company'].name,
        'coverage_percent': str(result['coverage_percent']),
    })


@login_required
@require_permission('add_insurance_companies')
def card_quick_register_api(request):
    """تسجيل سريع لمشترك جديد أول مرة، مباشرة من شاشة البيع."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)

    data = {
        'insurance_company_id': request.POST.get('insurance_company_id'),
        'card_number': request.POST.get('card_number'),
        'full_name': request.POST.get('full_name'),
        'member_id': request.POST.get('member_id'),
        'coverage_percent': request.POST.get('coverage_percent'),
    }
    try:
        result = services.quick_register_card(tenant, data, request.user)
    except ValueError as e:
        return _json_error(str(e))
    log_activity(request, 'تسجيل مشترك تأمين جديد', result['member'].card_number, 'create')
    return _json_ok({
        'member_id': result['member'].id, 'member_name': result['member'].full_name,
        'card_number': result['member'].card_number,
        'insurance_company_id': result['insurance_company'].id,
        'insurance_company_name': result['insurance_company'].name,
        'coverage_percent': str(result['coverage_percent']),
    }, 'تم تسجيل المشترك بنجاح')

# ════════════════════════════════════════════════════════════
# Claims
# ════════════════════════════════════════════════════════════

@login_required
@require_permission('view_insurance_claims')
def claim_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')
    return render(request, 'insurance/claim_list.html', {
        'status_choices': InsuranceClaim.STATUS_CHOICES,
        'section': 'insurance_claims',
    })


@login_required
@require_permission('view_insurance_claims')
def claim_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    status = request.GET.get('status', '').strip()
    search = request.GET.get('search[value]', '').strip()

    qs = InsuranceClaim.objects.filter(tenant=tenant).select_related('customer', 'member', 'insurance_company', 'invoice')
    records_total = qs.count()

    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(
            Q(claim_number__icontains=search) | Q(member__full_name__icontains=search) |
            Q(card_number__icontains=search) |
            Q(invoice__invoice_number__icontains=search) | Q(insurance_company__name__icontains=search)
        )
    records_filtered = qs.count()

    qs = qs.order_by('-created_at')[start:start + length]

    data = [
        {
            'id': c.id, 'claim_number': c.claim_number,
            'invoice_number': c.invoice.invoice_number,
            'member_name': c.member.full_name if c.member else (c.customer.name if c.customer else '—'),
            'card_number': c.card_number or '—',
            'insurance_company_name': c.insurance_company.name,
            'status': c.status, 'status_display': c.get_status_display(),
            'covered_amount': str(c.covered_amount), 'approved_amount': str(c.approved_amount) if c.approved_amount is not None else None,
            'paid_amount': str(c.paid_amount), 'remaining_amount': str(c.remaining_amount),
        }
        for c in qs
    ]
    return JsonResponse({'draw': draw, 'recordsTotal': records_total,
                          'recordsFiltered': records_filtered, 'data': data})


@login_required
@require_permission('view_insurance_claims')
def invoice_eligible_lines_api(request, invoice_id):
    """يُعيد بنود فاتورة مؤكدة لاختيار ما يُطالَب به — لإنشاء مطالبة تأمين لاحقاً لفاتورة لم تُربط بتأمين وقت البيع."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    try:
        invoice = SaleInvoice.objects.get(tenant=tenant, pk=invoice_id)
    except SaleInvoice.DoesNotExist:
        return _json_error('الفاتورة غير موجودة', status=404)

    if hasattr(invoice, 'insurance_claim'):
        return _json_error('يوجد بالفعل مطالبة تأمين لهذه الفاتورة.')

    lines = [
        {
            'id': line.id, 'item_name': line.item.name,
            'quantity': str(line.quantity), 'line_total': str(line.line_total),
            'is_insurance_excluded': line.item.is_insurance_excluded,
        }
        for line in invoice.lines.select_related('item').all()
    ]
    return _json_ok({'lines': lines, 'grand_total': str(invoice.grand_total)})


@login_required
@require_permission('add_insurance_claims')
def claim_create_api(request):
    """إنشاء مطالبة تأمين لفاتورة مؤكدة مسبقاً عبر البحث برقم البطاقة."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)

    import json
    try:
        body = json.loads(request.body)
    except (ValueError, TypeError):
        return _json_error('بيانات غير صالحة')

    invoice_id = body.get('invoice_id')
    card_number = body.get('card_number')
    line_selections = body.get('lines', [])

    try:
        invoice = SaleInvoice.objects.get(tenant=tenant, pk=invoice_id)
    except SaleInvoice.DoesNotExist:
        return _json_error('الفاتورة غير موجودة')

    try:
        result = services.lookup_insurance_card(tenant, card_number)
    except ValueError as e:
        return _json_error(str(e))

    try:
        claim = services.create_claim_for_invoice(
            invoice, result['insurance_company'], line_selections, request.user,
            member=result['member'], card_number=result['member'].card_number,
        )
    except ValueError as e:
        return _json_error(str(e))

    log_activity(request, 'إنشاء مطالبة تأمين', claim.claim_number, 'create')
    return _json_ok({'id': claim.id, 'claim_number': claim.claim_number}, 'تم إنشاء المطالبة بنجاح')


@login_required
@require_permission('view_insurance_claims')
def claim_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    try:
        c = InsuranceClaim.objects.select_related('customer', 'member', 'insurance_company', 'invoice').get(tenant=tenant, pk=pk)
    except InsuranceClaim.DoesNotExist:
        return _json_error('المطالبة غير موجودة', status=404)

    lines = [
        {
            'item_name': line.invoice_line.item.name, 'coverage_percent': str(line.coverage_percent),
            'claimed_amount': str(line.claimed_amount),
            'approved_amount': str(line.approved_amount) if line.approved_amount is not None else None,
        }
        for line in c.lines.select_related('invoice_line__item').all()
    ]
    return _json_ok({
        'id': c.id, 'claim_number': c.claim_number, 'status': c.status, 'status_display': c.get_status_display(),
        'invoice_number': c.invoice.invoice_number,
        'member_name': c.member.full_name if c.member else '—',
        'card_number': c.card_number or '—',
        'customer_name': c.customer.name if c.customer else 'بدون عميل مسجَّل',
        'insurance_company_name': c.insurance_company.name,
        'covered_amount': str(c.covered_amount), 'patient_amount': str(c.patient_amount),
        'approved_amount': str(c.approved_amount) if c.approved_amount is not None else None,
        'paid_amount': str(c.paid_amount), 'remaining_amount': str(c.remaining_amount),
        'due_date': c.due_date.isoformat() if c.due_date else None,
        'rejection_reason': c.rejection_reason, 'lines': lines,
    })


@login_required
@require_permission('submit_insurance_claims')
def claim_submit_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)
    try:
        claim = InsuranceClaim.objects.get(tenant=tenant, pk=pk)
    except InsuranceClaim.DoesNotExist:
        return _json_error('المطالبة غير موجودة', status=404)
    try:
        services.submit_claim(claim, request.user)
    except ValueError as e:
        return _json_error(str(e))
    log_activity(request, 'تقديم مطالبة تأمين', claim.claim_number, 'update')
    return _json_ok(msg='تم تقديم المطالبة بنجاح')


@login_required
@require_permission('respond_insurance_claims')
def claim_respond_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)
    try:
        claim = InsuranceClaim.objects.get(tenant=tenant, pk=pk)
    except InsuranceClaim.DoesNotExist:
        return _json_error('المطالبة غير موجودة', status=404)

    status = request.POST.get('status')
    reason = request.POST.get('rejection_reason', '')
    try:
        approved_amount = Decimal(request.POST.get('approved_amount') or '0')
    except InvalidOperation:
        return _json_error('مبلغ الاعتماد غير صالح')

    try:
        services.record_claim_response(claim, approved_amount, status, reason, request.user)
    except ValueError as e:
        return _json_error(str(e))
    log_activity(request, 'رد على مطالبة تأمين', claim.claim_number, 'update')
    return _json_ok(msg='تم تسجيل رد شركة التأمين')


@login_required
@require_permission('settle_insurance_claims')
def claim_settle_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)
    try:
        claim = InsuranceClaim.objects.get(tenant=tenant, pk=pk)
    except InsuranceClaim.DoesNotExist:
        return _json_error('المطالبة غير موجودة', status=404)

    try:
        amount = Decimal(request.POST.get('amount') or '0')
    except InvalidOperation:
        return _json_error('مبلغ غير صالح')
    reference = request.POST.get('reference', '')
    date = request.POST.get('date') or timezone.localdate()
    received_method = request.POST.get('received_method', 'cash')

    try:
        services.settle_claim_payment(claim, amount, date, reference, request.user, received_method=received_method)
    except ValueError as e:
        return _json_error(str(e))
    log_activity(request, 'تسوية مطالبة تأمين', claim.claim_number, 'update')
    return _json_ok(msg='تم تسجيل التسوية بنجاح')


@login_required
@require_permission('cancel_insurance_claims')
def claim_cancel_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('طريقة غير مسموحة', status=405)
    try:
        claim = InsuranceClaim.objects.get(tenant=tenant, pk=pk)
    except InsuranceClaim.DoesNotExist:
        return _json_error('المطالبة غير موجودة', status=404)

    reason = request.POST.get('reason', '')
    try:
        services.cancel_claim(claim, request.user, reason)
    except ValueError as e:
        return _json_error(str(e))
    log_activity(request, 'إلغاء مطالبة تأمين', claim.claim_number, 'delete')
    return _json_ok(msg='تم إلغاء المطالبة')


# ════════════════════════════════════════════════════════════
# Statement
# ════════════════════════════════════════════════════════════

@login_required
@require_permission('view_insurance_statement')
def insurance_statement(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    company_id = request.GET.get('company_id')
    companies = InsuranceCompany.objects.filter(tenant=tenant, is_active=True).order_by('name')
    entries = []
    selected_company = None
    if company_id:
        selected_company = companies.filter(pk=company_id).first()
        if selected_company:
            entries = InsuranceClaimSettlement.objects.filter(
                tenant=tenant, insurance_company=selected_company
            ).select_related('claim').order_by('entry_date', 'id')

    return render(request, 'insurance/statement.html', {
        'companies': companies,
        'selected_company': selected_company,
        'entries': entries,
        'section': 'insurance_statement',
    })


@login_required
@require_permission('view_insurance_claims_aging')
def claims_aging_report(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    report = InsuranceReportGenerator(tenant).get_claims_aging_report()

    return render(request, 'insurance/claims_aging.html', {
        'report': report,
        'section': 'insurance_claims_aging',
    })
