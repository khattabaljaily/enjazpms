"""
نمط المشتريات يُحدَّد مرة واحدة عند إنشاء المشترك (التسجيل أو إضافة مشترك من لوحة المنصة)
ثم يُقفل نهائياً. مشتركو ما قبل الميزة (غير المقفولين) تحدد لهم إدارة المنصة النمط مرة واحدة.
"""
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.forms import RegistrationRequestForm, Step3SettingsForm
from apps.accounts.models import User
from apps.core.forms import TenantForm
from apps.core.models import BusinessType, Tenant


def _tenant(**extra):
    bt, _ = BusinessType.objects.get_or_create(slug='lock-bt', defaults={'name': 'lock', 'name_ar': 'lock'})
    data = dict(name='مشترك', business_type=bt, subscription_plan='enterprise', version_type='multi_branch',
                max_stocks=20, max_branches=10, currency='SDG', terms_version=settings.TERMS_VERSION,
                terms_accepted_at=timezone.now())
    data.update(extra)
    return Tenant.objects.create(**data)


class ModelLockTests(TestCase):
    def test_locked_mode_cannot_change(self):
        t = _tenant(purchasing_mode='hybrid', purchasing_mode_locked=True)
        t.purchasing_mode = 'decentralized'
        with self.assertRaises(ValueError):
            t.save()
        t.refresh_from_db()
        self.assertEqual(t.purchasing_mode, 'hybrid')
        t2 = Tenant.objects.get(pk=t.pk)
        t2.purchasing_mode = 'decentralized'
        with self.assertRaises(ValueError):
            t2.save(update_fields=['purchasing_mode'])

    def test_other_fields_still_save_on_locked_tenant(self):
        t = _tenant(purchasing_mode='hybrid', purchasing_mode_locked=True)
        t.name = 'اسم جديد'
        t.save()
        t.refresh_from_db()
        self.assertEqual((t.name, t.purchasing_mode, t.purchasing_mode_locked), ('اسم جديد', 'hybrid', True))

    def test_unlocked_legacy_tenant_can_still_be_set(self):
        t = _tenant()
        self.assertFalse(t.purchasing_mode_locked)
        t.purchasing_mode = 'hybrid'
        t.save()
        t.refresh_from_db()
        self.assertEqual(t.purchasing_mode, 'hybrid')


class AdminTenantFormTests(TestCase):
    def _data(self, **extra):
        bt, _ = BusinessType.objects.get_or_create(slug='lock-bt', defaults={'name': 'lock', 'name_ar': 'lock'})
        data = {'name': 'نشاط', 'business_type': bt.pk, 'subscription_plan': 'enterprise',
                'country': 'السودان', 'timezone': 'Africa/Khartoum', 'currency': 'SDG', 'is_active': 'on',
                'max_users': 40, 'max_stocks': 20, 'max_branches': 10, 'exchange_rate': '1', 'hard_currency': 'USD'}
        data.update(extra)
        return data

    def test_create_with_hybrid_locks_it(self):
        form = TenantForm(self._data(purchasing_mode='hybrid'))
        self.assertTrue(form.is_valid(), form.errors)
        t = form.save()
        self.assertEqual((t.purchasing_mode, t.purchasing_mode_locked), ('hybrid', True))

    def test_create_defaults_to_decentralized_and_locks(self):
        form = TenantForm(self._data())
        self.assertTrue(form.is_valid(), form.errors)
        t = form.save()
        self.assertEqual((t.purchasing_mode, t.purchasing_mode_locked), ('decentralized', True))

    def test_non_enterprise_plan_is_always_decentralized(self):
        form = TenantForm(self._data(subscription_plan='basic', purchasing_mode='hybrid'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().purchasing_mode, 'decentralized')

    def test_update_of_locked_tenant_ignores_posted_mode(self):
        t = _tenant(purchasing_mode='decentralized', purchasing_mode_locked=True)
        data = self._data(name='جديد', purchasing_mode='hybrid')
        form = TenantForm(data, instance=t)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        saved.refresh_from_db()
        self.assertEqual((saved.name, saved.purchasing_mode), ('جديد', 'decentralized'))

    def test_legacy_tenant_can_be_set_once_by_platform_admin_then_locks(self):
        t = _tenant()
        form = TenantForm(self._data(purchasing_mode='hybrid'), instance=t)
        self.assertTrue(form.is_valid(), form.errors)
        t = form.save()
        t.refresh_from_db()
        self.assertEqual((t.purchasing_mode, t.purchasing_mode_locked), ('hybrid', True))
        again = TenantForm(self._data(purchasing_mode='decentralized'), instance=t)
        self.assertTrue(again.is_valid(), again.errors)
        again.save()
        t.refresh_from_db()
        self.assertEqual(t.purchasing_mode, 'hybrid')

    def test_saving_legacy_tenant_without_choosing_does_not_lock_it(self):
        t = _tenant()
        form = TenantForm(self._data(name='تعديل فقط'), instance=t)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        t.refresh_from_db()
        self.assertFalse(t.purchasing_mode_locked)


class RegistrationTests(TestCase):
    def _session(self):
        bt, _ = BusinessType.objects.get_or_create(slug='lock-bt', defaults={'name': 'lock', 'name_ar': 'lock'})
        session = self.client.session
        session['reg_step1'] = {'username': 'newowner', 'email': 'owner@example.com', 'password': 'x12345678',
                                'first_name': 'أ', 'last_name': 'ب'}
        session['reg_step2'] = {'business_type_id': bt.pk, 'business_name': 'نشاط جديد', 'phone': '0911',
                                'address': 'الخرطوم', 'city': 'الخرطوم', 'country': 'السودان'}
        session.save()

    def _post(self, **extra):
        data = {'subscription_plan': 'enterprise', 'timezone': 'Africa/Khartoum', 'currency': 'SDG'}
        data.update(extra)
        return self.client.post(reverse('accounts:register_step3'), data)

    def test_registration_stores_and_locks_chosen_mode(self):
        self._session()
        self._post(purchasing_mode='hybrid')
        t = Tenant.objects.get(name='نشاط جديد')
        self.assertEqual((t.version_type, t.purchasing_mode, t.purchasing_mode_locked), ('multi_branch', 'hybrid', True))

    def test_registration_default_is_decentralized_and_locked(self):
        self._session()
        self._post()
        t = Tenant.objects.get(name='نشاط جديد')
        self.assertEqual((t.purchasing_mode, t.purchasing_mode_locked), ('decentralized', True))

    def test_non_enterprise_registration_ignores_hybrid(self):
        self._session()
        self._post(subscription_plan='basic', purchasing_mode='hybrid')
        t = Tenant.objects.get(name='نشاط جديد')
        self.assertEqual((t.version_type, t.purchasing_mode), ('single_store', 'decentralized'))

    def test_step3_form_field_and_page(self):
        form = Step3SettingsForm({'subscription_plan': 'enterprise', 'timezone': 'x', 'currency': 'SDG',
                                  'purchasing_mode': 'hybrid'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['purchasing_mode'], 'hybrid')
        self._session()
        page = self.client.get(reverse('accounts:register_step3'))
        self.assertContains(page, 'name="purchasing_mode"')
        self.assertContains(page, 'مرة واحدة')

    def test_contact_request_form_accepts_the_mode(self):
        form = RegistrationRequestForm({
            'personal_email': 'a@b.com', 'business_type': BusinessType.objects.get_or_create(
                slug='lock-bt', defaults={'name': 'l', 'name_ar': 'l'})[0].pk,
            'business_name': 'x', 'phone': '1', 'address': 'y', 'version_type': 'multi_branch',
            'purchasing_mode': 'hybrid'})
        form.is_valid()
        self.assertNotIn('purchasing_mode', form.errors)
        self.assertEqual(form.cleaned_data.get('purchasing_mode'), 'hybrid')


class OwnerSettingsReadOnlyTests(TestCase):
    def test_owner_page_shows_mode_but_no_selector(self):
        t = _tenant(purchasing_mode='hybrid', purchasing_mode_locked=True)
        user = User.objects.create_user(username='lock-owner', password='x12345678', tenant=t, is_tenant_admin=True)
        self.client.force_login(user)
        page = self.client.get(reverse('core:tenant_settings')).content.decode()
        self.assertIn('id="purchasingModeCard"', page)
        self.assertIn('يُحدَّد مرة واحدة عند إنشاء الحساب ولا يمكن تغييره', page)
        self.assertNotIn('name="purchasing_mode"', page)
        self.assertNotIn('saveTenantPurchasing', page)
