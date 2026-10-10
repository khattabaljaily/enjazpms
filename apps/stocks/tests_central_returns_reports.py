"""
المرحلة 3 من الشراء المركزي: مرتجع الفرع إلى المخزن المركزي (يؤكده المركزي)، مرتجع الشراء
المركزي إلى المورد، وتقارير المخزن المركزي — مع عزل الفروع.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import (
    get_branch_supervisor_permission_keys, get_enterprise_owner_permission_keys,
)
from apps.items.models import ItemBatch
from apps.notifications.models import Notification
from apps.purchases.models import PurchaseInvoice, PurchaseReturn, SupplierLedger
from apps.purchases.tests_central_purchasing import CentralPurchasingBase
from apps.stocks.models import Shipment, Stock, StockQuantity
from apps.stocks.shipment_services import receive_shipment
from apps.stocks.tests_shipments import ShipmentBase
from apps.treasury.models import Treasury


class BranchReturnTests(ShipmentBase):
    def setUp(self):
        super().setUp()
        self.set_qty(self.s1, self.item, '8')

    def post_return(self, user, from_stock=None, qty='3', item=None):
        self.client.force_login(user)
        return self.client.post(reverse('stocks:return_create'), json.dumps({
            'from_stock': (from_stock or self.s1).pk, 'notes': 'تالف',
            'lines': [{'item_id': (item or self.item).pk, 'quantity': qty}]}),
            content_type='application/json')

    def test_branch_sends_return_stock_leaves_branch_and_central_is_notified(self):
        resp = self.post_return(self.u1)
        self.assertEqual(resp.status_code, 200, resp.content)
        sh = Shipment.objects.get()
        self.assertEqual((sh.direction, sh.status, sh.branch_id), ('to_central', 'in_transit', self.b1.pk))
        self.assertTrue(sh.shipment_number.startswith('RET-'))
        self.assertEqual(self.qty(self.s1), Decimal('5'))
        self.assertEqual(self.qty(self.central), Decimal('10'))  # لم يدخل المركزي بعد
        self.assertTrue(Notification.objects.filter(branch__isnull=True, title='مرتجع من فرع في الطريق').exists())

    def test_central_confirms_actual_quantity_and_difference_returns_to_branch(self):
        self.post_return(self.u1)
        sh = Shipment.objects.get()
        lid = sh.lines.get().id
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:shipment_receive', args=[sh.pk]),
                                json.dumps({'lines': {str(lid): '2'}}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.qty(self.central), Decimal('12'))
        self.assertEqual(self.qty(self.s1), Decimal('6'))  # 8 - 3 + فرق 1
        sh.refresh_from_db()
        self.assertTrue(sh.has_difference)
        self.assertTrue(Notification.objects.filter(branch=self.b1, title='فرق في استلام مرتجعك').exists())

    def test_branch_cannot_confirm_its_own_return_and_owner_cannot_confirm_a_distribution(self):
        self.post_return(self.u1)
        sh = Shipment.objects.get()
        self.client.force_login(self.u1)
        self.assertIn(self.client.post(reverse('stocks:shipment_receive', args=[sh.pk]), '{}',
                                       content_type='application/json').status_code, (302, 403, 404))
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'in_transit')

    def test_branch_cancels_return_in_transit_and_gets_stock_back(self):
        self.post_return(self.u1)
        sh = Shipment.objects.get()
        resp = self.client.post(reverse('stocks:shipment_cancel', args=[sh.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.qty(self.s1), Decimal('8'))
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'cancelled')

    def test_owner_cannot_cancel_a_branch_return_nor_create_one(self):
        self.post_return(self.u1)
        sh = Shipment.objects.get()
        self.client.force_login(self.user)
        self.assertIn(self.client.post(reverse('stocks:shipment_cancel', args=[sh.pk])).status_code, (302, 403, 404))
        self.assertIn(self.client.get(reverse('stocks:return_create')).status_code, (302, 403, 404))
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'in_transit')

    def test_other_branch_cannot_see_or_cancel_return(self):
        self.post_return(self.u1)
        sh = Shipment.objects.get()
        self.client.force_login(self.u2)
        self.assertEqual(self.client.get(reverse('stocks:shipment_detail', args=[sh.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('stocks:shipment_cancel', args=[sh.pk])).status_code, 404)

    def test_branch_cannot_return_from_another_branch_or_central_stock(self):
        for stock in (self.s2, self.central):
            resp = self.post_return(self.u1, from_stock=stock)
            self.assertEqual(resp.status_code, 400, stock.name)
        self.assertEqual(Shipment.objects.count(), 0)

    def test_insufficient_quantity_leaves_no_shipment(self):
        resp = self.post_return(self.u1, qty='9')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Shipment.objects.count(), 0)
        self.assertEqual(self.qty(self.s1), Decimal('8'))

    def test_batches_travel_with_the_return(self):
        self.item.track_batch = True
        self.item.track_expiry = True
        self.item.save()
        exp = timezone.localdate() + timedelta(days=90)
        ItemBatch.objects.create(tenant=self.tenant, item=self.item, stock=self.s1, batch_number='BR-1',
                                 expiry_date=exp, quantity_received=8, quantity_remaining=8)
        self.post_return(self.u1)
        sh = Shipment.objects.get()
        self.assertEqual(ItemBatch.objects.get(stock=self.s1, batch_number='BR-1').quantity_remaining, Decimal('5'))
        receive_shipment(sh, {}, self.user)
        central_batch = ItemBatch.objects.get(stock=self.central, batch_number='BR-1')
        self.assertEqual((central_batch.quantity_remaining, central_batch.expiry_date), (Decimal('3'), exp))

    def test_branch_list_shows_both_directions_for_own_branch_only(self):
        self.post_return(self.u1)
        small = [{'item': self.item, 'quantity': Decimal('2')}]
        self.make_shipment(to_stock=self.s1, lines=small, send=True)
        self.make_shipment(to_stock=self.s2, lines=small, send=True)
        self.client.force_login(self.u1)
        rows = self.client.get(reverse('stocks:shipment_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual(sorted(r['direction'] for r in rows), ['to_branch', 'to_central'])
        self.assertTrue(all(r['branch'] == 'فرع الشمال' for r in rows))

    def test_form_page_for_branch(self):
        self.client.force_login(self.u1)
        page = self.client.get(reverse('stocks:return_create'))
        self.assertContains(page, 'مرتجع جديد إلى المخزن المركزي')
        self.assertContains(page, 'مخزن الشمال')
        self.assertNotContains(page, 'مخزن الجنوب')


class CentralPurchaseReturnTests(CentralPurchasingBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        resp = self.post_invoice(self.user, self.central, self.csup, pm='credit', action='confirm')
        self.assertTrue(resp.json()['success'], resp.content)
        self.invoice = PurchaseInvoice.objects.get(tenant=self.tenant)

    def post_return_to_supplier(self, qty='2', refund='balance', action='confirm'):
        self.client.force_login(self.user)
        line = self.invoice.lines.get()
        return self.client.post(reverse('purchases:return_create', args=[self.invoice.pk]), json.dumps({
            'header': {'return_date': timezone.localdate().isoformat(), 'refund_method': refund, 'reason': 'تالف'},
            'lines': [{'invoice_line_id': line.pk, 'returned_quantity': qty}], 'action': action}),
            content_type='application/json')

    def test_owner_returns_goods_to_supplier_and_ledger_drops(self):
        resp = self.post_return_to_supplier(qty='2')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertEqual(self.qty(self.central), Decimal('3'))
        total = sum(e.amount for e in SupplierLedger.objects.filter(tenant=self.tenant, supplier=self.csup))
        self.assertEqual(total, Decimal('30.00'))  # 50 - 20

    def test_cash_refund_goes_to_head_office_treasury_and_cancel_reverses_it(self):
        before = Treasury.objects.get(pk=self.ho_treasury.pk).current_balance
        resp = self.post_return_to_supplier(qty='2', refund='cash')
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertEqual(Treasury.objects.get(pk=self.ho_treasury.pk).current_balance - before, Decimal('20'))
        pr = PurchaseReturn.objects.get(tenant=self.tenant)
        resp = self.client.post(reverse('purchases:return_cancel', args=[pr.pk]))
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertEqual(Treasury.objects.get(pk=self.ho_treasury.pk).current_balance, before)
        self.assertEqual(self.qty(self.central), Decimal('5'))

    def test_return_lists_are_scoped(self):
        self.post_return_to_supplier(qty='1')
        self.client.force_login(self.user)
        rows = self.client.get(reverse('purchases:return_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual(len(rows), 1)
        self.client.force_login(self.branch_user)
        # مستخدم الفرع (بلا صلاحية مرتجعات) أو بها: لا يرى مرتجعاً مركزياً
        pr = PurchaseReturn.objects.get(tenant=self.tenant)
        for name in ('return_detail', 'return_lines_api'):
            resp = self.client.get(reverse(f'purchases:{name}', args=[pr.pk]))
            self.assertIn(resp.status_code, (302, 403, 404), name)

    def test_branch_user_with_returns_permission_still_cannot_touch_central_returns(self):
        from apps.accounts.models import PermissionGroup
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='مرتجعات الفرع', is_active=True,
            permissions={'view_purchase_returns': True, 'add_purchase_returns': True})
        self.branch_user.permission_groups.add(group)
        self.post_return_to_supplier(qty='1')
        pr = PurchaseReturn.objects.get(tenant=self.tenant)
        self.client.force_login(self.branch_user)
        self.assertEqual(self.client.get(reverse('purchases:return_detail', args=[pr.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('purchases:return_cancel', args=[pr.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('purchases:return_create', args=[self.invoice.pk])).status_code, 404)
        rows = self.client.get(reverse('purchases:return_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual(rows, [])

    def test_over_return_is_refused(self):
        resp = self.post_return_to_supplier(qty='9')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PurchaseReturn.objects.filter(status='confirmed').count(), 0)


class CentralReportsTests(ShipmentBase):
    """التقارير المركزية داخل أقسام التقارير: شحنات في الطريق، فروقات، مشتريات، ونطاق «المخزن المركزي»."""

    def setUp(self):
        super().setUp()
        self.sh = self.make_shipment(send=True)                       # في الطريق
        sh2 = self.make_shipment(lines=[{'item': self.plain, 'quantity': Decimal('3')}], send=True)
        receive_shipment(sh2, {self.line_ids(sh2)[self.plain.pk]: Decimal('1')}, self.u1)  # ناقص 2

    def test_shipment_reports_and_exports_for_owner(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('stocks:reports:shipments_in_transit_report'))
        self.assertEqual(page.status_code, 200)
        self.assertEqual([r['number'] for r in page.context['report']['rows']], [self.sh.shipment_number])
        page = self.client.get(reverse('stocks:reports:shipment_differences_report'))
        self.assertEqual([(r['item'], r['difference']) for r in page.context['report']['rows']],
                         [('مستلزم ب', Decimal('2'))])
        self.assertContains(page, '10.00')  # قيمة الفرق: 2 × تكلفة 5
        self.assertNotContains(page, ',00')
        for name in ('stocks:reports:shipments_in_transit_report_export',
                     'stocks:reports:shipment_differences_report_export',
                     'purchases:reports:central_purchases_report_export'):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)
            self.assertIn('text/csv', resp['Content-Type'])
        self.assertEqual(self.client.get(reverse('purchases:reports:central_purchases_report')).status_code, 200)

    def test_stock_reports_offer_central_warehouse_scope(self):
        self.client.force_login(self.user)
        url = reverse('stocks:reports:valuation_report')
        page = self.client.get(url)
        self.assertContains(page, 'value="central"')
        central = self.client.get(url, {'branch': 'central'})
        names = [row['item_name'] for cat in central.context['report']['categories'] for row in cat['items']]
        self.assertIn('دواء أ', names)                                  # 4 باقية في المركزي
        self.assertEqual(self.client.get(reverse('stocks:reports:valuation_report_export'),
                                         {'branch': 'central'}).status_code, 200)
        branch_only = self.client.get(url, {'branch': self.b2.pk})
        self.assertEqual(branch_only.context['report']['categories'], [])

    def test_old_combined_page_is_gone_and_links_live_in_reports(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse('stocks:central_reports')
        self.client.force_login(self.user)
        dash = self.client.get(reverse('core:dashboard'))
        self.assertContains(dash, reverse('stocks:reports:shipments_in_transit_report'))
        self.assertContains(dash, reverse('purchases:reports:central_purchases_report'))

    def test_reports_are_hidden_from_branches_and_non_hybrid(self):
        names = ('stocks:reports:shipments_in_transit_report', 'stocks:reports:shipment_differences_report',
                 'purchases:reports:central_purchases_report')
        self.client.force_login(self.u1)
        for name in names:
            self.assertIn(self.client.get(reverse(name)).status_code, (302, 403, 404), name)
        page = self.client.get(reverse('stocks:reports:valuation_report'), {'branch': 'central'})
        self.assertNotContains(page, 'value="central"')
        self.tenant.purchasing_mode = 'decentralized'
        self.tenant.save(update_fields=['purchasing_mode'])
        self.client.force_login(self.user)
        for name in names:
            self.assertIn(self.client.get(reverse(name)).status_code, (302, 403, 404), name)


class Phase3PermissionKeysTests(ShipmentBase):
    def test_key_ownership(self):
        owner = set(get_enterprise_owner_permission_keys())
        sup = set(get_branch_supervisor_permission_keys())
        central = {'receive_central_returns', 'view_central_purchase_returns', 'add_central_purchase_returns',
                   'view_central_reports'}
        self.assertLessEqual(central, owner)
        self.assertFalse(central & sup)
        self.assertIn('create_central_returns', sup)
        self.assertNotIn('create_central_returns', owner)
