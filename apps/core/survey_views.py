"""
استبيانات تعريف العملاء المستهدفين.

- صفحة عامة برابط خاص لكل عميل: /survey/<token>/ (بلا تسجيل دخول).
- عند الإرسال يُولَّد ملف PDF معبّأ بشكل الاستبيان ويُرسل بالإيميل إلى settings.SURVEY_RESULTS_EMAIL.
- لوحة مدير المنصة: إنشاء الروابط ومتابعة الردود وتنزيل الـ PDF وإعادة إرسال الإيميل.
"""
import logging
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import survey_schema as S
from .models import ClientSurvey
from .survey_pdf import render_survey_pdf

logger = logging.getLogger(__name__)

PERM = 'manage_marketing'


def _survey_url(request, survey):
    return request.build_absolute_uri(reverse('core:survey_public', args=[survey.token]))


def _pdf_filename(survey):
    stamp = timezone.localtime(survey.submitted_at or timezone.now()).strftime('%Y%m%d-%H%M')
    return f'survey-{survey.form_type}-{survey.pk}-{stamp}.pdf'


def _send_results_email(survey):
    """يرسل الـ PDF للجهة المحددة؛ يسجّل النتيجة على السجل ولا يرمي استثناءً."""
    recipient = getattr(settings, 'SURVEY_RESULTS_EMAIL', '')
    if not recipient:
        survey.email_error = 'لم يُضبط بريد استلام الردود (SURVEY_RESULTS_EMAIL)'
        survey.save(update_fields=['email_error'])
        return False
    a = survey.answers or {}
    org = a.get('org_name') or survey.client_name
    form_label = S.FORM_SHORT_TITLES[survey.form_type]
    body = (
        f'وصل رد جديد على استبيان تعريف العميل ({form_label}).\n\n'
        f'المنشأة: {org}\n'
        f'المسؤول: {a.get("contact_name", "") or "—"} ({a.get("contact_role", "") or "—"})\n'
        f'الهاتف: {a.get("phone", "") or "—"}\n'
        f'الاسم المسجّل في الرابط: {survey.client_name}\n'
        f'تاريخ الرد: {timezone.localtime(survey.submitted_at).strftime("%Y/%m/%d %H:%M")}\n\n'
        'الاستبيان المعبّأ مرفق بصيغة PDF.\n\n— منصة إنجاز'
    )
    try:
        msg = EmailMessage(
            subject=f'رد استبيان — {org} ({form_label})',
            body=body, to=[recipient],
        )
        with survey.pdf_file.open('rb') as f:
            msg.attach(_pdf_filename(survey), f.read(), 'application/pdf')
        msg.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 — الرد محفوظ مهما فشل الإيميل
        logger.exception('Survey email failed for survey %s', survey.pk)
        survey.email_error = str(exc)[:500]
        survey.save(update_fields=['email_error'])
        return False
    survey.email_sent_at = timezone.now()
    survey.email_error = ''
    survey.save(update_fields=['email_sent_at', 'email_error'])
    return True


# ═════════ الصفحة العامة للعميل ═════════

@never_cache
def survey_public(request, token):
    survey = get_object_or_404(ClientSurvey, token=token)

    if survey.is_submitted:
        return render(request, 'core/survey_thanks.html', {'survey': survey})

    errors = []
    answers = {}
    if request.method == 'POST':
        answers = S.parse_answers(survey.form_type, request.POST)
        if not answers.get('org_name'):
            errors.append('اسم النشاط / المنشأة مطلوب.')
        if not answers.get('contact_name'):
            errors.append('اسم المسؤول مطلوب.')
        if not answers.get('phone'):
            errors.append('رقم الهاتف / واتساب مطلوب.')
        if not errors:
            survey.answers = answers
            survey.submitted_at = timezone.now()
            try:
                pdf = render_survey_pdf(survey)
            except Exception:  # noqa: BLE001
                logger.exception('Survey PDF failed for survey %s', survey.pk)
                survey.submitted_at = None
                survey.answers = {}
                errors.append('تعذّر إنشاء الملف، حاول مرة أخرى بعد قليل.')
            else:
                survey.pdf_file.save(_pdf_filename(survey), ContentFile(pdf), save=False)
                survey.save()
                _send_results_email(survey)
                return redirect('core:survey_public', token=survey.token)

    if not survey.opened_at and request.method == 'GET':
        survey.opened_at = timezone.now()
        survey.save(update_fields=['opened_at'])

    return render(request, 'core/survey_public.html', {
        'survey': survey,
        'form_title': S.FORM_TITLES[survey.form_type],
        'intro': S.INTRO[survey.form_type],
        'important_note': S.IMPORTANT_NOTE,
        'sections': S.numbered_sections(survey.form_type),
        'answers': answers or survey.answers or {},
        'errors': errors,
    })


# ═════════ لوحة مدير المنصة ═════════

@login_required
def admin_surveys(request):
    if not request.user.has_platform_perm(PERM):
        return redirect('core:no_permission')
    surveys = list(ClientSurvey.objects.select_related('created_by')[:300])
    for s in surveys:
        s.public_url = _survey_url(request, s)
    return render(request, 'core/admin_surveys.html', {
        'surveys': surveys,
        'form_choices': ClientSurvey.FORM_CHOICES,
        'total': len(surveys),
        'submitted': sum(1 for s in surveys if s.is_submitted),
        'pending': sum(1 for s in surveys if not s.is_submitted),
        'results_email': getattr(settings, 'SURVEY_RESULTS_EMAIL', ''),
    })


def _guard(request):
    if not request.user.has_platform_perm(PERM):
        return JsonResponse({'success': False, 'message': 'غير مصرح'}, status=403)
    return None


@login_required
@require_POST
def admin_survey_create(request):
    err = _guard(request)
    if err:
        return err
    form_type = request.POST.get('form_type', '')
    client_name = request.POST.get('client_name', '').strip()
    if form_type not in S.SECTIONS:
        return JsonResponse({'success': False, 'message': 'نوع الاستبيان غير صالح'}, status=400)
    if not client_name:
        return JsonResponse({'success': False, 'message': 'اسم العميل مطلوب'}, status=400)
    survey = ClientSurvey.objects.create(
        token=secrets.token_urlsafe(18),
        form_type=form_type,
        client_name=client_name[:200],
        client_phone=request.POST.get('client_phone', '').strip()[:50],
        client_note=request.POST.get('client_note', '').strip()[:300],
        created_by=request.user,
    )
    return JsonResponse({'success': True, 'url': _survey_url(request, survey), 'id': survey.pk})


@login_required
@require_POST
def admin_survey_delete(request, pk):
    err = _guard(request)
    if err:
        return err
    survey = get_object_or_404(ClientSurvey, pk=pk)
    if survey.pdf_file:
        survey.pdf_file.delete(save=False)
    survey.delete()
    return JsonResponse({'success': True})


@login_required
@require_POST
def admin_survey_resend(request, pk):
    err = _guard(request)
    if err:
        return err
    survey = get_object_or_404(ClientSurvey, pk=pk)
    if not survey.is_submitted or not survey.pdf_file:
        return JsonResponse({'success': False, 'message': 'لم يرد العميل بعد'}, status=400)
    ok = _send_results_email(survey)
    return JsonResponse({
        'success': ok,
        'message': 'تم إرسال الإيميل' if ok else f'فشل الإرسال: {survey.email_error}',
    })


@login_required
def admin_survey_pdf(request, pk):
    if not request.user.has_platform_perm(PERM):
        return redirect('core:no_permission')
    survey = get_object_or_404(ClientSurvey, pk=pk)
    if not survey.pdf_file:
        raise Http404
    return FileResponse(survey.pdf_file.open('rb'), content_type='application/pdf',
                        filename=_pdf_filename(survey))
