"""
اختبار خطوة التسجيل الثانية (بيانات النشاط) — يغطي ثغرة حقيقية: عند العودة
لصفحة الخطوة الثانية (GET) بعد إرسالها بنجاح، اختيار "نوع النشاط" كان يُفقد
بصمت لأن المفتاح المحفوظ في الجلسة (business_type_id) يختلف عن اسم حقل
الفورم (business_type)، فيعود الفورم دائماً للنوع الافتراضي.
"""
from django.test import TestCase
from django.urls import reverse

from apps.core.models import BusinessType


class RegisterStep2SessionRoundTripTests(TestCase):
    def setUp(self):
        # نوعان نشطان بترتيب عرض مختلف حتى لا يتطابق الاختيار مع الافتراضي صدفة
        self.default_bt = BusinessType.objects.create(
            name='default-bt', name_ar='النوع الافتراضي', slug='default-bt', display_order=1,
        )
        self.chosen_bt = BusinessType.objects.create(
            name='chosen-bt', name_ar='النوع المختار', slug='chosen-bt', display_order=2,
        )

    def _complete_step1(self):
        session = self.client.session
        session['reg_step1'] = {
            'username': 'newowner', 'email': 'owner@example.com',
            'first_name': 'خطاب', 'last_name': 'الجعيلي',
        }
        session.save()

    def test_business_type_choice_survives_revisiting_step2(self):
        self._complete_step1()

        resp = self.client.post(reverse('accounts:register_step2'), {
            'business_type': self.chosen_bt.pk,
            'business_name': 'صيدلية الاختبار',
            'country': 'السودان',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload.get('success'), payload)

        # الجلسة يجب أن تحفظ اختيار المستخدم الفعلي، لا الافتراضي
        self.assertEqual(self.client.session['reg_step2']['business_type_id'], self.chosen_bt.pk)

        # العودة لصفحة الخطوة الثانية (GET) يجب أن تُظهر نفس الاختيار السابق
        resp2 = self.client.get(reverse('accounts:register_step2'))
        self.assertEqual(resp2.status_code, 200)
        form = resp2.context['form']
        self.assertEqual(str(form['business_type'].value()), str(self.chosen_bt.pk))
