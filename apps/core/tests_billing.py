"""لوحة المنصة: مدفوعات الاشتراكات اليدوية + الإيصال، وإشعارات المنصة للمشتركين."""
import json
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import PlatformBroadcast, SubscriptionInvoice, SubscriptionPayment, Tenant
from apps.core.test_utils import TenantTestCase
from apps.notifications.models import Notification


class _PlatformBase(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.root = User.objects.create_superuser(username='root-billing', password='secret123', email='r@x.com')
        self.client.force_login(self.root)
        self.today = timezone.localdate()

    def post_json(self, name, data, args=None):
        return self.client.post(reverse(name, args=args or []), data=json.dumps(data),
                                content_type='application/json')

    def pay(self, **over):
        data = dict(tenant_id=self.tenant.pk, plan='pro', amount='45', currency='USD', days=30,
                    method='bank', reference='TX-1', notes='')
        data.update(over)
        return self.post_json('core:admin_payment_create_api', data)


class PaymentTests(_PlatformBase):
    def test_payment_extends_subscription_and_issues_numbered_receipt(self):
        self.tenant.subscription_expires = self.today + timedelta(days=10)
        self.tenant.save()
        r = self.pay()
        self.assertTrue(r.json()['success'], r.content)
        p = SubscriptionPayment.objects.get()
        self.tenant.refresh_from_db()
        self.assertEqual(p.receipt_number, f'ENJ-{self.today.year}-0001')
        self.assertEqual(p.period_start, self.today + timedelta(days=10))
        self.assertEqual(self.tenant.subscription_expires, self.today + timedelta(days=40))
        self.assertEqual(self.tenant.subscription_plan, 'pro')

    def test_expired_subscription_extends_from_today_and_reactivates(self):
        self.tenant.subscription_expires = self.today - timedelta(days=20)
        self.tenant.is_active = False
        self.tenant.save()
        self.assertTrue(self.pay(days=90).json()['success'])
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_expires, self.today + timedelta(days=90))
        self.assertTrue(self.tenant.is_active)

    def test_receipt_numbers_are_sequential(self):
        self.pay()
        self.pay()
        self.assertEqual(list(SubscriptionPayment.objects.order_by('id').values_list('receipt_number', flat=True)),
                         [f'ENJ-{self.today.year}-0001', f'ENJ-{self.today.year}-0002'])

    def test_invalid_input_rejected_and_nothing_changes(self):
        before = Tenant.objects.get(pk=self.tenant.pk).subscription_expires
        for bad in (dict(amount='0'), dict(amount='abc'), dict(days=0), dict(plan='zzz'),
                    dict(tenant_id=999999), dict(paid_at=str(self.today + timedelta(days=3)))):
            self.assertEqual(self.pay(**bad).status_code, 400, bad)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)
        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).subscription_expires, before)

    def test_receipt_pdf_is_generated(self):
        self.pay()
        p = SubscriptionPayment.objects.get()
        r = self.client.get(reverse('core:admin_payment_receipt', args=[p.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content.startswith(b'%PDF'))

    def test_email_receipt_is_manual_and_recorded(self):
        self.tenant.email = 'client@example.com'
        self.tenant.save()
        self.pay()
        p = SubscriptionPayment.objects.get()
        self.assertIsNone(p.emailed_at)
        self.assertEqual(len(mail.outbox), 0)  # التسجيل وحده لا يرسل شيئاً
        r = self.post_json('core:admin_payment_email_api', {}, [p.pk])
        self.assertTrue(r.json()['success'], r.content)
        self.assertEqual(mail.outbox[0].to, ['client@example.com'])
        self.assertEqual(mail.outbox[0].attachments[0][0], f'{p.receipt_number}.pdf')
        p.refresh_from_db()
        self.assertEqual(p.emailed_to, 'client@example.com')

    def test_email_without_address_fails(self):
        self.pay()
        p = SubscriptionPayment.objects.get()
        self.assertEqual(self.post_json('core:admin_payment_email_api', {}, [p.pk]).status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_void_excludes_from_totals_but_keeps_subscription(self):
        self.pay()
        p = SubscriptionPayment.objects.get()
        expires = Tenant.objects.get(pk=self.tenant.pk).subscription_expires
        self.assertTrue(self.post_json('core:admin_payment_void_api', {}, [p.pk]).json()['success'])
        p.refresh_from_db()
        self.assertTrue(p.is_void)
        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).subscription_expires, expires)
        r = self.client.get(reverse('core:admin_payments'))
        self.assertEqual(r.context['payments_count'], 0)
        self.assertEqual(self.post_json('core:admin_payment_email_api', {'email': 'a@b.com'}, [p.pk]).status_code, 400)

    def test_page_renders(self):
        self.pay()
        r = self.client.get(reverse('core:admin_payments'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'ENJ-{self.today.year}-0001')

    def test_tenant_admin_and_unprivileged_staff_are_denied(self):
        self.client.force_login(self.user)  # مدير مشترك عادي
        self.assertEqual(self.pay().status_code, 403)
        self.assertEqual(self.client.get(reverse('core:admin_payments')).status_code, 302)
        # مساعد المنصة: RequestFactory يتجاوز الـ middleware لنختبر فحص الصلاحية في العرض نفسه
        from django.test import RequestFactory
        from apps.core import billing_views
        staff = User(username='staff-b', is_platform_staff=True, platform_permissions=['view_reports'])
        req = RequestFactory().post('/x', data=json.dumps({}), content_type='application/json')
        req.user = staff
        self.assertEqual(billing_views.admin_payment_create_api(req).status_code, 403)
        self.assertEqual(billing_views.admin_broadcast_send_api(req).status_code, 403)
        req = RequestFactory().get('/x')
        req.user = staff
        self.assertEqual(billing_views.admin_payments(req).status_code, 302)  # → no_permission
        staff.platform_permissions = ['manage_billing']
        self.assertTrue(staff.has_platform_perm('manage_billing'))
        self.assertFalse(staff.has_platform_perm('send_broadcasts'))
        self.assertEqual(SubscriptionPayment.objects.count(), 0)


class BroadcastTests(_PlatformBase):
    def setUp(self):
        super().setUp()
        self.other = Tenant.objects.create(
            name='Other', business_type=self.tenant.business_type, subscription_plan='pro',
            version_type=self.tenant.version_type, subscription_expires=self.today + timedelta(days=3))
        self.inactive = Tenant.objects.create(
            name='Off', business_type=self.tenant.business_type, subscription_plan='basic',
            version_type=self.tenant.version_type, is_active=False)

    def send(self, **over):
        data = dict(title='صيانة', message='صيانة الليلة', audience='all', priority='high')
        data.update(over)
        return self.post_json('core:admin_broadcast_send_api', data)

    def test_all_reaches_active_tenants_only(self):
        r = self.send()
        self.assertTrue(r.json()['success'], r.content)
        got = set(Notification.objects.filter(notification_type='platform').values_list('tenant__name', flat=True))
        self.assertEqual(got, {self.tenant.name, 'Other'})
        self.assertEqual(PlatformBroadcast.objects.get().recipients_count, 2)

    def test_dry_run_creates_nothing(self):
        r = self.send(dry_run=True, audience='plan', plan='pro')
        self.assertEqual(r.json()['count'], 1)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(PlatformBroadcast.objects.count(), 0)

    def test_audiences(self):
        self.assertEqual(self.send(dry_run=True, audience='expiring').json()['count'], 1)
        self.assertEqual(self.send(dry_run=True, audience='selected', tenant_ids=[self.tenant.pk]).json()['count'], 1)

    def test_validation(self):
        for bad in (dict(title=''), dict(message=' '), dict(audience='plan', plan='x'),
                    dict(audience='selected', tenant_ids=[]), dict(link='http://evil.com'),
                    dict(audience='bogus')):
            self.assertEqual(self.send(**bad).status_code, 400, bad)
        self.assertEqual(Notification.objects.count(), 0)

    def test_permission_required(self):
        self.client.force_login(self.user)
        self.assertEqual(self.send().status_code, 403)
        self.assertEqual(Notification.objects.count(), 0)

    def test_tenant_users_see_it_including_branch_users(self):
        self.send()
        self.client.force_login(self.user)
        r = self.client.get(reverse('notifications:list'))
        self.assertContains(r, 'صيانة الليلة')
        api = self.client.get('/notifications/api/').json()
        self.assertEqual(api['items'][0]['title'], 'صيانة')


class InvoiceTests(_PlatformBase):
    def make(self, **over):
        data = dict(tenant_id=self.tenant.pk, plan='pro', amount='45', currency='USD', days=30,
                    due_date=str(self.today + timedelta(days=7)), notes='حوّل إلى الحساب كذا')
        data.update(over)
        return self.post_json('core:admin_invoice_create_api', data)

    def test_create_numbers_and_pdf(self):
        r = self.make()
        self.assertTrue(r.json()['success'], r.content)
        inv = SubscriptionInvoice.objects.get()
        self.assertEqual(inv.invoice_number, f'ENJ-INV-{self.today.year}-0001')
        self.assertEqual(inv.due_date, self.today + timedelta(days=7))
        self.assertEqual(inv.status, 'open')
        pdf = self.client.get(reverse('core:admin_invoice_pdf', args=[inv.pk]))
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF'))
        self.make()
        self.assertEqual(SubscriptionInvoice.objects.latest('id').invoice_number, f'ENJ-INV-{self.today.year}-0002')

    def test_does_not_touch_subscription(self):
        before = Tenant.objects.get(pk=self.tenant.pk).subscription_expires
        self.make()
        self.assertEqual(Tenant.objects.get(pk=self.tenant.pk).subscription_expires, before)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)

    def test_due_date_required_and_not_in_past(self):
        for bad in (dict(due_date=''), dict(due_date=str(self.today - timedelta(days=1))),
                    dict(amount='0'), dict(days=0), dict(plan='x'), dict(tenant_id=999999)):
            self.assertEqual(self.make(**bad).status_code, 400, bad)
        self.assertEqual(SubscriptionInvoice.objects.count(), 0)

    def test_paying_from_invoice_links_and_marks_paid(self):
        self.make()
        inv = SubscriptionInvoice.objects.get()
        r = self.pay(invoice_id=inv.pk)
        self.assertTrue(r.json()['success'], r.content)
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'paid')
        self.assertEqual(inv.payment, SubscriptionPayment.objects.get())
        # لا تُسدَّد مرتين
        self.assertEqual(self.pay(invoice_id=inv.pk).status_code, 400)
        self.assertEqual(SubscriptionPayment.objects.count(), 1)
        # ولا يمكن إلغاء مطالبة مسدَّدة
        self.assertEqual(self.post_json('core:admin_invoice_void_api', {}, [inv.pk]).status_code, 400)

    def test_invoice_of_other_tenant_rejected(self):
        other = Tenant.objects.create(name='Other', business_type=self.tenant.business_type,
                                      subscription_plan='pro', version_type=self.tenant.version_type)
        self.make(tenant_id=other.pk)
        inv = SubscriptionInvoice.objects.get()
        self.assertEqual(self.pay(invoice_id=inv.pk).status_code, 400)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)

    def test_overdue_status_and_void(self):
        self.make()
        inv = SubscriptionInvoice.objects.get()
        SubscriptionInvoice.objects.filter(pk=inv.pk).update(due_date=self.today - timedelta(days=2))
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'overdue')
        self.assertTrue(self.post_json('core:admin_invoice_void_api', {}, [inv.pk]).json()['success'])
        inv.refresh_from_db()
        self.assertEqual(inv.status, 'void')
        self.assertEqual(self.pay(invoice_id=inv.pk).status_code, 400)

    def test_pages_render_and_prefill(self):
        self.make()
        inv = SubscriptionInvoice.objects.get()
        r = self.client.get(reverse('core:admin_invoices'))
        self.assertContains(r, inv.invoice_number)
        r = self.client.get(reverse('core:admin_payments') + f'?invoice={inv.pk}')
        self.assertEqual(r.context['prefill']['invoice_id'], inv.pk)

    def test_permission_required(self):
        self.client.force_login(self.user)
        self.assertEqual(self.make().status_code, 403)
        self.assertEqual(SubscriptionInvoice.objects.count(), 0)


class ChromeLookupTests(TenantTestCase):
    def test_finds_chrome_even_when_path_is_empty(self):
        """خدمة gunicorn بـ PATH مختلف: يجب أن يجد Chrome من المسارات الثابتة."""
        import os
        from unittest import mock
        from apps.core import brand_docs
        with mock.patch.dict(os.environ, {'PATH': '/nonexistent'}), \
                mock.patch.object(brand_docs.shutil, 'which', return_value=None):
            self.assertTrue(os.path.exists(brand_docs._chrome()))


class PdfUnderServicePathTests(_PlatformBase):
    def test_pdf_renders_when_process_path_is_only_the_venv(self):
        """خدمة gunicorn تعمل بـ PATH = مجلد الـ venv فقط: يجب أن يتولّد الـ PDF رغم ذلك."""
        import os
        from unittest import mock
        self.pay()
        p = SubscriptionPayment.objects.get()
        with mock.patch.dict(os.environ, {'PATH': os.path.dirname(os.sys.executable)}):
            r = self.client.get(reverse('core:admin_payment_receipt', args=[p.pk]))
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertTrue(r.content.startswith(b'%PDF'))


class VerifyDocumentTests(_PlatformBase):
    """رمز QR المطبوع في مطالبة السداد وسند القبض يفتح صفحة تحقق عامة موقَّعة الرابط."""

    def setUp(self):
        super().setUp()
        InvoiceTests.make(self)
        self.pay()
        self.invoice = SubscriptionInvoice.objects.get()
        self.payment = SubscriptionPayment.objects.get()
        self.client.logout()                       # من يفحص الرمز لا يملك حساباً

    def url(self, kind, number, sig=None):
        from apps.core import doc_verify
        return reverse('core:verify_document', args=[kind, number, sig or doc_verify.sign(kind, number)])

    def test_the_link_in_the_qr_is_signed_and_opens_for_anyone(self):
        from apps.core import doc_verify
        link = doc_verify.verify_url('invoice', self.invoice.invoice_number)
        self.assertTrue(link.startswith('https://') and link.endswith('/'))
        r = self.client.get(self.url('invoice', self.invoice.invoice_number))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, self.invoice.invoice_number)
        self.assertContains(r, 'مطالبة سداد')
        self.assertContains(r, 'بانتظار السداد')
        self.assertContains(r, '$45')
        self.assertEqual(r['X-Robots-Tag'], 'noindex, nofollow')

    def test_a_receipt_shows_its_period_and_amount(self):
        r = self.client.get(self.url('receipt', self.payment.receipt_number))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'سند قبض')
        self.assertContains(r, self.payment.receipt_number)
        self.assertContains(r, self.tenant.name)
        self.assertContains(r, 'تم تجديد الاشتراك حتى')
        self.assertContains(r, f'{self.payment.period_end:%Y/%m/%d}')

    def test_forged_or_unknown_documents_are_not_found(self):
        number = self.invoice.invoice_number
        self.assertEqual(self.client.get(self.url('invoice', number, 'a' * 20)).status_code, 404)           # wrong signature
        self.assertEqual(self.client.get(self.url('receipt', number)).status_code, 404)                    # right signature, wrong kind
        self.assertEqual(self.client.get(self.url('invoice', 'ENJ-INV-2026-9999')).status_code, 404)       # signed but not issued
        self.assertEqual(self.client.get(self.url('contract', number)).status_code, 404)                   # unknown kind

    def test_a_void_document_says_so(self):
        SubscriptionInvoice.objects.filter(pk=self.invoice.pk).update(voided_at=timezone.now())
        r = self.client.get(self.url('invoice', self.invoice.invoice_number))
        self.assertContains(r, 'ملغى')
        self.assertNotContains(r, 'مستند صحيح')

    def test_a_subscriber_with_an_expired_subscription_can_still_open_it(self):
        user = User.objects.create_user(username='late-payer', password='secret123', tenant=self.tenant)
        Tenant.objects.filter(pk=self.tenant.pk).update(subscription_expires=self.today - timedelta(days=30))
        self.client.force_login(user)
        r = self.client.get(self.url('receipt', self.payment.receipt_number))
        self.assertEqual(r.status_code, 200)

    def test_both_printed_documents_carry_the_qr_and_the_link(self):
        from apps.core import brand_docs, doc_verify
        from django.template.loader import render_to_string
        for name, ctx, kind, number in (
                ('core/docs/invoice_doc.html', dict(invoice=self.invoice), 'invoice', self.invoice.invoice_number),
                ('core/docs/receipt_doc.html', dict(payment=self.payment, invoice=None), 'receipt', self.payment.receipt_number)):
            ctx.update(tenant=self.tenant, bank=brand_docs.BANK_DETAILS, address='', amount_text='$45', currency_name='دولار',
                       brand='/static/brand/', **brand_docs._verification(kind, number))
            html = render_to_string(name, ctx)
            self.assertIn('<svg', html)
            self.assertIn(doc_verify.verify_url(kind, number), html)
            self.assertNotIn('Stamp', html)
            self.assertNotIn('الختم', html)
