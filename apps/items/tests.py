from apps.core.test_utils import TenantTestCase

class AutoSkuTests(TenantTestCase):
    """رمز SKU التلقائي لا يتصادم مع رموز موجودة مهما كان ترتيب الإنشاء."""

    def test_auto_sku_skips_codes_already_taken(self):
        from apps.items.models import Item
        Item.objects.create(tenant=self.tenant, name='أ', sku='ITM-00006')
        Item.objects.create(tenant=self.tenant, name='ب', sku='PHN-001')
        new = Item.objects.create(tenant=self.tenant, name='ج')
        self.assertEqual(new.sku, 'ITM-00007')
        another = Item.objects.create(tenant=self.tenant, name='د')
        self.assertEqual(another.sku, 'ITM-00008')
