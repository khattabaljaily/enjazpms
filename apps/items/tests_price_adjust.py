"""تعديل عام للأسعار بنسبة مئوية: الحساب، العملة الصعبة، الاستثناءات، التعليق الإلزامي، السجل."""
from decimal import Decimal

from django.urls import reverse

from apps.core.test_utils import TenantTestCase, make_item, make_supplier
from apps.items.models import Category, PriceAdjustment


class PriceAdjustBase(TenantTestCase):
    def post(self, name, **data):
        data.setdefault('percent', '10')
        data.setdefault('fields', ['selling', 'min'])
        return self.client.post(reverse(f'items:{name}'), data)

    def apply(self, **data):
        data.setdefault('note', 'زيادة الجمارك')
        return self.post('price_adjust_apply_api', **data)


class LocalCurrencyTests(PriceAdjustBase):
    def test_percent_applies_to_selected_prices_only(self):
        item = make_item(self.tenant, cost_price='40', selling_price='100', min_selling_price='90')
        resp = self.apply(percent='10')
        self.assertEqual(resp.status_code, 200, resp.content)
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('110.00'))
        self.assertEqual(item.min_selling_price, Decimal('99.00'))
        self.assertEqual(item.cost_price, Decimal('40'))          # التكلفة لم تُختر

    def test_cost_can_be_included_and_negative_percent_reduces(self):
        item = make_item(self.tenant, cost_price='40', selling_price='100')
        self.apply(percent='-10', fields=['selling', 'cost'])
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('90.00'))
        self.assertEqual(item.cost_price, Decimal('36.00'))

    def test_note_is_required_and_nothing_changes_without_it(self):
        item = make_item(self.tenant, selling_price='100')
        resp = self.post('price_adjust_apply_api', percent='10', note='  ')
        self.assertEqual(resp.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('100'))
        self.assertEqual(PriceAdjustment.objects.count(), 0)

    def test_invalid_percent_rejected(self):
        make_item(self.tenant, selling_price='100')
        for bad in ('0', 'abc', '-100', '5000', ''):
            self.assertEqual(self.apply(percent=bad).status_code, 400, bad)

    def test_requires_at_least_one_price_field(self):
        make_item(self.tenant, selling_price='100')
        self.assertEqual(self.apply(fields=[]).status_code, 400)

    def test_exclusions(self):
        cat = Category.objects.create(tenant=self.tenant, name='ألبان')
        sub = Category.objects.create(tenant=self.tenant, name='أجبان', parent=cat)
        sup = make_supplier(self.tenant)
        a = make_item(self.tenant, name='أ', selling_price='100')
        b = make_item(self.tenant, name='ب', selling_price='100', category=sub)       # فرعي من المستثنى
        c = make_item(self.tenant, name='ج', selling_price='100', supplier=sup)
        d = make_item(self.tenant, name='د', selling_price='100')                     # مستثنى بالاسم
        e = make_item(self.tenant, name='هـ', selling_price='100', item_type='service')
        f = make_item(self.tenant, name='و', selling_price='100', is_active=False)
        self.apply(percent='10', exclude_categories=[cat.id], exclude_suppliers=[sup.id],
                   exclude_items=[d.id], exclude_services='1', exclude_inactive='1')
        for it, expected in ((a, '110.00'), (b, '100'), (c, '100'), (d, '100'), (e, '100'), (f, '100')):
            it.refresh_from_db()
            self.assertEqual(it.selling_price, Decimal(expected), it.name)
        adj = PriceAdjustment.objects.get()
        self.assertEqual(adj.items_count, 1)
        self.assertEqual(adj.excluded_count, 5)
        self.assertIn('استثناء', adj.scope_summary)

    def test_only_selected_categories_include_children(self):
        cat = Category.objects.create(tenant=self.tenant, name='م')
        sub = Category.objects.create(tenant=self.tenant, name='م2', parent=cat)
        inside = make_item(self.tenant, name='داخل', selling_price='100', category=sub)
        outside = make_item(self.tenant, name='خارج', selling_price='100')
        self.apply(percent='10', only_categories=[cat.id])
        inside.refresh_from_db()
        outside.refresh_from_db()
        self.assertEqual(inside.selling_price, Decimal('110.00'))
        self.assertEqual(outside.selling_price, Decimal('100'))

    def test_history_lines_keep_old_and_new_values(self):
        item = make_item(self.tenant, selling_price='100')
        self.apply(percent='10', fields=['selling'])
        adj = PriceAdjustment.objects.get()
        line = adj.lines.get()
        self.assertEqual(line.item_id, item.id)
        self.assertEqual(line.old_values, {'selling_price': '100.00'})
        self.assertEqual(line.new_values, {'selling_price': '110.00'})
        self.assertEqual(adj.note, 'زيادة الجمارك')
        hist = self.client.get(reverse('items:price_adjust_history_api')).json()
        self.assertEqual(len(hist['results']), 1)

    def test_preview_changes_nothing(self):
        item = make_item(self.tenant, selling_price='100')
        resp = self.post('price_adjust_preview_api', percent='10')
        self.assertEqual(resp.json()['affected'], 1)
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('100'))
        self.assertEqual(PriceAdjustment.objects.count(), 0)

    def test_overflow_rolls_everything_back(self):
        ok = make_item(self.tenant, name='عادي', selling_price='100')
        make_item(self.tenant, name='ضخم', selling_price='9999999999.00')
        resp = self.apply(percent='1000')
        self.assertEqual(resp.status_code, 400)
        ok.refresh_from_db()
        self.assertEqual(ok.selling_price, Decimal('100'))
        self.assertEqual(PriceAdjustment.objects.count(), 0)

    def test_hc_fields_untouched_when_hc_mode_off(self):
        item = make_item(self.tenant, selling_price='100', selling_price_hc='2')
        self.apply(percent='10', fields=['selling'])
        item.refresh_from_db()
        self.assertEqual(item.selling_price_hc, Decimal('2'))


class HardCurrencyTests(PriceAdjustBase):
    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.exchange_rate = Decimal('2000')
        self.tenant.save()

    def test_local_and_hard_currency_move_together_at_current_rate(self):
        item = make_item(self.tenant, selling_price='4000000', cost_price='3000000',
                         selling_price_hc='2000', cost_price_hc='1500')
        self.apply(percent='10', fields=['selling', 'cost'])
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('4400000.00'))
        self.assertEqual(item.selling_price_hc, Decimal('2200.0000'))
        self.assertEqual(item.cost_price, Decimal('3300000.00'))
        self.assertEqual(item.cost_price_hc, Decimal('1650.0000'))
        self.assertEqual(item.selling_price, item.selling_price_hc * self.tenant.exchange_rate)

    def test_item_with_only_hard_currency_price(self):
        item = make_item(self.tenant, selling_price='0', selling_price_hc='50')
        self.apply(percent='10', fields=['selling'])
        item.refresh_from_db()
        self.assertEqual(item.selling_price_hc, Decimal('55.0000'))
        self.assertEqual(item.selling_price, Decimal('110000.00'))

    def test_item_with_only_local_price_gets_hc_equivalent(self):
        item = make_item(self.tenant, selling_price='1000')
        self.apply(percent='10', fields=['selling'])
        item.refresh_from_db()
        self.assertEqual(item.selling_price, Decimal('1100.00'))
        self.assertEqual(item.selling_price_hc, Decimal('0.5500'))

    def test_history_records_rate_and_currency(self):
        make_item(self.tenant, selling_price='1000')
        self.apply(percent='5', fields=['selling'])
        adj = PriceAdjustment.objects.get()
        self.assertTrue(adj.hard_currency_mode)
        self.assertEqual((adj.hard_currency, adj.exchange_rate), ('USD', Decimal('2000.0000')))

    def test_rate_change_afterwards_stays_consistent(self):
        from apps.items.services import reprice_items_for_rate
        item = make_item(self.tenant, selling_price='1000')
        self.apply(percent='10', fields=['selling'])
        reprice_items_for_rate(self.tenant, Decimal('2000'))
        item.refresh_from_db()
        self.assertEqual(item.selling_price, (item.selling_price_hc * Decimal('2000')).quantize(Decimal('0.01')))
