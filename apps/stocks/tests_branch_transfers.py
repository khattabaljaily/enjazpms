"""
التحويل بين الفروع: الفرع المحوِّل يرسل (تخرج الكمية فوراً)، والفرع المستلِم يعتمد الكمية الفعلية.
الفرق على الفرع المحوِّل: الزائد يُخصم من مخزنه، والناقص يبقى معلّقاً حتى يُعاد لمخزنه أو يُشطب تالفاً.
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_item, make_stock
from apps.items.models import ItemBatch
from apps.notifications.models import Notification
from apps.stocks.branch_transfer_services import (
    cancel_branch_transfer, receive_branch_transfer, send_branch_transfer, settle_branch_transfer_shortage,
)
from apps.stocks.models import StockDestruction, StockQuantity, StockTransfer, StockTransferLine
from apps.stocks.services import confirm_stock_transfer


class BranchTransferBase(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.b1 = Branch.objects.create(tenant=self.tenant, name='فرع الشمال')
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.s1 = make_stock(self.tenant, name='مخزن الشمال', branch=self.b1)
        self.s1b = make_stock(self.tenant, name='مخزن الشمال 2', branch=self.b1)
        self.s2 = make_stock(self.tenant, name='مخزن الجنوب', branch=self.b2)
        self.item = make_item(self.tenant, name='دواء أ', cost_price='12')
        self.u1 = User.objects.create_user(username='bt-u1', password='x12345678', tenant=self.tenant,
                                           branch=self.b1, is_branch_supervisor=True)
        self.u2 = User.objects.create_user(username='bt-u2', password='x12345678', tenant=self.tenant,
                                           branch=self.b2, is_branch_supervisor=True)
        self.set_qty(self.s1, '10')

    def set_qty(self, stock, qty, item=None):
        StockQuantity.objects.update_or_create(
            tenant=self.tenant, stock=stock, item=item or self.item,
            defaults={'quantity': Decimal(qty), 'reserved_quantity': Decimal('0')})

    def qty(self, stock, item=None):
        sq = StockQuantity.objects.filter(tenant=self.tenant, stock=stock, item=item or self.item).first()
        return sq.quantity if sq else Decimal('0')

    def make_transfer(self, quantity='6', to_stock=None, send=False):
        to_stock = to_stock or self.s2
        tr = StockTransfer.objects.create(
            tenant=self.tenant, from_stock=self.s1, to_stock=to_stock, transfer_date=timezone.localdate(),
            is_inter_branch=to_stock.branch_id != self.s1.branch_id)
        StockTransferLine.objects.create(tenant=self.tenant, transfer=tr, item=self.item, quantity=Decimal(quantity))
        if send:
            send_branch_transfer(tr, self.u1)
            tr.refresh_from_db()
        return tr

    def line(self, tr):
        return tr.lines.get()


class BranchTransferServiceTests(BranchTransferBase):
    def test_send_takes_quantity_out_and_keeps_receiver_unchanged(self):
        tr = self.make_transfer(send=True)
        self.assertEqual(tr.status, 'in_transit')
        self.assertIsNotNone(tr.sent_at)
        self.assertEqual(self.qty(self.s1), Decimal('4'))
        self.assertEqual(self.qty(self.s2), Decimal('0'))
        self.assertEqual(self.line(tr).unit_cost, Decimal('12'))

    def test_send_fails_when_quantity_insufficient(self):
        tr = self.make_transfer(quantity='11')
        with self.assertRaises(ValueError):
            send_branch_transfer(tr, self.u1)
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'draft')
        self.assertEqual(self.qty(self.s1), Decimal('10'))

    def test_full_receipt_has_no_difference(self):
        tr = self.make_transfer(send=True)
        receive_branch_transfer(tr, {}, self.u2)
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'received')
        self.assertFalse(tr.has_difference)
        self.assertEqual(tr.shortage_status, 'none')
        self.assertEqual(tr.received_by, self.u2)
        self.assertEqual(self.qty(self.s2), Decimal('6'))
        self.assertEqual(self.qty(self.s1), Decimal('4'))

    def test_shortage_stays_pending_then_restock_returns_it_to_sender(self):
        tr = self.make_transfer(send=True)
        receive_branch_transfer(tr, {self.line(tr).id: Decimal('4')}, self.u2)
        tr.refresh_from_db()
        self.assertTrue(tr.has_difference)
        self.assertEqual(tr.shortage_status, 'pending')
        self.assertEqual(self.qty(self.s2), Decimal('4'))
        self.assertEqual(self.qty(self.s1), Decimal('4'))  # الناقص معلّق: ليس في أي مخزن

        settle_branch_transfer_shortage(tr, 'restock', self.u1)
        tr.refresh_from_db()
        self.assertEqual(tr.shortage_status, 'restocked')
        self.assertEqual(tr.settled_by, self.u1)
        self.assertEqual(self.qty(self.s1), Decimal('6'))
        with self.assertRaises(ValueError):
            settle_branch_transfer_shortage(tr, 'restock', self.u1)

    def test_shortage_write_off_records_damaged_destruction_on_sender(self):
        tr = self.make_transfer(send=True)
        receive_branch_transfer(tr, {self.line(tr).id: Decimal('4')}, self.u2)
        settle_branch_transfer_shortage(tr, 'write_off', self.u1)
        tr.refresh_from_db()
        self.assertEqual(tr.shortage_status, 'written_off')
        destruction = tr.shortage_destruction
        self.assertEqual(destruction.stock, self.s1)
        self.assertEqual(destruction.status, 'confirmed')
        self.assertEqual(destruction.reason, 'damaged')
        self.assertEqual(destruction.total_quantity, Decimal('2'))
        self.assertEqual(destruction.total_value, Decimal('24'))
        self.assertEqual(self.qty(self.s1), Decimal('4'))

    def test_surplus_is_deducted_from_sender(self):
        tr = self.make_transfer(send=True)
        receive_branch_transfer(tr, {self.line(tr).id: Decimal('7')}, self.u2)
        tr.refresh_from_db()
        self.assertTrue(tr.has_difference)
        self.assertEqual(tr.shortage_status, 'none')
        self.assertEqual(self.qty(self.s2), Decimal('7'))
        self.assertEqual(self.qty(self.s1), Decimal('3'))
        self.assertEqual(self.line(tr).difference, Decimal('1'))

    def test_surplus_beyond_sender_stock_is_rejected_atomically(self):
        tr = self.make_transfer(send=True)
        with self.assertRaises(ValueError):
            receive_branch_transfer(tr, {self.line(tr).id: Decimal('11')}, self.u2)
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'in_transit')
        self.assertEqual(self.qty(self.s1), Decimal('4'))
        self.assertEqual(self.qty(self.s2), Decimal('0'))

    def test_negative_received_quantity_rejected(self):
        tr = self.make_transfer(send=True)
        with self.assertRaises(ValueError):
            receive_branch_transfer(tr, {self.line(tr).id: Decimal('-1')}, self.u2)

    def test_cancel_in_transit_returns_quantity(self):
        tr = self.make_transfer(send=True)
        cancel_branch_transfer(tr, self.u1)
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'cancelled')
        self.assertEqual(self.qty(self.s1), Decimal('10'))
        with self.assertRaises(ValueError):
            receive_branch_transfer(tr, {}, self.u2)

    def test_batches_move_fefo_and_shortage_restock_restores_them(self):
        self.item.track_expiry = True
        self.item.save(update_fields=['track_expiry'])
        today = date.today()
        near = ItemBatch.objects.create(tenant=self.tenant, item=self.item, stock=self.s1, batch_number='A',
                                        expiry_date=today + timedelta(days=30), quantity_received=4,
                                        quantity_remaining=4)
        far = ItemBatch.objects.create(tenant=self.tenant, item=self.item, stock=self.s1, batch_number='B',
                                       expiry_date=today + timedelta(days=300), quantity_received=6,
                                       quantity_remaining=6)
        tr = self.make_transfer(send=True)
        near.refresh_from_db(); far.refresh_from_db()
        self.assertEqual((near.quantity_remaining, far.quantity_remaining), (Decimal('0'), Decimal('4')))

        receive_branch_transfer(tr, {self.line(tr).id: Decimal('5')}, self.u2)
        got = {b.batch_number: b.quantity_remaining
               for b in ItemBatch.objects.filter(tenant=self.tenant, item=self.item, stock=self.s2)}
        self.assertEqual(got, {'A': Decimal('4'), 'B': Decimal('1')})

        settle_branch_transfer_shortage(tr, 'restock', self.u1)
        far.refresh_from_db()
        self.assertEqual(far.quantity_remaining, Decimal('5'))

    def test_instant_confirm_is_refused_for_inter_branch(self):
        tr = self.make_transfer()
        with self.assertRaises(ValueError):
            confirm_stock_transfer(tr)

    def test_same_branch_transfer_stays_instant(self):
        tr = self.make_transfer(to_stock=self.s1b)
        self.assertFalse(tr.is_inter_branch)
        confirm_stock_transfer(tr)
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'confirmed')
        self.assertEqual(self.qty(self.s1b), Decimal('6'))


class BranchTransferViewTests(BranchTransferBase):
    def post(self, name, tr, body=None):
        return self.client.post(reverse(f'stocks:{name}', args=[tr.pk]), json.dumps(body or {}),
                                content_type='application/json')

    def test_sender_creates_and_sends_to_other_branch(self):
        self.client.force_login(self.u1)
        page = self.client.get(reverse('stocks:transfer_create'))
        self.assertContains(page, 'مخزن الجنوب')
        resp = self.client.post(reverse('stocks:transfer_create'), json.dumps({
            'from_stock': self.s1.pk, 'to_stock': self.s2.pk,
            'lines': [{'item_id': self.item.pk, 'quantity': '3'}],
        }), content_type='application/json')
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['is_inter_branch'])
        tr = StockTransfer.objects.get(pk=data['id'])
        self.assertTrue(self.post('transfer_confirm', tr).json()['success'])
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'in_transit')
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, branch=self.b2,
                                                    title='تحويل وارد في الطريق').exists())

    def test_branch_cannot_send_from_another_branch_stock(self):
        self.client.force_login(self.u2)
        resp = self.client.post(reverse('stocks:transfer_create'), json.dumps({
            'from_stock': self.s1.pk, 'to_stock': self.s2.pk,
            'lines': [{'item_id': self.item.pk, 'quantity': '3'}],
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 404)

    def test_receiver_does_not_see_drafts(self):
        tr = self.make_transfer()
        self.client.force_login(self.u2)
        self.assertEqual(self.client.get(reverse('stocks:transfer_detail', args=[tr.pk])).status_code, 404)
        rows = self.client.get(reverse('stocks:transfer_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual(rows, [])
        self.assertEqual(self.post('transfer_delete', tr).status_code, 404)

    def test_receiver_sees_incoming_and_receives_with_shortage(self):
        tr = self.make_transfer(send=True)
        self.client.force_login(self.u2)
        page = self.client.get(reverse('stocks:transfer_detail', args=[tr.pk]))
        self.assertContains(page, 'اعتماد الاستلام')
        rows = self.client.get(reverse('stocks:transfer_api'), {'draw': 1, 'start': 0, 'length': 50}).json()['data']
        self.assertEqual([r['direction'] for r in rows], ['وارد'])

        self.assertEqual(self.post('transfer_cancel', tr).status_code, 403)
        resp = self.post('transfer_receive', tr, {'lines': {str(self.line(tr).id): '5'}})
        self.assertTrue(resp.json()['success'])
        tr.refresh_from_db()
        self.assertEqual(tr.shortage_status, 'pending')
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, branch=self.b1,
                                                    title='ناقص في استلام تحويل').exists())
        self.assertEqual(self.post('transfer_settle', tr, {'action': 'write_off'}).status_code, 403)

        self.client.force_login(self.u1)
        self.assertContains(self.client.get(reverse('stocks:transfer_detail', args=[tr.pk])), 'شطب تالفاً')
        self.assertTrue(self.post('transfer_settle', tr, {'action': 'write_off'}).json()['success'])
        self.assertEqual(StockDestruction.objects.filter(tenant=self.tenant, stock=self.s1).count(), 1)

    def test_sender_cannot_receive(self):
        tr = self.make_transfer(send=True)
        self.client.force_login(self.u1)
        self.assertEqual(self.post('transfer_receive', tr).status_code, 403)
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'in_transit')

    def test_sender_cancels_in_transit(self):
        tr = self.make_transfer(send=True)
        self.client.force_login(self.u1)
        self.assertTrue(self.post('transfer_cancel', tr).json()['success'])
        self.assertEqual(self.qty(self.s1), Decimal('10'))

    def test_owner_has_no_access_to_transfers(self):
        tr = self.make_transfer(send=True)
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('core:dashboard')), reverse('stocks:transfer_list'))
        self.assertEqual(self.client.get(reverse('stocks:transfer_list')).status_code, 404)
        self.assertEqual(self.client.get(reverse('stocks:transfer_create')).status_code, 404)
        self.assertEqual(self.client.get(reverse('stocks:transfer_detail', args=[tr.pk])).status_code, 404)
        for name in ('transfer_confirm', 'transfer_receive', 'transfer_settle', 'transfer_cancel', 'transfer_delete'):
            self.assertEqual(self.post(name, tr).status_code, 404, name)
        tr.refresh_from_db()
        self.assertEqual(tr.status, 'in_transit')

    def test_branch_user_sees_transfers_menu(self):
        self.client.force_login(self.u1)
        self.assertContains(self.client.get(reverse('core:dashboard')), reverse('stocks:transfer_list'))

    def test_unrelated_branch_cannot_see_transfer(self):
        b3 = Branch.objects.create(tenant=self.tenant, name='فرع الشرق')
        make_stock(self.tenant, name='مخزن الشرق', branch=b3)
        u3 = User.objects.create_user(username='bt-u3', password='x12345678', tenant=self.tenant,
                                      branch=b3, is_branch_supervisor=True)
        tr = self.make_transfer(send=True)
        self.client.force_login(u3)
        self.assertEqual(self.client.get(reverse('stocks:transfer_detail', args=[tr.pk])).status_code, 404)
        self.assertEqual(self.post('transfer_receive', tr).status_code, 404)
