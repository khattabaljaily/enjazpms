"""
وحدة التأمين — منطق الأعمال (Services)
========================================

هذا الملف هو المكان الوحيد الذي يُعدِّل:
  - InsuranceClaim / InsuranceClaimLine
  - InsuranceClaimSettlement

لا يُعدِّل جداول apps.sales مباشرة أبداً — يستدعي دوال apps.sales.services
(بنفس أسلوب apps.agents.services)، لأن apps/sales/services.py هو المصدر
الوحيد المسموح له بتعديل StockQuantity/StockMovement/SalePayment/CustomerLedger.

قاعدة العمل المتفق عليها: لو شركة التأمين اعتمدت مبلغاً أقل من المطالَب به،
الفرق يُسجَّل كخسارة (writeoff) على الصيدلية — لا تتم إعادة فوترة المريض.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .models import InsuranceClaim, InsuranceClaimLine, InsuranceClaimSettlement


def _apply_settlement_ledger(tenant, insurance_company, claim, amount, entry_type,
                              date, reference_type='', reference_id=None, notes='',
                              is_reversal=False):
    """يُنشئ قيداً في InsuranceClaimSettlement برصيد تراكمي محدَّث."""
    from django.db.models import Sum

    prev = (
        InsuranceClaimSettlement.objects
        .filter(tenant=tenant, insurance_company=insurance_company)
        .aggregate(s=Sum('amount'))['s'] or Decimal('0')
    )
    return InsuranceClaimSettlement.objects.create(
        tenant=tenant,
        insurance_company=insurance_company,
        claim=claim,
        entry_type=entry_type,
        amount=amount,
        entry_date=date,
        reference_type=reference_type,
        reference_id=reference_id,
        running_balance=prev + amount,
        is_reversal=is_reversal,
        notes=notes,
    )


def build_line_selections(invoice, coverage_percent):
    """
    يبني قائمة الأصناف المؤهلة للتأمين في فاتورة (يستبعد is_insurance_excluded)
    مع مبلغ التغطية الإجمالي المتوقع — مصدر واحد يُستخدم قبل التأكيد (لتقدير
    insurance_amount) وبعده (لإنشاء المطالبة الفعلية) بنفس الأرقام بالضبط.
    """
    line_selections = []
    covered_total = Decimal('0')
    pct = Decimal(str(coverage_percent or 0))
    for line in invoice.lines.select_related('item').all():
        if line.item.is_insurance_excluded:
            continue
        amt = (line.line_total * pct / Decimal('100')).quantize(Decimal('0.01'))
        line_selections.append({'invoice_line_id': line.id, 'coverage_percent': pct})
        covered_total += amt
    return line_selections, covered_total


def lookup_insurance_card(tenant, card_number):
    """
    يبحث عن مشترك تأمين برقم بطاقته ويتحقق من صلاحيته: العضو نشط وشركة التأمين
    نشطة. البوليصة والعضوية نفسها بيانات شركة التأمين — لسنا مصدرها، فقط
    نسجّل رقم البطاقة والاسم والنسبة كما وردت لتسريع الزيارات القادمة.
    """
    from .models import InsuranceMember

    card_number = (card_number or '').strip()
    if not card_number:
        raise ValueError('أدخل رقم البطاقة.')

    try:
        member = InsuranceMember.objects.select_related('insurance_company').get(
            tenant=tenant, card_number=card_number,
        )
    except InsuranceMember.DoesNotExist:
        raise ValueError('لا توجد بطاقة بهذا الرقم — يمكنك تسجيلها كبطاقة جديدة.')

    if not member.is_active:
        raise ValueError('عضوية هذا المشترك غير نشطة.')
    if not member.insurance_company.is_active:
        raise ValueError('شركة التأمين غير نشطة.')

    return {
        'member': member, 'insurance_company': member.insurance_company,
        'coverage_percent': member.effective_coverage_percent,
    }


@transaction.atomic
def quick_register_card(tenant, data, user):
    """
    تسجيل سريع لبطاقة تأمين جديدة عند أول زيارة لمشترك غير مسجَّل من قبل —
    مباشرة من شاشة البيع، بدون أي شاشة إدارة منفصلة.
    """
    from .models import InsuranceCompany, InsuranceMember

    company_id = data.get('insurance_company_id')
    if not company_id:
        raise ValueError('اختر شركة التأمين.')
    try:
        company = InsuranceCompany.objects.get(tenant=tenant, pk=company_id, is_active=True)
    except InsuranceCompany.DoesNotExist:
        raise ValueError('شركة التأمين غير موجودة أو غير نشطة.')

    card_number = (data.get('card_number') or '').strip()
    if not card_number:
        raise ValueError('أدخل رقم البطاقة.')
    if InsuranceMember.objects.filter(tenant=tenant, card_number=card_number).exists():
        raise ValueError('يوجد مشترك بهذا الرقم مسجَّل مسبقاً — ابحث عنه بدل تسجيله من جديد.')

    full_name = (data.get('full_name') or '').strip()
    if not full_name:
        raise ValueError('أدخل اسم المشترك.')

    member = InsuranceMember.objects.create(
        tenant=tenant, insurance_company=company, card_number=card_number,
        member_id=(data.get('member_id') or '').strip(), full_name=full_name,
        coverage_percent=Decimal(str(data['coverage_percent'])) if data.get('coverage_percent') else None,
        is_active=True, created_by=user, updated_by=user,
    )
    return {
        'member': member, 'insurance_company': company,
        'coverage_percent': member.effective_coverage_percent,
    }


def create_claim_from_sale(invoice, tenant, insurance_coverage_percent, user):
    """
    ينشئ مطالبة تأمين تلقائياً بعد تأكيد فاتورة تحمل بيانات تأمين — نقطة
    استدعاء واحدة مشتركة بين POS والفاتورة اليدوية (إنشاء وتعديل). يتجاهل
    الطلب بصمت لو مفيش عضو تأمين مرتبط بالفاتورة أصلاً.
    """
    if not invoice.insurance_member_id:
        return None
    if invoice.payment_method != 'mixed':
        raise ValueError('تفعيل التأمين يتطلب اختيار طريقة دفع «مختلط».')

    member = invoice.insurance_member
    coverage_percent = Decimal(str(insurance_coverage_percent)) if insurance_coverage_percent else member.effective_coverage_percent

    line_selections, _covered_total = build_line_selections(invoice, coverage_percent)
    if not line_selections:
        return None
    return create_claim_for_invoice(
        invoice, member.insurance_company, line_selections, user,
        member=member, card_number=invoice.insurance_card_number,
    )


def discard_draft_claim(invoice):
    """
    يحذف مطالبة تأمين لسه مسودة (لم تُقدَّم لشركة التأمين) مرتبطة بالفاتورة،
    إن وُجدت — يُستدعى قبل أي تعديل/إلغاء/استرجاع يغيّر مبلغ الفاتورة، لضمان
    عدم بقاء مطالبة بأرقام قديمة/غير صحيحة. لا يلمس أي مطالبة تجاوزت draft.
    """
    if hasattr(invoice, 'insurance_claim') and invoice.insurance_claim.status == 'draft':
        claim = invoice.insurance_claim
        InsuranceClaimLine.objects.filter(claim=claim).delete()
        claim.delete()
        # لازم نمسح الكاش الداخلي لـ OneToOne العكسي على invoice نفسه، وإلا
        # hasattr(invoice, 'insurance_claim') لاحقاً في نفس الطلب هيرجع True
        # برغم إن الصف اتحذف فعلاً من القاعدة (كاش Django القياسي لعلاقات o2o).
        invoice._state.fields_cache.pop('insurance_claim', None)
        return True
    return False


@transaction.atomic
def create_claim_for_invoice(invoice, insurance_company, line_selections, user,
                              member=None, card_number=''):
    """
    ينشئ مطالبة تأمين جديدة (مسودة) لفاتورة مؤكدة.
    line_selections: [{'invoice_line_id': int, 'coverage_percent': Decimal}, ...]
    """
    if hasattr(invoice, 'insurance_claim'):
        raise ValueError('يوجد بالفعل مطالبة تأمين مرتبطة بهذه الفاتورة.')
    if invoice.status not in ('confirmed', 'partially_returned'):
        raise ValueError('لا يمكن إنشاء مطالبة تأمين إلا لفاتورة مؤكدة.')
    if not line_selections:
        raise ValueError('يجب اختيار بند واحد على الأقل للمطالبة.')

    tenant = invoice.tenant
    claim = InsuranceClaim.objects.create(
        tenant=tenant,
        invoice=invoice,
        customer=invoice.customer,
        insurance_company=insurance_company,
        member=member,
        card_number=card_number or '',
        status='draft',
    )

    covered_total = Decimal('0')
    default_pct = member.effective_coverage_percent if member else Decimal('0')
    for sel in line_selections:
        line = invoice.lines.get(pk=sel['invoice_line_id'])
        pct = Decimal(str(sel.get('coverage_percent') or default_pct))
        claimed = (line.line_total * pct / Decimal('100')).quantize(Decimal('0.01'))
        InsuranceClaimLine.objects.create(
            tenant=tenant,
            claim=claim,
            invoice_line=line,
            coverage_percent=pct,
            claimed_amount=claimed,
        )
        covered_total += claimed

    claim.covered_amount = covered_total
    claim.patient_amount = (invoice.grand_total - covered_total).quantize(Decimal('0.01'))
    claim.save(update_fields=['covered_amount', 'patient_amount'])
    return claim


@transaction.atomic
def submit_claim(claim, user):
    from datetime import timedelta

    claim = InsuranceClaim.objects.select_for_update().get(pk=claim.pk)
    if claim.status != 'draft':
        raise ValueError('لا يمكن تقديم المطالبة إلا وهي في حالة مسودة.')

    today = timezone.localdate()
    claim.status = 'submitted'
    claim.submitted_at = timezone.now()
    claim.submitted_by = user
    claim.due_date = today + timedelta(days=claim.insurance_company.settlement_period_days)
    claim.save(update_fields=['status', 'submitted_at', 'submitted_by', 'due_date'])

    _apply_settlement_ledger(
        tenant=claim.tenant, insurance_company=claim.insurance_company, claim=claim,
        amount=claim.covered_amount, entry_type='claim_submitted', date=today,
        reference_type='insurance_claim', reference_id=claim.id,
        notes=f'مطالبة {claim.claim_number} — فاتورة {claim.invoice.invoice_number}',
    )
    return claim


@transaction.atomic
def record_claim_response(claim, approved_amount, status, rejection_reason, user):
    """
    status: 'approved' | 'partially_approved' | 'rejected'.
    الفرق بين المطالَب به والمعتمد يُسجَّل كخسارة (writeoff) على الصيدلية.
    """
    claim = InsuranceClaim.objects.select_for_update().get(pk=claim.pk)
    if claim.status != 'submitted':
        raise ValueError('لا يمكن تسجيل رد إلا لمطالبة مُقدَّمة.')
    if status not in ('approved', 'partially_approved', 'rejected'):
        raise ValueError('حالة رد غير صالحة.')

    approved_amount = Decimal(str(approved_amount or 0))
    today = timezone.localdate()

    claim.status = status
    claim.approved_amount = approved_amount
    claim.responded_at = timezone.now()
    claim.rejection_reason = rejection_reason or ''
    claim.save(update_fields=['status', 'approved_amount', 'responded_at', 'rejection_reason'])

    shortfall = claim.covered_amount - approved_amount
    if shortfall > 0:
        from apps.sales.services import write_off_invoice_balance

        _apply_settlement_ledger(
            tenant=claim.tenant, insurance_company=claim.insurance_company, claim=claim,
            amount=-shortfall, entry_type='writeoff', date=today,
            reference_type='insurance_claim', reference_id=claim.id,
            notes=f'خسارة على شطب فرق الاعتماد — مطالبة {claim.claim_number}',
        )
        # الفرق خسارة على الصيدلية — لا يُعاد فوترته للعميل، فيُشطب من رصيد الفاتورة مباشرة
        write_off_invoice_balance(
            claim.invoice, shortfall,
            reason=f'خسارة على مطالبة تأمين {claim.claim_number} — فرق الاعتماد',
            user=user,
        )
    return claim


@transaction.atomic
def settle_claim_payment(claim, amount, date, reference, user, treasury=None, received_method='cash'):
    """
    يُسجِّل دفعة تسوية من شركة التأمين — تُقفَل عبر apps.sales.services.record_customer_payment.

    received_method: 'cash' أو 'bank' — طريقة استلام شركة التأمين للمبلغ فعلياً.
    'cash' يُرحَّل للخزينة كوارد (مثل تحصيل نقدي عادي)، و'bank' يُسجَّل فقط
    دون أثر على رصيد الخزينة — بنفس منطق طريقة الدفع 'bank' لدفعات العملاء.
    """
    from apps.sales.services import record_customer_payment

    if received_method not in ('cash', 'bank'):
        raise ValueError('طريقة الاستلام يجب أن تكون نقداً أو تحويلاً بنكياً.')

    claim = InsuranceClaim.objects.select_for_update().get(pk=claim.pk)
    if claim.status not in ('approved', 'partially_approved', 'partially_paid'):
        raise ValueError('لا يمكن تسجيل تسوية إلا لمطالبة معتمدة.')

    amount = Decimal(str(amount or 0))
    if amount <= 0:
        raise ValueError('مبلغ التسوية يجب أن يكون أكبر من صفر.')
    if amount > claim.remaining_amount + Decimal('0.01'):
        raise ValueError(f'المبلغ ({amount}) يتجاوز المتبقي على المطالبة ({claim.remaining_amount}).')

    method_label = 'نقداً' if received_method == 'cash' else 'تحويلاً بنكياً'
    payment = record_customer_payment(
        invoice=claim.invoice, amount=amount, method='insurance', date=date,
        reference=reference, notes=f'تسوية مطالبة تأمين {claim.claim_number} — استُلمت {method_label}', user=user,
        treasury=treasury, post_to_treasury=(received_method == 'cash'),
    )

    claim.paid_amount = (claim.paid_amount or Decimal('0')) + amount
    claim.status = 'paid' if claim.remaining_amount <= Decimal('0.01') else 'partially_paid'
    claim.save(update_fields=['paid_amount', 'status'])

    _apply_settlement_ledger(
        tenant=claim.tenant, insurance_company=claim.insurance_company, claim=claim,
        amount=-amount, entry_type='payment_received', date=date,
        reference_type='insurance_claim_payment', reference_id=payment.id if payment else None,
        notes=f'دفعة تسوية — مطالبة {claim.claim_number}',
    )
    return claim


@transaction.atomic
def cancel_claim(claim, user, reason=''):
    claim = InsuranceClaim.objects.select_for_update().get(pk=claim.pk)
    if claim.paid_amount and claim.paid_amount > 0:
        raise ValueError('لا يمكن إلغاء مطالبة تم تسديد جزء منها.')

    today = timezone.localdate()
    was_submitted = claim.status not in ('draft',)

    claim.status = 'cancelled'
    claim.notes = (claim.notes + '\n' if claim.notes else '') + f'أُلغيت: {reason}'.strip()
    claim.save(update_fields=['status', 'notes'])

    if was_submitted:
        _apply_settlement_ledger(
            tenant=claim.tenant, insurance_company=claim.insurance_company, claim=claim,
            amount=-claim.covered_amount, entry_type='claim_submitted', date=today,
            reference_type='insurance_claim', reference_id=claim.id,
            notes=f'إلغاء مطالبة {claim.claim_number}', is_reversal=True,
        )
    return claim
