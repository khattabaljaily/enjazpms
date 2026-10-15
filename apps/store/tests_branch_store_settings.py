"""
إعدادات المتجر لكل فرع (نسخة المؤسسات): الفرع يعدّل بنفسه تفعيل متجره واتصاله وحسابه البنكي
ودوامه وخيارات عرضه؛ مدير النشاط يضبط اسم المتجر ورابطه ووصفه فقط؛ والمتجر العام يقرأ بيانات
الفرع المختار. بقية الباقات (بلا فروع) لا تتغير.
"""
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Branch, TenantCapabilities
from apps.core.test_utils import TenantTestCase, make_item, make_stock
from apps.stocks.models import StockQuantity
from apps.store.models import BranchStoreSettings, OnlineOrder, StoreSettings


class BranchStoreSettingsTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant,
            defaults={f.name: True for f in TenantCapabilities._meta.get_fields()
                      if getattr(f, 'name', '').startswith('has_')})
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        self.item = make_item(self.tenant, name='بنادول')
        for b in (self.b1, self.b2):
            stock = make_stock(self.tenant, name=f'م{b.id}', branch=b, is_default=True)
            StockQuantity.objects.update_or_create(
                tenant=self.tenant, stock=stock, item=self.item,
                defaults={'quantity': Decimal('5'), 'reserved_quantity': Decimal('0')})
        self.store, _ = StoreSettings.objects.update_or_create(
            tenant=self.tenant, defaults=dict(
                is_enabled=True, status_override='open', whatsapp='111',
                bank_details='حساب النشاط', show_price_list=True, show_prices=True))
        self.sup1 = User.objects.create_user(username='bs-sup1', password='x12345678', tenant=self.tenant,
                                             branch=self.b1, is_branch_supervisor=True)
        self.anon = Client()

    def url(self, name, *args):
        return reverse(f'store:{name}', args=[self.store.slug, *args])

    def save_form(self, **extra):
        """يحاكي حفظ الفرع لصفحته: الصناديق غير المرسلة تعني «إيقاف»."""
        data = {'is_enabled': 'on', 'status_override': 'open', 'show_prices': 'on',
                'show_price_list': 'on', **extra}
        data = {k: v for k, v in data.items() if v is not None}
        self.client.force_login(self.sup1)
        return self.client.post(reverse('store:manage_branch_settings'), data)

    # ---- صفحة الفرع -------------------------------------------------------
    def test_page_prefills_from_business_settings_then_branch_values_win(self):
        self.client.force_login(self.sup1)
        page = self.client.get(reverse('store:manage_branch_settings'))
        self.assertContains(page, 'حساب النشاط')               # قبل أول حفظ: قيم النشاط
        self.save_form(bank_details='بنك فرع أ', whatsapp='999')
        page = self.client.get(reverse('store:manage_branch_settings'))
        self.assertContains(page, 'بنك فرع أ')
        self.assertNotContains(page, 'حساب النشاط')
        # فرع آخر لم يحفظ بعد يرث
        self.assertEqual(self.store.for_branch(self.b2).bank_details, 'حساب النشاط')

    def test_branch_edits_own_settings_and_storefront_uses_them(self):
        self.assertEqual(self.save_form(whatsapp='999', bank_details='بنك فرع أ',
                                        show_price_list=None).status_code, 302)
        own = BranchStoreSettings.objects.get(branch=self.b1)
        self.assertEqual(own.whatsapp, '999')
        self.assertIs(own.show_price_list, False)
        self.assertIs(own.is_enabled, True)

        page = self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        self.assertContains(page, 'wa.me/999')
        self.assertNotContains(page, 'wa.me/111')
        # فرع ب بلا تخصيص يرث إعدادات النشاط
        page = Client().get(self.url('storefront'), {'branch': self.b2.id})
        self.assertContains(page, 'wa.me/111')
        # قائمة الأسعار مُعطّلة لفرع أ فقط
        self.assertEqual(self.anon.get(self.url('price_list')).status_code, 404)
        self.assertContains(Client().get(self.url('price_list'), {'branch': self.b2.id}), 'بنادول')

    def test_saved_blank_value_is_the_branch_decision(self):
        self.save_form(whatsapp='')                      # الفرع لا يريد واتساب
        self.assertNotContains(self.anon.get(self.url('storefront'), {'branch': self.b1.id}), 'wa.me/')

    def test_bank_details_follow_the_order_branch(self):
        self.save_form(bank_details='بنك فرع أ')
        self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        self.anon.post(self.url('cart_add'), {'item_id': self.item.id, 'qty': '1'})
        self.anon.post(self.url('checkout'), {'name': 'ز', 'phone': '1', 'payment_method': 'bank'})
        order = OnlineOrder.objects.get(tenant=self.tenant)
        self.assertEqual(order.branch, self.b1)
        self.assertContains(self.anon.get(self.url('order_confirm', order.token)), 'بنك فرع أ')

    def test_branch_min_order_and_hours(self):
        self.save_form(min_order_amount='500', day_saturday_enabled='on',
                       day_saturday_open='09:00', day_saturday_close='20:00')
        own = BranchStoreSettings.objects.get(branch=self.b1)
        self.assertEqual(own.min_order_amount, Decimal('500'))
        self.assertEqual(own.working_hours['saturday'], {'enabled': True, 'open': '09:00', 'close': '20:00'})
        self.assertFalse(own.working_hours['sunday']['enabled'])
        self.assertEqual(self.store.for_branch(self.b1).min_order_amount, Decimal('500'))
        self.assertEqual(self.store.for_branch(self.b2).min_order_amount, self.store.min_order_amount)

    def test_branch_closed_closes_only_that_branch(self):
        self.save_form(status_override='closed')
        self.assertNotContains(self.anon.get(self.url('storefront'), {'branch': self.b1.id}), 'بنادول')
        self.assertContains(Client().get(self.url('storefront'), {'branch': self.b2.id}), 'بنادول')

    # ---- التفعيل لكل فرع ---------------------------------------------------
    def test_disabled_branch_is_not_offered_and_cannot_be_chosen(self):
        self.save_form(is_enabled=None)                  # فرع أ غير متاح
        page = Client().get(self.url('storefront'))
        self.assertContains(page, 'فرع ب')
        self.assertNotContains(page, 'فرع أ')
        c = Client()
        page = c.get(self.url('storefront'), {'branch': self.b1.id})      # لا يُقبل اختياره
        self.assertNotContains(page, 'بنادول')
        self.assertEqual(c.post(self.url('cart_add'), {'item_id': self.item.id, 'qty': '1'},
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 400)
        self.assertContains(Client().get(self.url('storefront'), {'branch': self.b2.id}), 'بنادول')

    def test_store_unavailable_when_every_branch_is_off(self):
        BranchStoreSettings.objects.create(tenant=self.tenant, branch=self.b1, is_enabled=False)
        BranchStoreSettings.objects.create(tenant=self.tenant, branch=self.b2, is_enabled=False)
        for name in ('storefront', 'price_list', 'cart'):
            self.assertEqual(Client().get(self.url(name)).status_code, 503, name)
        resp = Client().post(self.url('cart_add'), {'item_id': self.item.id, 'qty': '1'})
        self.assertEqual(resp.status_code, 503)

    def test_branch_store_works_even_if_business_level_switch_is_off(self):
        StoreSettings.objects.filter(pk=self.store.pk).update(is_enabled=False)
        self.save_form()                                  # فرع أ يفعّل متجره بنفسه
        self.assertContains(Client().get(self.url('storefront'), {'branch': self.b1.id}), 'بنادول')
        self.assertEqual(Client().get(self.url('storefront'), {'branch': self.b2.id}).status_code, 200)
        self.assertNotContains(Client().get(self.url('storefront'), {'branch': self.b2.id}), 'بنادول')

    def test_clearing_inheritance_fields(self):
        self.save_form(price_list_columns=['name'])
        own = BranchStoreSettings.objects.get(branch=self.b1)
        self.assertEqual(own.price_list_columns, ['name'])

    # ---- الصلاحيات والقائمة ------------------------------------------------
    def test_owner_cannot_use_branch_page_and_branch_cannot_touch_others(self):
        self.assertIn(self.client.get(reverse('store:manage_branch_settings')).status_code, (302, 403))
        self.save_form(whatsapp='999')
        self.assertFalse(BranchStoreSettings.objects.filter(branch=self.b2).exists())

    def test_sidebar_has_store_group_for_branch_only(self):
        self.client.force_login(self.sup1)
        page = self.client.get(reverse('core:dashboard'))
        self.assertContains(page, reverse('store:manage_branch_settings'))
        self.assertContains(page, reverse('store:manage_orders'))
        self.client.logout()
        self.client.force_login(User.objects.filter(tenant=self.tenant, is_tenant_admin=True).first())
        page = self.client.get(reverse('core:dashboard'))
        self.assertNotContains(page, reverse('store:manage_branch_settings'))

    # ---- مدير النشاط: هوية المتجر فقط ----------------------------------------
    def test_owner_page_shows_identity_only_and_saves_only_identity(self):
        page = self.client.get(reverse('store:manage_settings'))
        self.assertContains(page, 'name="display_name"')
        self.assertContains(page, 'name="slug"')
        self.assertContains(page, 'name="description"')
        for hidden in ('name="is_enabled"', 'name="whatsapp"', 'name="bank_details"', 'name="show_prices"',
                       'name="accent_color"', 'name="min_order_amount"', 'name="open_message"',
                       'name="day_saturday_enabled"'):
            self.assertNotContains(page, hidden, msg_prefix=hidden)
        before = StoreSettings.objects.get(pk=self.store.pk)
        resp = self.client.post(reverse('store:manage_settings'),
                                {'display_name': 'اسم جديد', 'description': 'وصف جديد', 'slug': 'my-new-slug'})
        self.assertEqual(resp.status_code, 302)
        after = StoreSettings.objects.get(pk=self.store.pk)
        self.assertEqual((after.display_name, after.description, after.slug), ('اسم جديد', 'وصف جديد', 'my-new-slug'))
        for f in ('is_enabled', 'whatsapp', 'bank_details', 'show_prices', 'show_price_list',
                  'status_override', 'accent_color', 'working_hours', 'min_order_amount'):
            self.assertEqual(getattr(after, f), getattr(before, f), f)


class StoreSettingsOtherPlansUnchangedTests(TenantTestCase):
    subscription_plan = 'pro'

    def test_non_enterprise_owner_still_gets_the_full_page_and_saves_everything(self):
        store, _ = StoreSettings.objects.update_or_create(
            tenant=self.tenant, defaults=dict(is_enabled=True, status_override='open'))
        page = self.client.get(reverse('store:manage_settings'))
        for field in ('is_enabled', 'whatsapp', 'bank_details', 'show_prices', 'accent_color', 'open_message'):
            self.assertContains(page, f'name="{field}"', msg_prefix=field)
        self.assertContains(page, 'عرض المنتجات')
        self.assertNotContains(page, 'الكتالوج')
        self.client.post(reverse('store:manage_settings'), {
            'display_name': 'م', 'whatsapp': '555', 'slug': store.slug, 'status_override': 'auto'})
        store.refresh_from_db()
        self.assertEqual(store.whatsapp, '555')
        self.assertFalse(store.is_enabled)               # نفس السلوك القديم: الصندوق غير المرسل = إيقاف
