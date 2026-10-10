"""
شحنات المخزن المركزي (النمط الهجين) — المرحلة 2:
مسودة ← إرسال (في الطريق) ← تأكيد الفرع للكمية الفعلية؛ الفرق يعود للمركزي؛ الدفعات (FEFO)؛
الإلغاء؛ وعزل الشحنات بين الفروع.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PermissionGroup, User
from apps.accounts.permissions import (
    get_branch_supervisor_permission_keys, get_enterprise_owner_permission_keys,
)
from apps.core.models import Branch, TenantCapabilities
from apps.core.test_utils import TenantTestCase, make_item, make_stock
from apps.items.models import ItemBatch
from apps.notifications.models import Notification
from apps.sales.models import StockMovement
from apps.stocks.models import Shipment, Stock, StockQuantity
from apps.stocks.shipment_services import (
    cancel_shipment, create_shipment, receive_shipment, send_shipment,
)


class ShipmentBase(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant,
            defaults={f.name: True for f in TenantCapabilities._meta.get_fields()
                      if getattr(f, 'name', '').startswith('has_')})
        self.tenant.purchasing_mode = 'hybrid'
        self.tenant.save(update_fields=['purchasing_mode'])
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.s1 = make_stock(self.tenant, name='مخزن الشمال', branch=self.b1)
        self.s2 = make_stock(self.tenant, name='مخزن الجنوب', branch=self.b2)
        self.central = Stock.objects.create(tenant=self.tenant, name='المخزن المركزي', is_central=True)
        self.item = make_item(self.tenant, name='دواء أ', cost_price='12')
        self.plain = make_item(self.tenant, name='مستلزم ب', cost_price='5')
        recv = PermissionGroup.objects.create(
            tenant=self.tenant, name='استلام', is_active=True,
            permissions={'view_incoming_shipments': True, 'receive_incoming_shipments': True})
        self.u1 = User.objects.create_user(username='sh-u1', password='x12345678', tenant=self.tenant,
                                           branch=self.b1, is_branch_supervisor=True)
        self.u2 = User.objects.create_user(username='sh-u2', password='x12345678', tenant=self.tenant,
                                           branch=self.b2, is_branch_supervisor=True)
        self.set_qty(self.central, self.item, '10')
        self.set_qty(self.central, self.plain, '4')

    def set_qty(self, stock, item, qty):
        StockQuantity.objects.update_or_create(
            tenant=self.tenant, stock=stock, item=item,
            defaults={'quantity': Decimal(qty), 'reserved_quantity': Decimal('0')})

    def qty(self, stock, item=None):
        sq = StockQuantity.objects.filter(tenant=self.tenant, stock=stock, item=item or self.item).first()
        return sq.quantity if sq else Decimal('0')

    def make_shipment(self, to_stock=None, lines=None, send=False):
        sh = create_shipment(
            self.tenant, self.central, to_stock or self.s1, timezone.localdate(), 'ملاحظة',
            lines or [{'item': self.item, 'quantity': Decimal('6')}], user=self.user)
        if send:
            send_shipment(sh, self.user)
            sh.refresh_from_db()
        return sh

    def line_ids(self, sh):
        return {ln.item_id: ln.id for ln in sh.lines.all()}


class ShipmentServiceTests(ShipmentBase):
    def test_send_moves_quantity_out_of_central_and_keeps_branch_unchanged(self):
        sh = self.make_shipment(send=True)
        self.assertEqual(sh.status, 'in_transit')
        self.assertEqual(self.qty(self.central), Decimal('4'))
        self.assertEqual(self.qty(self.s1), Decimal('0'))
        mv = StockMovement.objects.get(reference_type='central_shipment', stock=self.central)
        self.assertEqual((mv.direction, mv.quantity, mv.unit_cost), ('out', Decimal('6'), Decimal('12')))

    def test_full_receipt_adds_to_branch(self):
        sh = self.make_shipment(send=True)
        receive_shipment(sh, {}, self.u1)
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'received')
        self.assertFalse(sh.has_difference)
        self.assertEqual(self.qty(self.s1), Decimal('6'))
        self.assertEqual(self.qty(self.central), Decimal('4'))
        self.assertEqual(sh.received_by, self.u1)
        mv = StockMovement.objects.get(reference_type='central_shipment', stock=self.s1)
        self.assertEqual((mv.direction, mv.quantity, mv.unit_cost), ('in', Decimal('6'), Decimal('12')))

    def test_partial_receipt_credits_actual_and_returns_difference_to_central(self):
        sh = self.make_shipment(send=True)
        lid = self.line_ids(sh)[self.item.pk]
        receive_shipment(sh, {lid: Decimal('4')}, self.u1)
        sh.refresh_from_db()
        self.assertTrue(sh.has_difference)
        self.assertEqual(self.qty(self.s1), Decimal('4'))
        self.assertEqual(self.qty(self.central), Decimal('6'))  # 10 - 6 + فرق 2 = 6
        line = sh.lines.get()
        self.assertEqual((line.quantity_sent, line.quantity_received, line.difference), (Decimal('6'), Decimal('4'), Decimal('2')))
        self.assertTrue(StockMovement.objects.filter(reference_type='central_shipment_difference', stock=self.central).exists())

    def test_zero_receipt_returns_everything(self):
        sh = self.make_shipment(send=True)
        lid = self.line_ids(sh)[self.item.pk]
        receive_shipment(sh, {lid: Decimal('0')}, self.u1)
        self.assertEqual(self.qty(self.s1), Decimal('0'))
        self.assertEqual(self.qty(self.central), Decimal('10'))

    def test_invalid_received_quantities_are_rejected_without_changes(self):
        sh = self.make_shipment(send=True)
        lid = self.line_ids(sh)[self.item.pk]
        for bad in (Decimal('7'), Decimal('-1')):
            with self.assertRaises(ValueError):
                receive_shipment(sh, {lid: bad}, self.u1)
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'in_transit')
        self.assertEqual(self.qty(self.s1), Decimal('0'))

    def test_cannot_receive_twice(self):
        sh = self.make_shipment(send=True)
        receive_shipment(sh, {}, self.u1)
        with self.assertRaises(ValueError):
            receive_shipment(sh, {}, self.u1)
        self.assertEqual(self.qty(self.s1), Decimal('6'))

    def test_insufficient_central_stock_blocks_send_and_leaves_draft(self):
        sh = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('11')}])
        with self.assertRaises(ValueError):
            send_shipment(sh, self.user)
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'draft')
        self.assertEqual(self.qty(self.central), Decimal('10'))
        self.assertFalse(StockMovement.objects.filter(reference_type='central_shipment').exists())

    def test_partial_failure_on_send_rolls_back_all_lines(self):
        sh = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('2')},
                                       {'item': self.plain, 'quantity': Decimal('9')}])
        with self.assertRaises(ValueError):
            send_shipment(sh, self.user)
        self.assertEqual(self.qty(self.central), Decimal('10'))

    def test_cancel_in_transit_restores_central(self):
        sh = self.make_shipment(send=True)
        cancel_shipment(sh, self.user)
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'cancelled')
        self.assertEqual(self.qty(self.central), Decimal('10'))
        self.assertEqual(self.qty(self.s1), Decimal('0'))

    def test_cancel_draft_has_no_stock_effect_and_received_cannot_be_cancelled(self):
        draft = self.make_shipment()
        cancel_shipment(draft, self.user)
        self.assertEqual(self.qty(self.central), Decimal('10'))
        sh = self.make_shipment(send=True)
        receive_shipment(sh, {}, self.u1)
        with self.assertRaises(ValueError):
            cancel_shipment(sh, self.user)

    def test_creation_validation(self):
        with self.assertRaises(ValueError):  # من مخزن فرع
            create_shipment(self.tenant, self.s1, self.s2, timezone.localdate(), '', [{'item': self.item, 'quantity': 1}])
        with self.assertRaises(ValueError):  # إلى المركزي
            create_shipment(self.tenant, self.central, self.central, timezone.localdate(), '', [{'item': self.item, 'quantity': 1}])
        with self.assertRaises(ValueError):  # بلا بنود
            create_shipment(self.tenant, self.central, self.s1, timezone.localdate(), '', [{'item': self.item, 'quantity': 0}])
        with self.assertRaises(ValueError):  # بند مكرر
            create_shipment(self.tenant, self.central, self.s1, timezone.localdate(), '',
                            [{'item': self.item, 'quantity': 1}, {'item': self.item, 'quantity': 2}])

    def test_not_allowed_when_not_hybrid(self):
        self.tenant.purchasing_mode = 'decentralized'
        with self.assertRaises(ValueError):
            create_shipment(self.tenant, self.central, self.s1, timezone.localdate(), '', [{'item': self.item, 'quantity': 1}])

    def test_ordinary_transfer_from_or_to_central_is_refused(self):
        from apps.stocks.models import StockTransfer, StockTransferLine
        from apps.stocks.services import confirm_stock_transfer
        t = StockTransfer.objects.create(tenant=self.tenant, from_stock=self.central, to_stock=self.s1,
                                         transfer_date=timezone.localdate())
        StockTransferLine.objects.create(tenant=self.tenant, transfer=t, item=self.item, quantity=Decimal('1'))
        with self.assertRaises(ValueError):
            confirm_stock_transfer(t)
        self.assertEqual(self.qty(self.central), Decimal('10'))


class ShipmentBatchTests(ShipmentBase):
    def setUp(self):
        super().setUp()
        self.item.track_batch = True
        self.item.track_expiry = True
        self.item.save()
        today = timezone.localdate()
        self.b_early = ItemBatch.objects.create(
            tenant=self.tenant, item=self.item, stock=self.central, batch_number='B-EARLY',
            expiry_date=today + timedelta(days=30), quantity_received=3, quantity_remaining=3)
        self.b_late = ItemBatch.objects.create(
            tenant=self.tenant, item=self.item, stock=self.central, batch_number='B-LATE',
            expiry_date=today + timedelta(days=300), quantity_received=5, quantity_remaining=5)
        self.set_qty(self.central, self.item, '8')

    def rem(self, stock, number):
        b = ItemBatch.objects.filter(tenant=self.tenant, item=self.item, stock=stock, batch_number=number).first()
        return b.quantity_remaining if b else None

    def test_send_consumes_earliest_expiry_first(self):
        sh = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('6')}], send=True)
        self.assertEqual(self.rem(self.central, 'B-EARLY'), Decimal('0'))
        self.assertEqual(self.rem(self.central, 'B-LATE'), Decimal('2'))
        rows = list(sh.lines.get().batches.order_by('id').values_list('batch_number', 'quantity_sent'))
        self.assertEqual(rows, [('B-EARLY', Decimal('3')), ('B-LATE', Decimal('3'))])

    def test_receipt_creates_batches_with_expiry_at_branch(self):
        sh = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('6')}], send=True)
        receive_shipment(sh, {}, self.u1)
        self.assertEqual(self.rem(self.s1, 'B-EARLY'), Decimal('3'))
        self.assertEqual(self.rem(self.s1, 'B-LATE'), Decimal('3'))
        self.assertEqual(ItemBatch.objects.get(stock=self.s1, batch_number='B-EARLY').expiry_date,
                         self.b_early.expiry_date)

    def test_partial_receipt_returns_difference_to_the_right_batch(self):
        sh = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('6')}], send=True)
        lid = self.line_ids(sh)[self.item.pk]
        receive_shipment(sh, {lid: Decimal('4')}, self.u1)
        self.assertEqual(self.rem(self.s1, 'B-EARLY'), Decimal('3'))
        self.assertEqual(self.rem(self.s1, 'B-LATE'), Decimal('1'))
        self.assertEqual(self.rem(self.central, 'B-LATE'), Decimal('4'))  # 5 - 3 + فرق 2
        self.assertEqual(self.rem(self.central, 'B-EARLY'), Decimal('0'))

    def test_second_receipt_merges_into_existing_branch_batch(self):
        sh1 = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('3')}], send=True)
        receive_shipment(sh1, {}, self.u1)
        ItemBatch.objects.filter(pk=self.b_early.pk).update(quantity_remaining=3)  # دفعة جديدة بنفس الرقم
        self.set_qty(self.central, self.item, '8')
        sh2 = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('3')}], send=True)
        receive_shipment(sh2, {}, self.u1)
        self.assertEqual(ItemBatch.objects.filter(stock=self.s1, batch_number='B-EARLY').count(), 1)
        self.assertEqual(self.rem(self.s1, 'B-EARLY'), Decimal('6'))

    def test_cancel_in_transit_restores_batches(self):
        sh = self.make_shipment(lines=[{'item': self.item, 'quantity': Decimal('6')}], send=True)
        cancel_shipment(sh, self.user)
        self.assertEqual(self.rem(self.central, 'B-EARLY'), Decimal('3'))
        self.assertEqual(self.rem(self.central, 'B-LATE'), Decimal('5'))

    def test_untracked_item_creates_no_batches(self):
        sh = self.make_shipment(lines=[{'item': self.plain, 'quantity': Decimal('2')}], send=True)
        receive_shipment(sh, {}, self.u1)
        self.assertFalse(ItemBatch.objects.filter(item=self.plain).exists())


class ShipmentViewTests(ShipmentBase):
    def setUp(self):
        super().setUp()
        self.treasury_group = None

    def create_via_api(self, send=False, qty='6'):
        self.client.force_login(self.user)
        return self.client.post(reverse('stocks:shipment_create'), json.dumps({
            'to_stock': self.s1.pk, 'shipment_date': timezone.localdate().isoformat(), 'notes': 'x',
            'lines': [{'item_id': self.item.pk, 'quantity': qty}], 'send': send}),
            content_type='application/json')

    def test_owner_creates_and_sends_via_api_and_branch_is_notified(self):
        resp = self.create_via_api(send=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        sh = Shipment.objects.get()
        self.assertEqual(sh.status, 'in_transit')
        self.assertEqual(self.qty(self.central), Decimal('4'))
        note = Notification.objects.get(branch=self.b1)
        self.assertIn(sh.shipment_number, note.message)

    def test_owner_form_page_lists_branch_stocks(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('stocks:shipment_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'فرع الشمال — مخزن الشمال')
        self.assertNotContains(resp, '>المخزن المركزي — ')

    def test_branch_user_receives_with_actual_quantities(self):
        self.create_via_api(send=True)
        sh = Shipment.objects.get()
        lid = sh.lines.get().id
        self.client.force_login(self.u1)
        resp = self.client.post(reverse('stocks:shipment_receive', args=[sh.pk]),
                                json.dumps({'lines': {str(lid): '5'}}), content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()['has_difference'])
        self.assertEqual(self.qty(self.s1), Decimal('5'))
        self.assertEqual(self.qty(self.central), Decimal('5'))
        self.assertTrue(Notification.objects.filter(branch__isnull=True, title='فرق في استلام شحنة').exists())

    def test_other_branch_cannot_see_or_receive(self):
        self.create_via_api(send=True)
        sh = Shipment.objects.get()
        self.client.force_login(self.u2)
        self.assertEqual(self.client.get(reverse('stocks:shipment_detail', args=[sh.pk])).status_code, 404)
        resp = self.client.post(reverse('stocks:shipment_receive', args=[sh.pk]), '{}', content_type='application/json')
        self.assertEqual(resp.status_code, 404)
        rows = self.client.get(reverse('stocks:shipment_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual(rows, [])
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'in_transit')

    def test_branch_sees_only_sent_shipments_not_drafts(self):
        self.create_via_api(send=False)
        sh = Shipment.objects.get()
        self.client.force_login(self.u1)
        self.assertEqual(self.client.get(reverse('stocks:shipment_detail', args=[sh.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('stocks:shipment_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data'], [])

    def test_owner_sees_all_and_branch_user_cannot_create_send_or_cancel(self):
        self.create_via_api(send=True)
        sh = Shipment.objects.get()
        self.client.force_login(self.u1)
        self.assertIn(self.client.get(reverse('stocks:shipment_create')).status_code, (302, 403, 404))
        for name in ('shipment_send', 'shipment_cancel'):
            resp = self.client.post(reverse(f'stocks:{name}', args=[sh.pk]))
            self.assertIn(resp.status_code, (302, 403, 404), name)
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'in_transit')
        self.client.force_login(self.user)
        self.assertEqual(len(self.client.get(reverse('stocks:shipment_api'),
                                             {'draw': 1, 'start': 0, 'length': 50}).json()['data']), 1)

    def test_owner_cannot_receive_for_a_branch(self):
        self.create_via_api(send=True)
        sh = Shipment.objects.get()
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:shipment_receive', args=[sh.pk]), '{}', content_type='application/json')
        self.assertIn(resp.status_code, (302, 403, 404))
        sh.refresh_from_db()
        self.assertEqual(sh.status, 'in_transit')

    def test_owner_cancels_in_transit_via_api(self):
        self.create_via_api(send=True)
        sh = Shipment.objects.get()
        resp = self.client.post(reverse('stocks:shipment_cancel', args=[sh.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.qty(self.central), Decimal('10'))

    def test_detail_pages_render_for_both_sides(self):
        self.create_via_api(send=True)
        sh = Shipment.objects.get()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('stocks:shipment_detail', args=[sh.pk])), 'إلغاء الشحنة')
        self.client.force_login(self.u1)
        page = self.client.get(reverse('stocks:shipment_detail', args=[sh.pk]))
        self.assertContains(page, 'تأكيد الاستلام')
        self.assertContains(page, 'recv-qty')
        self.assertEqual(self.client.get(reverse('stocks:shipment_list')).status_code, 200)

    def test_not_hybrid_denies_everything(self):
        self.tenant.purchasing_mode = 'decentralized'
        self.tenant.save(update_fields=['purchasing_mode'])
        self.client.force_login(self.user)
        self.assertIn(self.client.get(reverse('stocks:shipment_list')).status_code, (302, 403, 404))
        self.assertIn(self.client.get(reverse('stocks:shipment_create')).status_code, (302, 403, 404))


class ShipmentPermissionKeysTests(ShipmentBase):
    def test_key_ownership(self):
        owner = set(get_enterprise_owner_permission_keys())
        sup = set(get_branch_supervisor_permission_keys())
        central = {'view_central_shipments', 'add_central_shipments', 'cancel_central_shipments'}
        incoming = {'view_incoming_shipments', 'receive_incoming_shipments'}
        self.assertLessEqual(central, owner)
        self.assertFalse(central & sup)
        self.assertLessEqual(incoming, sup)
        self.assertFalse(incoming & owner)
