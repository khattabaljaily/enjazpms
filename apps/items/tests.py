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


class HardCurrencyReimportTests(TenantTestCase):
    """إعادة استيراد منتج موجود في وضع العملة الصعبة تأخذ أسعار الملف لا الأسعار القديمة."""

    def test_reimport_updates_hc_prices_and_derives_local(self):
        import io
        from decimal import Decimal
        from django.core.files.uploadedfile import SimpleUploadedFile
        from openpyxl import Workbook
        from apps.data_import.product_importer import import_products
        from apps.items.models import Category, Item

        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.exchange_rate = Decimal('500')
        self.tenant.save()
        cat = Category.objects.create(tenant=self.tenant, name='عام')
        item = Item.objects.create(tenant=self.tenant, name='شاشة 24 بوصة', category=cat,
                                   selling_price_hc=Decimal('40'), selling_price=Decimal('20000'))

        wb = Workbook(); ws = wb.active
        ws.append(['اسم المنتج', 'التصنيف', 'سعر البيع (عملة صعبة)'])
        ws.append(['شاشة 24 بوصة', 'عام', 44])
        buf = io.BytesIO(); wb.save(buf)
        upload = SimpleUploadedFile('p.xlsx', buf.getvalue())
        with self.settings(DEEPSEEK_API_KEY=''):
            result = import_products(self.tenant, upload, self.user)

        self.assertEqual(result['updated'], 1, result)
        item.refresh_from_db()
        self.assertEqual(item.selling_price_hc, Decimal('44'))
        self.assertEqual(item.selling_price, Decimal('22000'))
