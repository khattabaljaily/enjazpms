"""لوحة الفرع: اسم الفرع بجانب اسم المستخدم، وعدّاد المنتجات لا يُصفَّر لفرع بلا أرصدة."""
from django.urls import reverse

from apps.accounts.models import User
from apps.core.models import Branch
from apps.core.test_utils import TenantTestCase, make_item, make_stock


class BranchDashboardTests(TenantTestCase):
    version_type = 'multi_branch'
    subscription_plan = 'enterprise'

    def setUp(self):
        super().setUp()
        self.branch = Branch.objects.create(tenant=self.tenant, name='فرع الشمال-٧٧')
        make_stock(self.tenant, name='مخزن الفرع', branch=self.branch)
        make_item(self.tenant, name='صنف ١')
        make_item(self.tenant, name='صنف ٢')
        self.sup = User.objects.create_user(username='bd-sup', password='x12345678', tenant=self.tenant,
                                            branch=self.branch, is_branch_supervisor=True,
                                            first_name='سعد', last_name='أحمد')

    def test_branch_name_shown_next_to_username(self):
        self.client.force_login(self.sup)
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertIn('· فرع الشمال-٧٧', html)

    def test_owner_has_no_branch_suffix(self):
        html = self.client.get(reverse('core:dashboard')).content.decode()
        self.assertNotIn('· فرع الشمال-٧٧', html)

    def test_product_count_counts_catalog_even_without_stock_quantities(self):
        self.client.force_login(self.sup)
        resp = self.client.get(reverse('core:dashboard'))
        self.assertEqual(resp.context['stats']['total_products'], 2)
