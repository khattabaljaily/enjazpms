"""التعاميم: النشر التلقائي عند الإجراءات الحساسة، والرؤية، وعدم التعطيل."""
import json
from decimal import Decimal

from django.urls import reverse

from apps.accounts.models import User
from apps.circulars.models import Circular, CircularRead
from apps.circulars.services import publish_circular
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_item, make_stock


class CircularPublishTests(TenantTestCase):
    def _save_opening(self, stock, item, qty):
        return self.client.post(reverse('stocks:opening_save_api'), json.dumps(
            {'stock_id': stock.pk, 'rows': [{'item_id': item.pk, 'quantity': qty}]}),
            content_type='application/json')

    def setUp(self):
        super().setUp()
        self.stock = make_stock(self.tenant, name='المخزن الرئيسي')
        self.item = make_item(self.tenant, name='سكر')

    def test_first_opening_entry_does_not_publish(self):
        self.assertEqual(self._save_opening(self.stock, self.item, '10').status_code, 200)
        self.assertEqual(Circular.objects.count(), 0)

    def test_opening_correction_publishes_with_before_after(self):
        self._save_opening(self.stock, self.item, '10')
        self._save_opening(self.stock, self.item, '25')
        c = Circular.objects.get()
        self.assertEqual(c.event_key, 'stock.opening_edit')
        self.assertEqual(c.level, 'red')
        self.assertEqual(c.before, {'سكر': '10'})
        self.assertEqual(c.after, {'سكر': '25'})
        self.assertIn('من 10 إلى 25', c.message)
        self.assertEqual(c.actor, self.user)

    def test_publish_never_raises(self):
        self.assertIsNone(publish_circular(None, event_key='x', message='m'))  # بلا مشترك

    def test_circular_page_visible_and_mark_read(self):
        publish_circular(event_key='stock.opening_edit', message='رسالة', tenant=self.tenant, user=self.user)
        resp = self.client.get(reverse('circulars:list'))
        self.assertContains(resp, 'رسالة')
        self.client.post(reverse('circulars:mark_all_read'))
        self.assertEqual(CircularRead.objects.filter(user=self.user).count(), 1)


class CircularBranchVisibilityTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def test_branch_user_sees_own_branch_and_general_only(self):
        b1 = Branch.objects.create(tenant=self.tenant, name='ب1')
        b2 = Branch.objects.create(tenant=self.tenant, name='ب2')
        for branch, msg in ((b1, 'تعميم-ب1'), (b2, 'تعميم-ب2'), (None, 'تعميم-عام')):
            publish_circular(event_key='k', message=msg, tenant=self.tenant, user=self.user, branch=branch)
        staff = User.objects.create_user(username='crc-st', password='x12345678', tenant=self.tenant, branch=b1)
        self.client.force_login(staff)
        body = self.client.get(reverse('circulars:list')).content.decode()
        self.assertIn('تعميم-ب1', body)
        self.assertIn('تعميم-عام', body)
        self.assertNotIn('تعميم-ب2', body)


class CircularEventsTests(TenantTestCase):
    """الأحداث الحساسة تنشر تعميماً."""

    def _post_json(self, url, data):
        return self.client.post(url, json.dumps(data), content_type='application/json')

    def test_user_delete_publishes(self):
        victim = User.objects.create_user(username='victim1', password='x12345678', tenant=self.tenant)
        self.client.post(reverse('accounts:user_delete_api', args=[victim.pk]))
        c = Circular.objects.get(event_key='user.delete')
        self.assertIn('victim1', c.message)

    def test_permission_group_delete_publishes(self):
        from apps.accounts.models import PermissionGroup
        g = PermissionGroup.objects.create(tenant=self.tenant, name='مجموعة س', permissions={})
        self.client.post(reverse('accounts:permission_group_delete_api', args=[g.pk]))
        self.assertTrue(Circular.objects.filter(event_key='permission_group.delete', message__contains='مجموعة س').exists())

    def test_item_delete_with_stock_publishes_but_without_stock_does_not(self):
        stock = make_stock(self.tenant, name='م')
        with_stock = make_item(self.tenant, name='له رصيد')
        without = make_item(self.tenant, name='بلا رصيد')
        from apps.stocks.models import StockQuantity
        StockQuantity.objects.update_or_create(tenant=self.tenant, stock=stock, item=with_stock, defaults={'quantity': Decimal('5')})
        self.client.post(reverse('items:delete_api', args=[without.pk]))
        self.assertEqual(Circular.objects.filter(event_key='item.delete').count(), 0)
        self.client.post(reverse('items:delete_api', args=[with_stock.pk]))
        self.assertEqual(Circular.objects.filter(event_key='item.delete').count(), 1)

    def test_settings_change_publishes_before_after(self):
        from apps.core.models import Settings
        Settings.objects.get_or_create(tenant=self.tenant)
        resp = self._post_json(reverse('core:tenant_settings_update_api'),
                               {'section': 'system', 'data': {'tax_enabled': True, 'tax_value': '15'}})
        self.assertEqual(resp.status_code, 200, resp.content)
        c = Circular.objects.get(event_key='system.settings_edit')
        self.assertEqual(c.after.get('tax_value'), '15')
        # حفظ بلا تغيير لا ينشر شيئاً
        before = Circular.objects.count()
        self._post_json(reverse('core:tenant_settings_update_api'),
                        {'section': 'system', 'data': {'tax_enabled': True, 'tax_value': '15'}})
        self.assertEqual(Circular.objects.count(), before)

    def test_stocktake_shortage_publishes(self):
        from django.utils import timezone
        from apps.stocks.models import Stocktake, StocktakeLine, StockQuantity
        stock = make_stock(self.tenant, name='مخزن الجرد')
        item = make_item(self.tenant, name='صنف الجرد')
        StockQuantity.objects.update_or_create(tenant=self.tenant, stock=stock, item=item, defaults={'quantity': Decimal('10')})
        st = Stocktake.objects.create(tenant=self.tenant, stock=stock, stocktake_date=timezone.localdate(), stocktake_number='ST-1')
        StocktakeLine.objects.create(tenant=self.tenant, stocktake=st, item=item,
                                     system_quantity=Decimal('10'), counted_quantity=Decimal('7'))
        resp = self.client.post(reverse('stocks:stocktake_confirm', args=[st.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        c = Circular.objects.get(event_key='stock.stocktake_shortage')
        self.assertIn('نقص 3', c.message)


class CircularLoginAlertTests(TenantTestCase):
    def test_red_unread_banner_shows_until_list_is_opened(self):
        publish_circular(event_key='user.delete', message='حذف', tenant=self.tenant, user=self.user)
        page = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('تعميم خطير غير مقروء', page)
        self.client.get(reverse('circulars:list'))  # فتح الصندوق يعلّم ما فيه مقروءًا
        self.assertNotIn('تعميم خطير غير مقروء', self.client.get(reverse('core:dashboard')).content.decode())

    def test_yellow_circular_does_not_trigger_banner(self):
        publish_circular(event_key='expense.cancel', message='ن', tenant=self.tenant, user=self.user)
        self.assertNotIn('تعميم خطير غير مقروء', self.client.get(reverse('core:dashboard')).content.decode())

    def test_treasury_transfer_cancel_publishes(self):
        from apps.treasury.models import Treasury, TreasuryTransfer
        a = Treasury.objects.create(tenant=self.tenant, name='خزينة أ', current_balance=Decimal('1000'))
        b = Treasury.objects.create(tenant=self.tenant, name='خزينة ب', current_balance=Decimal('1000'))
        resp = self.client.post('/treasury/api/transfer/', json.dumps({
            'from_treasury': a.pk, 'to_treasury': b.pk, 'from_amount': '100', 'to_amount': '100',
            'exchange_rate': '1', 'transfer_date': '2026-10-11'}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        tr = TreasuryTransfer.objects.get()
        resp = self.client.post(reverse('treasury:transfer_cancel_api', args=[tr.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        c = Circular.objects.get(event_key='transfer.cancel')
        self.assertIn('خزينة أ', c.message)
