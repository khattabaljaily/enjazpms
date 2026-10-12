"""النمط الهجين: إلغاء مركزية المخزن المركزي وحذفه (ما لم تكن عليه سجلات)."""
from decimal import Decimal

from django.urls import reverse

from apps.core.models import Branch, TenantCapabilities
from apps.core.test_utils import TenantTestCase, make_item, make_stock
from apps.stocks.forms import StockForm
from apps.stocks.models import Stock, StockQuantity


class CentralStockEditTests(TenantTestCase):
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
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع')
        self.branch_stock = make_stock(self.tenant, name='مخزن الفرع', branch=self.branch)
        self.central = Stock.objects.create(tenant=self.tenant, name='المركزي', is_central=True)

    def _update(self, stock, **data):
        payload = {'name': stock.name, 'is_active': 'on'}
        payload.update(data)
        return self.client.post(reverse('stocks:update_api', args=[stock.pk]), payload)

    def _delete(self, stock):
        return self.client.post(reverse('stocks:delete_api', args=[stock.pk]))

    def test_edit_form_offers_the_central_flag_only_for_the_central_stock(self):
        self.assertIn('is_central', StockForm(instance=self.central, tenant=self.tenant).fields)
        self.assertNotIn('is_central', StockForm(instance=self.branch_stock, tenant=self.tenant).fields)

    def test_unchecking_central_turns_it_into_a_branch_stock(self):
        r = self._update(self.central, branch=self.branch.pk)          # الحقل غير مُرسل = غير محدد
        self.assertEqual(r.status_code, 200, r.content)
        self.central.refresh_from_db()
        self.assertFalse(self.central.is_central)
        self.assertEqual(self.central.branch_id, self.branch.pk)

    def test_unchecking_central_requires_a_branch_in_enterprise(self):
        r = self._update(self.central)
        self.assertEqual(r.status_code, 400)
        self.central.refresh_from_db()
        self.assertTrue(self.central.is_central)

    def test_keeping_it_checked_still_saves(self):
        r = self._update(self.central, is_central='on', notes='ملاحظة')
        self.assertEqual(r.status_code, 200, r.content)
        self.central.refresh_from_db()
        self.assertTrue(self.central.is_central)
        self.assertEqual(self.central.notes, 'ملاحظة')

    def test_cannot_uncheck_when_stock_has_records(self):
        item = make_item(self.tenant)
        StockQuantity.objects.filter(stock=self.central, item=item).update(quantity=Decimal('5'))
        r = self._update(self.central, branch=self.branch.pk)
        self.assertEqual(r.status_code, 400)
        self.assertIn('is_central', r.json()['errors'])
        self.central.refresh_from_db()
        self.assertTrue(self.central.is_central)

    def test_central_stock_without_records_can_be_deleted(self):
        r = self._delete(self.central)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(Stock.objects.filter(pk=self.central.pk).exists())

    def test_central_stock_with_records_cannot_be_deleted(self):
        item = make_item(self.tenant)
        StockQuantity.objects.filter(stock=self.central, item=item).update(quantity=Decimal('5'))
        r = self._delete(self.central)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Stock.objects.filter(pk=self.central.pk).exists())

    def test_after_deleting_a_new_central_stock_can_be_created(self):
        self._delete(self.central)
        self.assertIn('is_central', StockForm(tenant=self.tenant).fields)

    def test_list_page_renders_toggle_and_flag(self):
        page = self.client.get(reverse('stocks:list'))
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.context['central_exists'])
        self.assertContains(page, 'id_is_central')
        detail = self.client.get(reverse('stocks:detail_api', args=[self.central.pk])).json()
        self.assertTrue(detail['data']['is_central'])
