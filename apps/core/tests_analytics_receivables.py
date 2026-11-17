"""التحليلات المتقدمة: «مستحقات على العملاء» تشمل الرصيد الافتتاحي وقيود الدفتر، لا الفواتير الآجلة فقط."""
from decimal import Decimal

from django.urls import reverse

from apps.core.test_utils import TenantTestCase
from apps.customers.models import Customer
from apps.sales.models import CustomerLedger


class AnalyticsReceivablesTests(TenantTestCase):
    def _debt(self):
        resp = self.client.get(reverse('core:analytics'))
        self.assertEqual(resp.status_code, 200)
        return resp.context['customer_debt']

    def test_opening_balance_without_invoices_is_counted(self):
        Customer.objects.create(tenant=self.tenant, name='عميل افتتاحي', opening_balance=Decimal('500'))
        self.assertEqual(self._debt(), 500.0)

    def test_ledger_entries_adjust_balance_and_credit_balances_are_not_netted(self):
        a = Customer.objects.create(tenant=self.tenant, name='أ', opening_balance=Decimal('500'))
        Customer.objects.create(tenant=self.tenant, name='ب دائن', opening_balance=Decimal('-300'))
        CustomerLedger.objects.create(tenant=self.tenant, customer=a, entry_type='payment',
                                      amount=Decimal('-200'), entry_date='2026-11-01', running_balance=Decimal('300'))
        self.assertEqual(self._debt(), 300.0)
