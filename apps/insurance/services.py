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


@transaction.atomic
def create_claim_for_invoice(invoice, policy, line_selections, user):
    """
    ينشئ مطالبة تأمين جديدة (مسودة) لفاتورة مؤكدة.
    line_selections: [{'invoice_line_id': int, 'coverage_percent': Decimal}, ...]
    """
    if hasattr(invoice, 'insurance_claim'):
        raise ValueError('يوجد بالفعل مطالبة تأمين مرتبطة بهذه الفاتورة.')
    if invoice.status not in ('confirmed', 'partially_returned'):
        raise ValueError('لا يمكن إنشاء مطالبة تأمين إلا لفاتورة مؤكدة.')
    if not invoice.customer:
        raise ValueError('لا يمكن إنشاء مطالبة تأمين لفاتورة غير مرتبطة بعميل.')
    if not line_selections:
        raise ValueError('يجب اختيار بند واحد على الأقل للمطالبة.')

    tenant = invoice.tenant
    claim = InsuranceClaim.objects.create(
        tenant=tenant,
        invoice=invoice,
        customer=invoice.customer,
        insurance_company=policy.insurance_company,
        policy=policy,
        status='draft',
    )

    covered_total = Decimal('0')
    for sel in line_selections:
        line = invoice.lines.get(pk=sel['invoice_line_id'])
        pct = Decimal(str(sel.get('coverage_percent') or policy.effective_coverage_percent))
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
def settle_claim_payment(claim, amount, date, reference, user, treasury=None):
    """يُسجِّل دفعة تسوية من شركة التأمين — تُقفَل عبر apps.sales.services.record_customer_payment."""
    from apps.sales.services import record_customer_payment

    claim = InsuranceClaim.objects.select_for_update().get(pk=claim.pk)
    if claim.status not in ('approved', 'partially_approved', 'partially_paid'):
        raise ValueError('لا يمكن تسجيل تسوية إلا لمطالبة معتمدة.')

    amount = Decimal(str(amount or 0))
    if amount <= 0:
        raise ValueError('مبلغ التسوية يجب أن يكون أكبر من صفر.')
    if amount > claim.remaining_amount + Decimal('0.01'):
        raise ValueError(f'المبلغ ({amount}) يتجاوز المتبقي على المطالبة ({claim.remaining_amount}).')

    payment = record_customer_payment(
        invoice=claim.invoice, amount=amount, method='insurance', date=date,
        reference=reference, notes=f'تسوية مطالبة تأمين {claim.claim_number}', user=user,
        treasury=treasury,
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
