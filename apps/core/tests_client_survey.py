"""
استبيانات تعريف العملاء: رابط عام لكل عميل، رد يُحفظ ويُولَّد منه PDF يُرسل بالإيميل،
وإدارة الروابط من لوحة المنصة.
"""
import shutil
import tempfile

import pymupdf
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.core import survey_schema as S
from apps.core.models import ClientSurvey

_MEDIA = tempfile.mkdtemp(prefix='survey-test-media-')


def _full_post(form_type, **over):
    """رد كامل يغطي كل نوع سؤال في الاستبيان."""
    post = {}
    for q in S.iter_questions(form_type):
        key = f'q_{q["id"]}'
        if q['type'] in ('text', 'number'):
            post[key] = '5'
        elif q['type'] == 'textarea':
            post[key] = 'سطر أول\nسطر ثانٍ'
        elif q['type'] == 'radio':
            post[key] = q['options'][0]['value']
        elif q['type'] == 'checkbox':
            post[key] = [o['value'] for o in q['options'][:2]]
        elif q['type'] == 'count_list':
            for it in q['items'][:3]:
                post[f'{key}__{it["value"]}'] = '1'
                post[f'{key}__{it["value"]}__n'] = '2'
    post.update({'q_org_name': 'شركة النور', 'q_contact_name': 'أحمد', 'q_phone': '0912345678'})
    post.update(over)
    return post


@override_settings(MEDIA_ROOT=_MEDIA, SURVEY_RESULTS_EMAIL='gnabi1998@gmail.com',
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ClientSurveyTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.admin = User.objects.create_superuser(username='sv-root', password='secret123')

    def _make(self, form_type='distribution'):
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('core:admin_survey_create'),
                                {'form_type': form_type, 'client_name': 'عميل مستهدف'})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.client.logout()
        return ClientSurvey.objects.get()

    # ── الإنشاء والصلاحيات ──
    def test_create_requires_platform_permission(self):
        # RequestFactory: نتجاوز الـ middleware لنختبر فحص الصلاحية في العرض نفسه
        from django.test import RequestFactory
        from apps.core.survey_views import admin_survey_create, admin_surveys
        staff = User(username='staff-noperm', is_platform_staff=True, platform_permissions=[])
        req = RequestFactory().post('/x', {'form_type': 'pharmacy', 'client_name': 'x'})
        req.user = staff
        self.assertEqual(admin_survey_create(req).status_code, 403)
        self.assertFalse(ClientSurvey.objects.exists())
        req = RequestFactory().get('/x')
        req.user = staff
        self.assertEqual(admin_surveys(req).status_code, 302)  # → no_permission

    def test_create_returns_public_url_and_validates(self):
        self.client.force_login(self.admin)
        bad = self.client.post(reverse('core:admin_survey_create'), {'form_type': 'zzz', 'client_name': 'x'})
        self.assertEqual(bad.status_code, 400)
        survey = self._make('pharmacy')
        self.assertTrue(survey.token)
        self.assertIn(survey.token, reverse('core:survey_public', args=[survey.token]))

    def test_admin_page_lists_links(self):
        survey = self._make()
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('core:admin_surveys'))
        self.assertContains(resp, survey.client_name)
        self.assertContains(resp, survey.token)

    # ── الصفحة العامة ──
    def test_public_form_renders_without_login_for_both_forms(self):
        for form_type in ('distribution', 'pharmacy'):
            ClientSurvey.objects.all().delete()
            survey = self._make(form_type)
            resp = self.client.get(reverse('core:survey_public', args=[survey.token]))
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, S.FORM_TITLES[form_type])
            survey.refresh_from_db()
            self.assertIsNotNone(survey.opened_at)

    def test_unknown_token_is_404(self):
        self.assertEqual(self.client.get(reverse('core:survey_public', args=['nope'])).status_code, 404)

    def test_required_fields_block_submission(self):
        survey = self._make()
        resp = self.client.post(reverse('core:survey_public', args=[survey.token]),
                                {'q_org_name': 'فقط الاسم'})
        self.assertContains(resp, 'اسم المسؤول مطلوب')
        survey.refresh_from_db()
        self.assertFalse(survey.is_submitted)
        self.assertEqual(len(mail.outbox), 0)

    def test_submission_creates_pdf_and_emails_it(self):
        for form_type in ('distribution', 'pharmacy'):
            ClientSurvey.objects.all().delete()
            mail.outbox.clear()
            survey = self._make(form_type)
            resp = self.client.post(reverse('core:survey_public', args=[survey.token]),
                                    _full_post(form_type), follow=True)
            self.assertContains(resp, 'تم استلام إجاباتك')
            survey.refresh_from_db()
            self.assertTrue(survey.is_submitted)
            self.assertEqual(survey.answers['org_name'], 'شركة النور')
            self.assertIsNotNone(survey.email_sent_at)

            self.assertEqual(len(mail.outbox), 1)
            msg = mail.outbox[0]
            self.assertEqual(msg.to, ['gnabi1998@gmail.com'])
            name, content, mimetype = msg.attachments[0]
            self.assertEqual(mimetype, 'application/pdf')
            doc = pymupdf.open('pdf', content)
            self.assertGreaterEqual(doc.page_count, 3)
            text = ''.join(p.get_text() for p in doc)
            self.assertIn('5', text)
            doc.close()

    def test_second_submission_is_ignored(self):
        survey = self._make()
        url = reverse('core:survey_public', args=[survey.token])
        self.client.post(url, _full_post('distribution'))
        self.client.post(url, _full_post('distribution', q_org_name='محاولة ثانية'))
        survey.refresh_from_db()
        self.assertEqual(survey.answers['org_name'], 'شركة النور')
        self.assertEqual(len(mail.outbox), 1)

    def test_email_failure_keeps_the_response(self):
        survey = self._make()
        with override_settings(SURVEY_RESULTS_EMAIL=''):
            self.client.post(reverse('core:survey_public', args=[survey.token]), _full_post('distribution'))
        survey.refresh_from_db()
        self.assertTrue(survey.is_submitted)
        self.assertTrue(survey.pdf_file)
        self.assertTrue(survey.email_error)
        # إعادة الإرسال من اللوحة بعد إصلاح الإعداد
        self.client.force_login(self.admin)
        resp = self.client.post(reverse('core:admin_survey_resend', args=[survey.pk]))
        self.assertTrue(resp.json()['success'])
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_choices_are_dropped(self):
        survey = self._make()
        self.client.post(reverse('core:survey_public', args=[survey.token]),
                         _full_post('distribution', q_structure='<script>', q_features=['store', 'evil']))
        survey.refresh_from_db()
        self.assertEqual(survey.answers['structure'], '')
        self.assertEqual(survey.answers['features'], ['store'])

    # ── التنزيل والحذف ──
    def test_pdf_download_and_delete(self):
        survey = self._make()
        self.client.post(reverse('core:survey_public', args=[survey.token]), _full_post('distribution'))
        self.client.force_login(self.admin)
        resp = self.client.get(reverse('core:admin_survey_pdf', args=[survey.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertTrue(b''.join(resp.streaming_content).startswith(b'%PDF'))
        self.client.post(reverse('core:admin_survey_delete', args=[survey.pk]))
        self.assertFalse(ClientSurvey.objects.exists())
