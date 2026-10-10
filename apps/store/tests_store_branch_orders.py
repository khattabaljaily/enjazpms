"""
المتجر في نسخة المؤسسات: الزبون يختار الفرع صراحةً (لا فرع افتراضي)، والكميات تُتحقق من
مخزن الفرع عند الإضافة للسلة وإتمام الطلب والقبول (بلا حجز)، والفرع هو من يستقبل طلباته
ويقبلها أو يرفضها وتُصدر فاتورتها منه؛ مدير النشاط يتابع للقراءة فقط.
"""
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Branch, TenantCapabilities
from apps.core.test_utils import TenantTestCase, make_item, make_stock
from apps.notifications.models import Notification
from apps.stocks.models import StockQuantity
from apps.store.models import OnlineOrder, StoreSettings
from apps.store.services import approve_order


class StoreBranchOrdersTests(TenantTestCase):
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
        self.s1 = make_stock(self.tenant, name='م1', branch=self.b1, is_default=True)
        self.s2 = make_stock(self.tenant, name='م2', branch=self.b2, is_default=True)
        self.item = make_item(self.tenant, name='بنادول')
        self.qty(self.s1, '3')
        self.store, _ = StoreSettings.objects.update_or_create(
            tenant=self.tenant, defaults=dict(is_enabled=True, status_override='open'))
        self.sup1 = User.objects.create_user(username='so-sup1', password='x12345678', tenant=self.tenant,
                                             branch=self.b1, is_branch_supervisor=True)
        self.sup2 = User.objects.create_user(username='so-sup2', password='x12345678', tenant=self.tenant,
                                             branch=self.b2, is_branch_supervisor=True)
        self.anon = Client()

    def qty(self, stock, value):
        StockQuantity.objects.update_or_create(
            tenant=self.tenant, stock=stock, item=self.item,
            defaults={'quantity': Decimal(value), 'reserved_quantity': Decimal('0')})

    def url(self, name, *args):
        return reverse(f'store:{name}', args=[self.store.slug, *args])

    def add(self, q, ajax=True):
        headers = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'} if ajax else {}
        return self.anon.post(self.url('cart_add'), {'item_id': self.item.id, 'qty': q}, **headers)

    def order(self, q='2'):
        self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        self.assertEqual(self.add(q).status_code, 200)
        resp = self.anon.post(self.url('checkout'), {'name': 'زبون', 'phone': '0911', 'payment_method': 'bank'})
        self.assertEqual(resp.status_code, 302)
        return OnlineOrder.objects.get(tenant=self.tenant)

    # ---- اختيار الفرع ---------------------------------------------------
    def test_branch_must_be_chosen_first(self):
        for name in ('storefront', 'cart'):
            page = self.anon.get(self.url(name))
            self.assertContains(page, 'اختر الفرع')
            self.assertNotContains(page, 'بنادول')
        self.assertEqual(self.add('1').status_code, 400)
        page = self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        self.assertContains(page, 'بنادول')

    # ---- الكميات ---------------------------------------------------------
    def test_cart_cannot_exceed_branch_stock(self):
        self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        resp = self.add('4')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('المتاح', resp.json()['error'])
        self.assertEqual(self.add('3').status_code, 200)
        self.assertEqual(self.add('1').status_code, 400)          # 3 في السلة بالفعل
        self.anon.get(self.url('storefront'), {'branch': self.b2.id})
        self.assertEqual(self.add('1').status_code, 400)          # فرع ب بلا رصيد

    def test_checkout_blocked_when_stock_dropped(self):
        self.anon.get(self.url('storefront'), {'branch': self.b1.id})
        self.add('3')
        self.qty(self.s1, '1')
        cart = self.anon.get(self.url('cart'))
        self.assertContains(cart, 'المتوفر 1 فقط')
        resp = self.anon.post(self.url('checkout'), {'name': 'زبون', 'phone': '0911', 'payment_method': 'bank'})
        self.assertRedirects(resp, self.url('cart'), fetch_redirect_response=False)
        self.assertFalse(OnlineOrder.objects.exists())

    def test_approval_rechecks_stock(self):
        order = self.order('2')
        self.qty(self.s1, '1')
        with self.assertRaises(ValueError):
            approve_order(order)
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')

    # ---- الفرع يستقبل ويقرر ---------------------------------------------
    def test_order_goes_to_its_branch(self):
        order = self.order()
        self.assertEqual(order.branch, self.b1)
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, branch=self.b1,
                                                    notification_type='online_order').exists())
        self.client.force_login(self.sup2)
        page = self.client.get(reverse('store:manage_orders'))
        self.assertEqual(page.context['counts']['pending'], 0)
        self.assertEqual(self.client.get(reverse('store:manage_order_detail', args=[order.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('store:manage_order_approve', args=[order.pk])).status_code, 404)

        self.client.force_login(self.sup1)
        self.assertContains(self.client.get(reverse('core:dashboard')), reverse('store:manage_orders'))
        page = self.client.get(reverse('store:manage_orders'))
        self.assertEqual(page.context['counts']['pending'], 1)
        self.assertContains(page, 'approveOrder')
        resp = self.client.post(reverse('store:manage_order_approve', args=[order.pk]))
        self.assertTrue(resp.json()['success'], resp.content)
        order.refresh_from_db()
        self.assertEqual(order.sale_invoice.stock, self.s1)
        self.assertEqual(order.sale_invoice.created_by, self.sup1)

    def test_owner_has_no_order_access_and_branch_has_no_settings(self):
        order = self.order()
        # مدير النشاط: إعدادات المتجر فقط — لا طلبات ولا عدّادها
        self.assertIn(self.client.get(reverse('store:manage_orders')).status_code, (302, 403))
        self.assertIn(self.client.get(reverse('store:manage_order_detail', args=[order.pk])).status_code, (302, 403))
        for name in ('store:manage_order_approve', 'store:manage_order_reject'):
            self.assertIn(self.client.post(reverse(name, args=[order.pk])).status_code, (302, 403), name)
        settings_page = self.client.get(reverse('store:manage_settings'))
        self.assertEqual(settings_page.status_code, 200)
        self.assertNotContains(settings_page, f'href="{reverse("store:manage_orders")}"')
        self.assertNotContains(self.client.get(reverse('core:dashboard')), f'href="{reverse("store:manage_orders")}"')
        # الفرع: الطلبات فقط — لا إعدادات المتجر، ورابط متجره يفتح على فرعه
        self.client.force_login(self.sup1)
        self.assertNotContains(self.client.get(reverse('store:manage_orders')), f'href="{reverse("store:manage_settings")}"')
        dash = self.client.get(reverse('core:dashboard'))
        self.assertNotContains(dash, f'href="{reverse("store:manage_settings")}"')
        self.assertContains(dash, f'/store/{self.store.slug}/?branch={self.b1.id}')
        orders_page = self.client.get(reverse('store:manage_orders'))
        self.assertContains(orders_page, 'رمز QR للمتجر')
        self.assertContains(orders_page, f'/store/{self.store.slug}/?branch={self.b1.id}')
        self.assertIn(self.client.get(reverse('store:manage_settings')).status_code, (302, 403))
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')


class StoreWithoutBranchesUnchangedTests(TenantTestCase):
    def test_owner_approves_as_before_and_no_branch_step(self):
        item = make_item(self.tenant, name='صنف')
        self.set_quantity(item, self.default_stock, '5')
        store = StoreSettings.objects.create(tenant=self.tenant, is_enabled=True, status_override='open')
        anon = Client()
        self.assertContains(anon.get(reverse('store:storefront', args=[store.slug])), 'صنف')
        anon.post(reverse('store:cart_add', args=[store.slug]), {'item_id': item.id, 'qty': '2'})
        anon.post(reverse('store:checkout', args=[store.slug]), {'name': 'ز', 'phone': '1', 'payment_method': 'bank'})
        order = OnlineOrder.objects.get(tenant=self.tenant)
        resp = self.client.post(reverse('store:manage_order_approve', args=[order.pk]))
        self.assertTrue(resp.json()['success'], resp.content)
