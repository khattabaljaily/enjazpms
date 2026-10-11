"""طلبات الشراء من الفروع (النمط المركزي): الدورة كاملة، العزل، التنفيذ بالشحنة وبأمر الشراء، والصلاحيات."""
import json
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import PermissionGroup, User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_item, make_stock
from apps.notifications.models import Notification
from apps.purchases.models import PurchaseInvoice
from apps.purchases.tests_central_mode import CentralModeBase
from apps.requisitions.models import PurchaseRequisition, PurchaseRequisitionLine
from apps.stocks.models import Shipment, Stock


class RequisitionBase(CentralModeBase):
    def setUp(self):
        super().setUp()
        self.b2 = Branch.objects.create(tenant=self.tenant, name='فرع الجنوب')
        self.s2 = make_stock(self.tenant, name='مخزن الجنوب', branch=self.b2)
        self.item2 = make_item(self.tenant, name='صنف ثان', cost_price='5')
        self.sup2 = User.objects.create_user(
            username='rq-sup2', password='x12345678', tenant=self.tenant, branch=self.b2, is_branch_supervisor=True)
        if not Stock.objects.filter(tenant=self.tenant, is_central=True).exists():  # الهجين: ينشئه مدير النشاط يدوياً
            Stock.objects.create(tenant=self.tenant, name='المركزي', is_central=True)
        self.set_quantity(self.item, self.central_stock, 100)
        self.set_quantity(self.item2, self.central_stock, 100)

    def api_save(self, user, *, action='save_draft', stock=None, lines=None, url=None, **extra):
        self.client.force_login(user)
        body = {
            'stock_id': (stock or self.s1).id, 'request_date': timezone.localdate().isoformat(),
            'priority': 'normal', 'notes': 'ملاحظة',
            'lines': lines if lines is not None else [{'item_id': self.item.id, 'quantity': '10'},
                                                      {'item_id': self.item2.id, 'quantity': '4', 'notes': 'عاجل للعرض'}],
            'action': action, **extra}
        return self.client.post(url or reverse('requisitions:create'), json.dumps(body),
                                content_type='application/json')

    def make_submitted(self):
        resp = self.api_save(self.supervisor, action='submit')
        self.assertTrue(resp.json()['success'], resp.content)
        return PurchaseRequisition.objects.get(tenant=self.tenant)

    def review(self, req, decisions=None, action='approve', notes='', user=None):
        self.client.force_login(user or self.user)
        lines = [{'line_id': l.pk, 'approved_quantity': str((decisions or {}).get(l.item_id, l.requested_quantity))}
                 for l in req.lines.all()]
        return self.client.post(reverse('requisitions:review', args=[req.pk]),
                                json.dumps({'action': action, 'lines': lines, 'notes': notes}),
                                content_type='application/json')

    def make_approved(self, decisions=None):
        req = self.make_submitted()
        resp = self.review(req, decisions)
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        return req

    def ship(self, req, lines, send=False, user=None):
        self.client.force_login(user or self.user)
        body = {'to_stock': req.stock_id, 'requisition_id': req.pk, 'send': send,
                'lines': [{'item_id': i, 'quantity': str(q)} for i, q in lines]}
        return self.client.post(reverse('stocks:shipment_create'), json.dumps(body), content_type='application/json')


class RequisitionGateTests(TenantTestCase):
    def test_basic_plan_has_no_requisitions(self):
        self.assertEqual(self.client.get(reverse('requisitions:list')).status_code, 404)


class HybridGateTests(RequisitionBase):
    mode = 'hybrid'

    def test_requisitions_are_404_in_hybrid(self):
        for user in (self.user, self.supervisor):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('requisitions:list')).status_code, 404)
        self.client.force_login(self.supervisor)
        self.assertEqual(self.client.get(reverse('requisitions:create')).status_code, 404)
        self.client.force_login(self.user)  # مدير النشاط لا يملك صلاحية إنشاء طلبات أصلاً
        self.assertEqual(self.client.get(reverse('requisitions:create')).status_code, 302)

    def test_hybrid_sidebar_has_no_requisitions(self):
        self.client.force_login(self.supervisor)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertNotIn('طلب شراء جديد', html)
        self.client.force_login(self.user)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertNotIn('طلبات الفروع', html)


class RequisitionBranchFlowTests(RequisitionBase):
    def test_save_draft_then_edit_then_submit(self):
        resp = self.api_save(self.supervisor)
        self.assertTrue(resp.json()['success'], resp.content)
        req = PurchaseRequisition.objects.get(tenant=self.tenant)
        self.assertEqual((req.status, req.branch_id, req.stock_id), ('draft', self.b1.id, self.s1.id))
        self.assertEqual(req.number, 'REQ-00001')
        self.assertEqual(req.lines.count(), 2)
        self.assertEqual(Notification.objects.filter(tenant=self.tenant).count(), 0)

        resp = self.api_save(self.supervisor, url=reverse('requisitions:edit', args=[req.pk]),
                             lines=[{'item_id': self.item.id, 'quantity': '7'}], priority='urgent')
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        self.assertEqual((req.lines.count(), req.priority), (1, 'urgent'))

        resp = self.client.post(reverse('requisitions:submit', args=[req.pk]))
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        self.assertEqual(req.status, 'submitted')
        note = Notification.objects.get(tenant=self.tenant)
        self.assertIsNone(note.branch_id)
        self.assertEqual(note.priority, 'high')

    def test_submitted_requisition_cannot_be_edited(self):
        req = self.make_submitted()
        resp = self.client.get(reverse('requisitions:edit', args=[req.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_validation_errors(self):
        for lines in ([], [{'item_id': self.item.id, 'quantity': '0'}],
                      [{'item_id': self.item.id, 'quantity': '1'}, {'item_id': self.item.id, 'quantity': '2'}],
                      [{'item_id': self.item.id, 'quantity': 'abc'}]):
            resp = self.api_save(self.supervisor, lines=lines)
            self.assertEqual(resp.status_code, 400, lines)
        resp = self.api_save(self.supervisor, stock=self.s2)  # مخزن فرع آخر
        self.assertEqual(resp.status_code, 400)
        resp = self.api_save(self.supervisor, stock=self.central_stock)
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(PurchaseRequisition.objects.exists())

    def test_branch_cancel_draft_and_submitted_but_not_approved(self):
        req = self.make_submitted()
        resp = self.client.post(reverse('requisitions:cancel', args=[req.pk]))
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        self.assertEqual(req.status, 'cancelled')

    def test_branch_cannot_cancel_approved(self):
        req = self.make_approved()
        self.client.force_login(self.supervisor)
        resp = self.client.post(reverse('requisitions:cancel', args=[req.pk]))
        self.assertEqual(resp.status_code, 400)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')

    def test_items_api_shows_branch_stock_balance(self):
        self.set_quantity(self.item, self.s1, 3)
        self.client.force_login(self.supervisor)
        resp = self.client.get(reverse('requisitions:items_api'), {'stock_id': self.s1.id, 'q': 'مركزي'})
        row = next(r for r in resp.json()['items'] if r['id'] == self.item.id)
        self.assertEqual(row['stock_qty'], 3.0)

    def test_list_and_detail_pages_render_for_branch(self):
        req = self.make_submitted()
        self.client.force_login(self.supervisor)
        self.assertContains(self.client.get(reverse('requisitions:list')), req.number)
        self.assertContains(self.client.get(reverse('requisitions:detail', args=[req.pk])), req.number)
        self.assertEqual(self.client.get(reverse('requisitions:create')).status_code, 200)
        self.assertEqual(self.client.get(reverse('requisitions:edit', args=[req.pk])).status_code, 404)


class RequisitionIsolationTests(RequisitionBase):
    def test_other_branch_cannot_see_or_open_requisition(self):
        req = self.make_submitted()
        self.client.force_login(self.sup2)
        self.assertNotContains(self.client.get(reverse('requisitions:list')), req.number)
        self.assertEqual(self.client.get(reverse('requisitions:detail', args=[req.pk])).status_code, 404)
        for name in ('submit', 'cancel'):
            self.assertEqual(self.client.post(reverse(f'requisitions:{name}', args=[req.pk])).status_code, 404)

    def test_head_office_never_sees_drafts(self):
        self.api_save(self.supervisor)
        req = PurchaseRequisition.objects.get()
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('requisitions:list')), req.number)
        self.assertEqual(self.client.get(reverse('requisitions:detail', args=[req.pk])).status_code, 404)

    def test_branch_cannot_review(self):
        req = self.make_submitted()
        resp = self.review(req, user=self.supervisor)
        self.assertEqual(resp.status_code, 403)  # مرفوض الصلاحية
        req.refresh_from_db()
        self.assertEqual(req.status, 'submitted')

    def test_branch_user_without_the_permission_is_denied(self):
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='فرع بلا طلبات', is_active=True, permissions={'view_stocks': True})
        user = User.objects.create_user(username='rq-plain', password='x12345678', tenant=self.tenant, branch=self.b1)
        user.permission_groups.add(group)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('requisitions:list')).status_code, 302)
        self.assertEqual(self.api_save(user).status_code, 403)

    def test_head_office_user_with_view_only_cannot_review(self):
        req = self.make_submitted()
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='إدارة قراءة', is_active=True, scope='admin',
            permissions={'view_branch_requisitions': True})
        user = User.objects.create_user(username='rq-ho-view', password='x12345678', tenant=self.tenant)
        user.permission_groups.add(group)
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse('requisitions:list')), req.number)
        self.assertEqual(self.review(req, user=user).status_code, 403)


class RequisitionReviewTests(RequisitionBase):
    def test_partial_approval_and_branch_notification(self):
        req = self.make_submitted()
        l1, l2 = req.lines.order_by('id')
        resp = self.review(req, {l1.item_id: 6, l2.item_id: 0}, notes='نقص ميزانية')
        self.assertEqual(resp.json()['status'], 'approved')
        req.refresh_from_db()
        l1.refresh_from_db(); l2.refresh_from_db()
        self.assertEqual((l1.approved_quantity, l2.approved_quantity), (Decimal('6'), Decimal('0')))
        self.assertEqual((req.status, req.reviewed_by_id, req.review_notes), ('approved', self.user.id, 'نقص ميزانية'))
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, branch=self.b1, title='اعتُمد طلب الشراء').exists())

    def test_all_zero_becomes_rejected_and_reject_action(self):
        req = self.make_submitted()
        resp = self.review(req, {l.item_id: 0 for l in req.lines.all()})
        self.assertEqual(resp.json()['status'], 'rejected')
        req2 = PurchaseRequisition.objects.get(pk=req.pk)
        self.assertEqual(req2.status, 'rejected')
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, branch=self.b1, title='رُفض طلب الشراء').exists())

    def test_reject_action_rejects_everything(self):
        req = self.make_submitted()
        resp = self.review(req, action='reject', notes='لا حاجة')
        self.assertEqual(resp.json()['status'], 'rejected')
        self.assertEqual(set(req.lines.values_list('approved_quantity', flat=True)), {Decimal('0')})

    def test_cannot_approve_more_than_requested_or_negative_or_twice(self):
        req = self.make_submitted()
        l1 = req.lines.order_by('id').first()
        for bad in (l1.requested_quantity + 1, -1, 'x'):
            resp = self.review(req, {l1.item_id: bad})
            self.assertEqual(resp.status_code, 400, bad)
        req.refresh_from_db()
        self.assertEqual(req.status, 'submitted')
        self.assertTrue(self.review(req).json()['success'])
        self.assertEqual(self.review(req).status_code, 400)

    def test_head_office_can_cancel_submitted_and_approved_without_shipments(self):
        req = self.make_approved()
        self.client.force_login(self.user)
        resp = self.client.post(reverse('requisitions:cancel', args=[req.pk]))
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, branch=self.b1, title='أُلغي طلب الشراء').exists())

    def test_detail_exposes_actions_by_role(self):
        req = self.make_submitted()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('requisitions:detail', args=[req.pk]))
        self.assertTrue(resp.context['can_review'])
        self.assertFalse(resp.context['can_ship'])
        self.review(req)
        resp = self.client.get(reverse('requisitions:detail', args=[req.pk]))
        self.assertTrue(resp.context['can_ship'])
        self.assertTrue(resp.context['can_buy'])
        self.assertFalse(resp.context['can_review'])
        self.client.force_login(self.supervisor)
        resp = self.client.get(reverse('requisitions:detail', args=[req.pk]))
        self.assertFalse(any(resp.context[k] for k in ('can_review', 'can_ship', 'can_buy', 'can_edit')))


class RequisitionFulfilmentTests(RequisitionBase):
    def test_shipment_prefill_page_for_approved_requisition(self):
        req = self.make_approved({self.item2.id: 0})
        self.client.force_login(self.user)
        resp = self.client.get(reverse('stocks:shipment_create'), {'requisition': req.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['requisition'].pk, req.pk)
        self.assertEqual(resp.context['prefill_stock_id'], self.s1.id)
        self.assertEqual([(r['id'], r['quantity']) for r in resp.context['prefill_lines']], [(self.item.id, 10.0)])

    def test_prefill_ignored_for_non_approved_or_other_tenant_state(self):
        req = self.make_submitted()
        self.client.force_login(self.user)
        resp = self.client.get(reverse('stocks:shipment_create'), {'requisition': req.pk})
        self.assertNotIn('requisition', resp.context)

    def test_full_fulfilment_by_shipments_marks_requisition_fulfilled(self):
        req = self.make_approved()
        resp = self.ship(req, [(self.item.id, 6)], send=True)
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        line = req.lines.get(item=self.item)
        self.assertEqual((line.fulfilled_quantity, line.remaining_quantity), (Decimal('6'), Decimal('4')))
        resp = self.ship(req, [(self.item.id, 4), (self.item2.id, 4)], send=True)
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        self.assertEqual(req.status, 'fulfilled')
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, branch=self.b1, title='اكتمل توريد طلب الشراء').exists())
        self.client.force_login(self.user)
        self.assertFalse(self.client.get(reverse('requisitions:detail', args=[req.pk])).context['can_ship'])

    def test_cannot_exceed_approved_quantity_even_with_drafts(self):
        req = self.make_approved({self.item.id: 5})
        self.assertTrue(self.ship(req, [(self.item.id, 4)]).json()['success'])  # مسودة
        resp = self.ship(req, [(self.item.id, 2)])
        self.assertEqual(resp.status_code, 400)
        self.assertIn('تتجاوز', resp.json()['message'])

    def test_cannot_ship_item_outside_requisition_or_unapproved_or_wrong_stock(self):
        extra = make_item(self.tenant, name='خارج الطلب')
        req = self.make_approved({self.item2.id: 0})
        self.assertEqual(self.ship(req, [(extra.id, 1)]).status_code, 400)
        self.assertEqual(self.ship(req, [(self.item2.id, 1)]).status_code, 400)  # رُفض البند
        sub = self.make_submitted_other()
        self.assertEqual(self.ship(sub, [(self.item.id, 1)]).status_code, 400)  # غير معتمد

    def make_submitted_other(self):
        resp = self.api_save(self.supervisor, action='submit')
        return PurchaseRequisition.objects.exclude(status='approved').get()

    def test_cancelling_an_in_transit_shipment_reopens_the_requisition(self):
        req = self.make_approved()
        self.assertTrue(self.ship(req, [(self.item.id, 10), (self.item2.id, 4)], send=True).json()['success'])
        req.refresh_from_db()
        self.assertEqual(req.status, 'fulfilled')
        shipment = Shipment.objects.get(requisition=req)
        self.client.force_login(self.user)
        resp = self.client.post(reverse('stocks:shipment_cancel', args=[shipment.pk]),
                                '{}', content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')

    def test_receiving_less_reopens_remaining(self):
        req = self.make_approved()
        self.assertTrue(self.ship(req, [(self.item.id, 10), (self.item2.id, 4)], send=True).json()['success'])
        shipment = Shipment.objects.get(requisition=req)
        self.client.force_login(self.supervisor)
        lines = {str(l.pk): ('6' if l.item_id == self.item.id else str(l.quantity_sent)) for l in shipment.lines.all()}
        resp = self.client.post(reverse('stocks:shipment_receive', args=[shipment.pk]),
                                json.dumps({'lines': lines}), content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.content)
        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        line = req.lines.get(item=self.item)
        self.assertEqual((line.fulfilled_quantity, line.remaining_quantity), (Decimal('6'), Decimal('4')))

    def test_cannot_cancel_requisition_with_active_shipment(self):
        req = self.make_approved()
        self.assertTrue(self.ship(req, [(self.item.id, 1)]).json()['success'])
        self.client.force_login(self.user)
        resp = self.client.post(reverse('requisitions:cancel', args=[req.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_shipment_detail_links_back_to_requisition(self):
        req = self.make_approved()
        sid = self.ship(req, [(self.item.id, 1)]).json()['id']
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('stocks:shipment_detail', args=[sid])), req.number)


class RequisitionPurchaseOrderTests(RequisitionBase):
    def test_order_form_is_prefilled_from_requisition(self):
        req = self.make_approved({self.item2.id: 3})
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:order_create'), {'requisition': req.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['requisition'].pk, req.pk)
        lines = json.loads(resp.context['existing_lines'])
        self.assertEqual({(l['item_id'], l['quantity']) for l in lines},
                         {(self.item.id, '10.0000'), (self.item2.id, '3.0000')})

    def test_plain_order_form_unchanged_without_requisition(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('purchases:order_create'))
        self.assertIsNone(resp.context['requisition'])
        self.assertEqual(resp.context['existing_lines'], '[]')

    def test_invoice_is_linked_to_requisition(self):
        req = self.make_approved()
        csup = self.csup
        self.client.force_login(self.user)
        body = {'header': {'stock_id': self.central_stock.id, 'supplier_id': csup.id, 'requisition_id': req.pk,
                           'invoice_date': timezone.localdate().isoformat(), 'payment_method': 'credit'},
                'lines': [{'item_id': self.item.id, 'quantity': '10', 'unit_cost': '10', 'tax_rate': '0'}],
                'action': 'confirm'}
        resp = self.client.post(reverse('purchases:order_create'), json.dumps(body), content_type='application/json')
        self.assertTrue(resp.json()['success'], resp.content)
        self.assertEqual(PurchaseInvoice.objects.get(tenant=self.tenant).requisition_id, req.pk)
        resp = self.client.get(reverse('requisitions:detail', args=[req.pk]))
        self.assertEqual(len(resp.context['invoices']), 1)

    def test_requisition_id_ignored_when_not_approved(self):
        req = self.make_submitted()
        self.client.force_login(self.user)
        body = {'header': {'stock_id': self.central_stock.id, 'supplier_id': self.csup.id, 'requisition_id': req.pk,
                           'invoice_date': timezone.localdate().isoformat(), 'payment_method': 'credit'},
                'lines': [{'item_id': self.item.id, 'quantity': '1', 'unit_cost': '10', 'tax_rate': '0'}],
                'action': 'save_draft'}
        self.client.post(reverse('purchases:order_create'), json.dumps(body), content_type='application/json')
        self.assertIsNone(PurchaseInvoice.objects.get(tenant=self.tenant).requisition_id)


class RequisitionSidebarAndPermissionsTests(RequisitionBase):
    def test_branch_sidebar_has_requisitions_but_no_purchasing(self):
        self.client.force_login(self.supervisor)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('طلب شراء جديد', html)
        self.assertIn(f'href="{reverse("requisitions:list")}"', html)
        self.assertNotIn(f'href="{reverse("purchases:order_list")}"', html)
        self.assertNotIn('طلبات الفروع', html)

    def test_head_office_sidebar_has_branch_requests_inside_purchases(self):
        self.client.force_login(self.user)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('طلبات الفروع', html)
        self.assertNotIn('طلب شراء جديد', html)

    def test_permission_keys_split_between_roles(self):
        for key in ('view_purchase_requisitions', 'add_purchase_requisitions', 'cancel_purchase_requisitions'):
            self.assertTrue(self.supervisor.has_perm_key(key), key)
            self.assertFalse(self.user.has_perm_key(key), key)
        for key in ('view_branch_requisitions', 'review_branch_requisitions'):
            self.assertTrue(self.user.has_perm_key(key), key)
            self.assertFalse(self.supervisor.has_perm_key(key), key)

    def test_permission_tree_shows_sections_only_for_central_tenants(self):
        from apps.accounts.permissions import filter_schema_for_tenant
        schema = filter_schema_for_tenant(self.tenant)
        self.assertIn('طلبات الشراء', schema)
        self.assertIn('طلبات الفروع', schema)
        self.tenant.purchasing_mode = 'hybrid'
        self.tenant.save(update_fields=['purchasing_mode'])
        schema = filter_schema_for_tenant(self.tenant)
        self.assertNotIn('طلبات الشراء', schema)
        self.assertNotIn('طلبات الفروع', schema)
