from django.test import Client
from django.urls import reverse

from apps.core.test_utils import TenantTestCase, make_item
from apps.store.models import StoreSettings


class StoreCartManualQtyTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self.store = StoreSettings.objects.create(tenant=self.tenant, is_enabled=True, status_override='open',
                                                  show_price_list=True)
        self.item = make_item(self.tenant, name='صنف متجر')
        self.set_quantity(self.item, self.default_stock, '20')
        self.anon = Client()

    def test_add_with_manual_qty_and_update_by_typing(self):
        self.anon.post(reverse('store:cart_add', args=[self.store.slug]), {'item_id': self.item.id, 'qty': '7'})
        self.anon.post(reverse('store:cart_update', args=[self.store.slug]), {'item_id': self.item.id, 'qty': '12'})
        r = self.anon.get(reverse('store:cart', args=[self.store.slug]))
        self.assertContains(r, 'name="qty"')
        self.assertEqual(r.context['cart_items'][0]['qty'], 12)

    def test_invalid_qty_does_not_crash(self):
        r = self.anon.post(reverse('store:cart_add', args=[self.store.slug]), {'item_id': self.item.id, 'qty': 'abc'})
        self.assertEqual(r.status_code, 302)
        r = self.anon.post(reverse('store:cart_update', args=[self.store.slug]), {'item_id': self.item.id, 'qty': 'x'})
        self.assertEqual(r.status_code, 302)


class PriceListColumnsTests(TenantTestCase):
    subscription_plan = 'pro'

    def setUp(self):
        super().setUp()
        self.store, _ = StoreSettings.objects.update_or_create(
            tenant=self.tenant,
            defaults=dict(is_enabled=True, show_price_list=True, show_out_of_stock=True))
        make_item(self.tenant, name='صنف قائمة')

    def test_default_shows_all_columns(self):
        r = self.client.get(reverse('store:price_list', args=[self.store.slug]))
        for h in ('Trade Name', 'Generic Name', 'W. Price', 'R. Price', 'Exp Date'):
            self.assertContains(r, h)

    def test_selected_columns_only(self):
        self.store.price_list_columns = ['name', 'retail']
        self.store.save()
        r = self.client.get(reverse('store:price_list', args=[self.store.slug]))
        self.assertContains(r, 'R. Price')
        self.assertNotContains(r, 'W. Price')
        self.assertNotContains(r, 'Generic Name')
        self.assertNotContains(r, 'Exp Date')

    def test_settings_post_saves_columns(self):
        self.client.post(reverse('store:manage_settings'), {
            'display_name': 'x', 'show_prices': 'on', 'show_price_list': 'on',
            'price_list_columns': ['name', 'unit', 'bogus'],
        })
        self.store.refresh_from_db()
        self.assertEqual(self.store.price_list_columns, ['name', 'unit'])


class StoreBranchSelectionTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        from apps.core.models import Branch
        from apps.core.test_utils import make_stock
        from apps.stocks.models import StockQuantity
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع أ')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع ب')
        self.s1 = make_stock(self.tenant, name='م1', branch=self.b1, is_default=True)
        self.s2 = make_stock(self.tenant, name='م2', branch=self.b2, is_default=True)
        self.i1 = make_item(self.tenant, name='صنف أ')
        self.i2 = make_item(self.tenant, name='صنف ب')
        StockQuantity.objects.filter(stock=self.s1, item=self.i1).update(quantity=5)
        StockQuantity.objects.filter(stock=self.s2, item=self.i2).update(quantity=5)
        self.store, _ = StoreSettings.objects.update_or_create(
            tenant=self.tenant, defaults=dict(is_enabled=True, status_override='open'))
        self.anon = Client()

    def _front(self, **params):
        return self.anon.get(reverse('store:storefront', args=[self.store.slug]), params)

    def test_branch_name_shown_and_products_follow_branch(self):
        r = self._front(branch=self.b1.id)
        self.assertContains(r, 'فرع أ')
        self.assertContains(r, 'صنف أ')
        self.assertNotContains(r, 'صنف ب')
        r = self._front(branch=self.b2.id)
        self.assertContains(r, 'صنف ب')
        self.assertNotContains(r, 'صنف أ')

    def test_choice_persists_and_invalid_branch_falls_back(self):
        self._front(branch=self.b2.id)
        self.assertContains(self._front(), 'صنف ب')
        self.assertContains(self._front(branch=99999), 'فرع')

    def test_order_records_branch_and_approval_uses_its_stock(self):
        from apps.store.models import OnlineOrder
        self._front(branch=self.b2.id)
        self.anon.post(reverse('store:cart_add', args=[self.store.slug]), {'item_id': self.i2.id, 'qty': '2'})
        r = self.anon.post(reverse('store:checkout', args=[self.store.slug]),
                           {'name': 'زائر', 'phone': '0912345678', 'payment_method': 'bank'})
        self.assertEqual(r.status_code, 302)
        order = OnlineOrder.objects.get(tenant=self.tenant)
        self.assertEqual(order.branch_id, self.b2.id)
        from apps.store.services import approve_order
        invoice = approve_order(order)
        self.assertEqual(invoice.stock_id, self.s2.id)
        self.assertEqual(invoice.branch_id, self.b2.id)
