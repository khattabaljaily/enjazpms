"""حذف الفرع المرتبط بسجلات ممنوع، وإعادة تفعيل فرع لا تتجاوز حد الباقة."""
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_customer, make_stock
from apps.treasury.models import Treasury


class BranchDeleteAndLimitTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'
    max_branches = 2

    def _delete(self, branch):
        return self.client.post(reverse('core:branch_delete_api', args=[branch.pk]))

    def test_empty_branch_can_be_deleted(self):
        b = Branch.objects.create(tenant=self.tenant, name='فارغ')
        self.assertEqual(self._delete(b).status_code, 200)
        self.assertFalse(Branch.objects.filter(pk=b.pk).exists())

    def test_branch_with_user_treasury_customer_is_not_deleted(self):
        for make in (
            lambda b: User.objects.create_user(username='bu', password='x12345678', tenant=self.tenant, branch=b),
            lambda b: Treasury.objects.create(tenant=self.tenant, name='T', branch=b, current_balance=50),
            lambda b: make_customer(self.tenant, branch=b),
        ):
            b = Branch.objects.create(tenant=self.tenant, name='مرتبط')
            obj = make(b)
            r = self._delete(b)
            self.assertEqual(r.status_code, 400)
            self.assertTrue(Branch.objects.filter(pk=b.pk).exists())
            obj.refresh_from_db()
            self.assertEqual(obj.branch_id, b.pk)

    def test_branch_with_invoice_gives_clear_error_not_500(self):
        from apps.sales.models import SaleInvoice
        b = Branch.objects.create(tenant=self.tenant, name='بفاتورة')
        s = make_stock(self.tenant, branch=b)
        SaleInvoice.objects.create(tenant=self.tenant, stock=s, branch=b, invoice_date=timezone.localdate())
        s.branch = None
        s.save()
        r = self._delete(b)
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Branch.objects.filter(pk=b.pk).exists())

    def test_reactivating_over_cap_is_rejected(self):
        Branch.objects.create(tenant=self.tenant, name='x')
        Branch.objects.create(tenant=self.tenant, name='y')
        z = Branch.objects.create(tenant=self.tenant, name='z', is_active=False)
        r = self.client.post(reverse('core:branch_update_api', args=[z.pk]), {'name': 'z', 'is_active': 'on'})
        self.assertEqual(r.status_code, 400)
        z.refresh_from_db()
        self.assertFalse(z.is_active)

    def test_reactivating_within_cap_and_editing_active_branch_work(self):
        x = Branch.objects.create(tenant=self.tenant, name='x')
        z = Branch.objects.create(tenant=self.tenant, name='z', is_active=False)
        url = reverse('core:branch_update_api', args=[z.pk])
        self.assertEqual(self.client.post(url, {'name': 'z', 'is_active': 'on'}).status_code, 200)
        Branch.objects.create(tenant=self.tenant, name='w', is_active=False)  # الحد 2 مكتمل الآن
        r = self.client.post(reverse('core:branch_update_api', args=[x.pk]), {'name': 'x2', 'is_active': 'on'})
        self.assertEqual(r.status_code, 200)
