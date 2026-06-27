import json
from decimal import Decimal

from django.test import RequestFactory, TestCase

from apps.accounts.models import User
from apps.core.models import BusinessType, Tenant
from apps.purchases.models import SupplierLedger
from apps.suppliers.models import Supplier
from apps.suppliers.views import supplier_payment_cancel_api, supplier_payment_create_api, supplier_payments_table_api
from apps.treasury.models import Treasury, TreasuryMovement


class SupplierPaymentTests(TestCase):
    def setUp(self):
        self.business_type = BusinessType.objects.create(
            name='retail',
            name_ar='متجر',
            slug='retail',
        )
        self.tenant = Tenant.objects.create(
            name='Test Tenant',
            slug='test-tenant',
            business_type=self.business_type,
            hard_currency_mode=True,
            hard_currency='USD',
            exchange_rate=Decimal('100.00'),
        )
        self.user = User.objects.create_user(
            username='supplier-admin',
            password='secret123',
            tenant=self.tenant,
            is_tenant_admin=True,
        )
        self.factory = RequestFactory()

    def test_hc_supplier_payment_list_uses_supplier_currency(self):
        supplier = Supplier.objects.create(
            tenant=self.tenant,
            name='HC Supplier',
            currency='USD',
            opening_balance=Decimal('0.00'),
        )
        SupplierLedger.objects.create(
            tenant=self.tenant,
            supplier=supplier,
            entry_type='payment',
            amount=Decimal('-5000.00'),
            entry_date='2026-06-27',
            reference_type='supplier_payment_hc_cash',
            notes='سداد',
            hc_amount=Decimal('-50.00'),
            hc_currency='USD',
            hc_exchange_rate=Decimal('100.00'),
            running_balance=Decimal('-5000.00'),
            hc_running_balance=Decimal('-50.00'),
        )

        request = self.factory.get('/suppliers/payments/api/')
        request.user = self.user
        request.tenant = self.tenant

        response = supplier_payments_table_api(request)
        self.assertEqual(response.status_code, 200)

        payload = json.loads(response.content.decode())
        row = payload['data'][0]
        self.assertEqual(row['payment_method'], 'نقداً')
        self.assertEqual(row['amount'], '50.00')
        self.assertEqual(row['currency'], 'USD')

    def test_lc_supplier_payment_list_stays_in_supplier_currency(self):
        supplier = Supplier.objects.create(
            tenant=self.tenant,
            name='LC Supplier',
            currency='SDG',
            opening_balance=Decimal('0.00'),
        )
        SupplierLedger.objects.create(
            tenant=self.tenant,
            supplier=supplier,
            entry_type='payment',
            amount=Decimal('-5000.00'),
            entry_date='2026-06-27',
            reference_type='supplier_payment_cash',
            notes='سداد',
            hc_amount=Decimal('-50.00'),
            hc_currency='USD',
            hc_exchange_rate=Decimal('100.00'),
            running_balance=Decimal('-5000.00'),
            hc_running_balance=Decimal('-50.00'),
        )

        request = self.factory.get('/suppliers/payments/api/')
        request.user = self.user
        request.tenant = self.tenant

        response = supplier_payments_table_api(request)
        self.assertEqual(response.status_code, 200)

        payload = json.loads(response.content.decode())
        row = payload['data'][0]
        self.assertEqual(row['amount'], '5000.00')
        self.assertEqual(row['currency'], 'SDG')

    def test_hc_supplier_cash_payment_cancel_restores_hc_treasury(self):
        supplier = Supplier.objects.create(
            tenant=self.tenant,
            name='HC Supplier',
            currency='USD',
            opening_balance=Decimal('0.00'),
        )
        treasury = Treasury.objects.create(
            tenant=self.tenant,
            name='HC Treasury',
            code='HC-1',
            is_hard_currency=True,
            is_active=True,
            current_balance=Decimal('1000.00'),
        )

        create_request = self.factory.post(
            '/suppliers/payments/create/',
            data=json.dumps({
                'supplier_id': supplier.id,
                'amount': '50.00',
                'payment_date': '2026-06-27',
                'method': 'cash',
                'treasury_id': treasury.id,
                'notes': 'سداد',
                'pay_in_hc': True,
                'exchange_rate': '100.00',
            }),
            content_type='application/json',
        )
        create_request.user = self.user
        create_request.tenant = self.tenant

        create_response = supplier_payment_create_api(create_request)
        self.assertEqual(create_response.status_code, 200)

        payment = SupplierLedger.objects.get(
            tenant=self.tenant,
            entry_type='payment',
            reference_type='supplier_payment_hc_cash',
        )
        movement = TreasuryMovement.objects.get(
            tenant=self.tenant,
            reference_type='supplier_payment_hc_cash',
            reference_id=payment.id,
        )
        self.assertEqual(movement.amount, Decimal('50.00'))
        treasury.refresh_from_db()
        self.assertEqual(treasury.current_balance, Decimal('950.00'))

        cancel_request = self.factory.post(f'/suppliers/payments/{payment.id}/cancel/')
        cancel_request.user = self.user
        cancel_request.tenant = self.tenant

        cancel_response = supplier_payment_cancel_api(cancel_request, payment.id)
        self.assertEqual(cancel_response.status_code, 200)

        treasury.refresh_from_db()
        self.assertEqual(treasury.current_balance, Decimal('1000.00'))

        refund_movement = TreasuryMovement.objects.get(
            tenant=self.tenant,
            reference_type='supplier_payment_hc_cash_cancel',
            reference_id=payment.id,
        )
        self.assertEqual(refund_movement.amount, Decimal('50.00'))
        self.assertEqual(refund_movement.movement_type, 'receipt')
