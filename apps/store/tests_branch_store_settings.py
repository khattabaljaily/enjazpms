"""
إعدادات المتجر لكل فرع (نسخة المؤسسات): الفرع يعدّل اتصاله وحسابه البنكي ودوامه وخيارات
عرضه بنفسه، وكل حقل فارغ يرث قيمة النشاط؛ والمتجر العام يقرأ بيانات الفرع المختار.
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
                bank_details='حساب النشاط', show_price_list=True))
        self.sup1 = User.objects.create_user(username='bs-sup1', password='x12345678', tenant=self.tenant,
                                             branch=self.b1, is_branch_supervisor=True)
        self.anon = Client()

    def url(self, name, *args):
        return reverse(f'store:{name}', args=[self.store.slug, *args])

    def save_form(self, **extra):
        data = {'whatsapp': '', 'bank_details': '', 'delivery_message': '', 'status_override': '',
                'columns_mode': 'inherit', 'hours_mode': 'inherit',
                **{f: '' for f in BranchStoreSettings.TRISTATE_FIELDS}, **extra}
        self.client.force_login(self.sup1)
        return self.client.post(reverse('store:manage_branch_settings'), data)

    def test_branch_edits_own_settings_and_storefront_uses_them(self):
        resp = self.save_form(whatsapp='999', bank_details='بنك فرع أ', show_price_list='0')
        self.assertEqual(resp.status_code, 302)
        own = BranchStoreSettings.objects.get(branch=self.b1)
        self.assertEqual(own.whatsapp, '999')
        self.assertIs(own.show_price_list, False)
        self.assertIsNone(own.show_prices)

        page = self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        self.assertContains(page, 'wa.me/999')
        self.assertNotContains(page, 'wa.me/111')
        # فرع آخر بلا تخصيص يرث إعدادات النشاط
        page = Client().get(self.url('storefront'), {'branch': self.b2.id})
        self.assertContains(page, 'wa.me/111')
        # قائمة الأسعار مُعطّلة لفرع أ فقط
        self.assertEqual(self.anon.get(self.url('price_list')).status_code, 404)
        self.assertContains(Client().get(self.url('price_list'), {'branch': self.b2.id}), 'بنادول')

    def test_bank_details_follow_the_order_branch(self):
        self.save_form(bank_details='بنك فرع أ')
        self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        self.anon.post(self.url('cart_add'), {'item_id': self.item.id, 'qty': '1'})
        self.anon.post(self.url('checkout'), {'name': 'ز', 'phone': '1', 'payment_method': 'bank'})
        order = OnlineOrder.objects.get(tenant=self.tenant)
        self.assertEqual(order.branch, self.b1)
        self.assertContains(self.anon.get(self.url('order_confirm', order.token)), 'بنك فرع أ')

    def test_branch_hours_close_only_that_branch(self):
        self.save_form(status_override='closed')
        self.assertNotContains(self.anon.get(self.url('storefront'), {'branch': self.b1.id}), 'بنادول')
        self.assertContains(Client().get(self.url('storefront'), {'branch': self.b2.id}), 'بنادول')

    def test_clearing_fields_restores_inheritance(self):
        self.save_form(whatsapp='999', show_prices='0', columns_mode='custom', price_list_columns=['name'])
        own = BranchStoreSettings.objects.get(branch=self.b1)
        self.assertEqual(own.price_list_columns, ['name'])
        self.save_form()
        own.refresh_from_db()
        self.assertEqual(own.whatsapp, '')
        self.assertIsNone(own.show_prices)
        self.assertIsNone(own.price_list_columns)
        self.assertEqual(own.working_hours, {})

    def test_owner_and_other_branches_cannot_edit(self):
        # مدير النشاط لا يملك صلاحية الفرع ولا فرعاً
        self.assertIn(self.client.get(reverse('store:manage_branch_settings')).status_code, (302, 403))
        # تعديل فرع أ لا يمس فرع ب
        self.save_form(whatsapp='999')
        self.assertFalse(BranchStoreSettings.objects.filter(branch=self.b2).exists())
        # المستخدم بلا فرع (مدير النشاط) لا يصل
        self.assertEqual(self.store.for_branch(self.b2).whatsapp, '111')
