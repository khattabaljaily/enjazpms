"""
المتجر الإلكتروني لصيدلية: بيان صريح هل المنتج يُصرف بوصفة طبية، وحقل إرفاق وصفة
اختياري (لا إلزامي)، وإخلاء مسؤولية المنصة. غير الصيدليات: بلا أي تغيير.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.core.models import Branch, BusinessType
from apps.core.test_utils import TenantTestCase, make_item
from apps.store.models import OnlineOrder, StoreSettings

def set_business_type(tenant, slug):
    bt, _ = BusinessType.objects.get_or_create(slug=slug, defaults={'name': slug, 'name_ar': slug})
    tenant.business_type = bt
    tenant.save(update_fields=['business_type'])
    return bt


PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64
PDF = b'%PDF-1.4\n' + b'0' * 64


class PrescriptionStoreTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        set_business_type(self.tenant, 'pharmacy')
        self.store = StoreSettings.objects.create(
            tenant=self.tenant, is_enabled=True, status_override='open', show_out_of_stock=True)
        self.rx = make_item(self.tenant, name='مضاد حيوي', requires_prescription=True)
        self.otc = make_item(self.tenant, name='فيتامين', requires_prescription=False)
        self.anon = Client()

    def _cart(self, *items):
        for it in items:
            self.anon.post(reverse('store:cart_add', args=[self.store.slug]), {'item_id': it.id, 'qty': '1'})

    def _checkout(self, files=None, **extra):
        data = {'name': 'زبون', 'phone': '0911', 'payment_method': 'bank', **extra}
        if files:
            data.update(files)
        return self.anon.post(reverse('store:checkout', args=[self.store.slug]), data)

    # ---- العرض --------------------------------------------------------
    def test_storefront_shows_prescription_status_for_every_product(self):
        html = self.anon.get(reverse('store:storefront', args=[self.store.slug])).content.decode()
        self.assertIn('يُصرف بوصفة طبية', html)
        self.assertIn('بدون وصفة طبية', html)

    def test_non_pharmacy_store_has_no_prescription_ui(self):
        set_business_type(self.tenant, 'medical-distributor')
        html = self.anon.get(reverse('store:storefront', args=[self.store.slug])).content.decode()
        self.assertNotIn('يُصرف بوصفة طبية', html)
        self.assertNotIn('بدون وصفة طبية', html)
        self._cart(self.rx)
        checkout = self.anon.get(reverse('store:checkout', args=[self.store.slug])).content.decode()
        self.assertNotIn('name="prescription"', checkout)

    # ---- السلة والدفع --------------------------------------------------
    def test_checkout_with_rx_item_shows_optional_upload_and_disclaimer(self):
        self._cart(self.rx)
        html = self.anon.get(reverse('store:checkout', args=[self.store.slug])).content.decode()
        self.assertIn('name="prescription"', html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn('اختياري', html)
        self.assertNotIn('name="prescription" required', html)
        self.assertIn('وسيط تقني فقط', html)
        self.assertIn('تنسيق تام بين المتجر والزبون', html)

    def test_checkout_without_rx_items_has_no_upload_or_notice(self):
        self._cart(self.otc)
        html = self.anon.get(reverse('store:checkout', args=[self.store.slug])).content.decode()
        self.assertNotIn('name="prescription"', html)
        self.assertNotIn('وسيط تقني فقط', html)

    def test_order_without_prescription_is_accepted(self):
        self._cart(self.rx)
        resp = self._checkout()
        self.assertEqual(resp.status_code, 302)
        order = OnlineOrder.objects.get()
        self.assertFalse(order.prescription)
        self.assertTrue(order.lines.get().requires_prescription)

    def test_order_with_valid_prescription_is_stored(self):
        self._cart(self.rx)
        resp = self._checkout({'prescription': SimpleUploadedFile('rx.png', PNG, content_type='image/png')})
        self.assertEqual(resp.status_code, 302)
        order = OnlineOrder.objects.get()
        self.assertTrue(order.prescription)
        self.assertNotIn('rx.png', order.prescription.name)  # اسم عشوائي
        order.prescription.delete(save=False)

    def test_pdf_prescription_is_accepted(self):
        self._cart(self.rx)
        resp = self._checkout({'prescription': SimpleUploadedFile('rx.pdf', PDF, content_type='application/pdf')})
        self.assertEqual(resp.status_code, 302)
        order = OnlineOrder.objects.get()
        order.prescription.delete(save=False)

    def test_invalid_prescription_files_are_rejected_without_creating_order(self):
        self._cart(self.rx)
        for name, content in (('rx.exe', b'MZ' + b'0' * 50), ('rx.png', b'not really a png file'),
                              ('rx.pdf', b'x' * (6 * 1024 * 1024))):
            resp = self._checkout({'prescription': SimpleUploadedFile(name, content)})
            self.assertEqual(resp.status_code, 200, name)
            self.assertIn('prescription', resp.context['errors'], name)
        self.assertEqual(OnlineOrder.objects.count(), 0)

    def test_prescription_ignored_for_non_pharmacy(self):
        set_business_type(self.tenant, 'medical-distributor')
        self._cart(self.rx)
        self._checkout({'prescription': SimpleUploadedFile('rx.png', PNG)})
        self.assertFalse(OnlineOrder.objects.get().prescription)

    # ---- الإدارة -----------------------------------------------------------
    def test_manager_sees_prescription_and_can_download_it(self):
        self._cart(self.rx)
        self._checkout({'prescription': SimpleUploadedFile('rx.png', PNG, content_type='image/png')})
        order = OnlineOrder.objects.get()
        detail = self.client.get(reverse('store:manage_order_detail', args=[order.pk]))
        self.assertContains(detail, 'عرض الوصفة المرفقة')
        self.assertContains(detail, 'يُصرف بوصفة طبية')
        file_resp = self.client.get(reverse('store:manage_order_prescription', args=[order.pk]))
        self.assertEqual(file_resp.status_code, 200)
        self.assertEqual(b''.join(file_resp.streaming_content), PNG)
        order.prescription.delete(save=False)

    def test_prescription_download_requires_login(self):
        self._cart(self.rx)
        self._checkout({'prescription': SimpleUploadedFile('rx.png', PNG)})
        order = OnlineOrder.objects.get()
        resp = Client().get(reverse('store:manage_order_prescription', args=[order.pk]))
        self.assertIn(resp.status_code, (301, 302, 403))
        order.prescription.delete(save=False)

    def test_manager_detail_without_prescription_says_optional(self):
        self._cart(self.rx)
        self._checkout()
        order = OnlineOrder.objects.get()
        detail = self.client.get(reverse('store:manage_order_detail', args=[order.pk]))
        self.assertContains(detail, 'لم يُرفق الزبون وصفة طبية')
        self.assertContains(detail, 'نظام إنجاز غير مسؤولة')


class PrescriptionBranchIsolationTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def test_other_branch_user_cannot_download_prescription(self):
        set_business_type(self.tenant, 'pharmacy')
        b1 = Branch.objects.create(tenant=self.tenant, name='ف1')
        b2 = Branch.objects.create(tenant=self.tenant, name='ف2')
        store = StoreSettings.objects.create(tenant=self.tenant, is_enabled=True, status_override='open')
        order = OnlineOrder.objects.create(
            tenant=self.tenant, store=store, branch=b2, customer_name='ز', customer_phone='1',
            payment_method='bank', prescription=SimpleUploadedFile('rx.png', PNG))
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='متجر', permissions={'view_store_orders': True}, is_active=True)
        user1 = User.objects.create_user(username='rx-b1', password='x12345678', tenant=self.tenant,
                                         branch=b1)
        user1.permission_groups.add(group)
        self.client.force_login(user1)
        resp = self.client.get(reverse('store:manage_order_prescription', args=[order.pk]))
        self.assertIn(resp.status_code, (403, 404))
        user2 = User.objects.create_user(username='rx-b2', password='x12345678', tenant=self.tenant,
                                         branch=b2)
        user2.permission_groups.add(group)
        self.client.force_login(user2)
        self.assertEqual(self.client.get(reverse('store:manage_order_prescription', args=[order.pk])).status_code, 200)
        order.prescription.delete(save=False)
