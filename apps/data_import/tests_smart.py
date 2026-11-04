"""الاستيراد الذكي للمنتجات: ملفات من أنظمة أخرى، أخطاء بسيطة لا توقف الملف، والعملة الصعبة."""
import io
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from apps.core.test_utils import TenantTestCase
from apps.items.models import Category, Item
from apps.data_import import product_importer, smart_reader as sr


def xlsx(*sheets):
    """sheets: [(title, rows)] → bytes."""
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@override_settings(DEEPSEEK_API_KEY='')
class SmartReaderTests(TenantTestCase):
    def test_parse_decimal_is_tolerant(self):
        self.assertEqual(sr.parse_decimal('١٬٥٠٠ ج.س')[0], Decimal('1500'))
        self.assertEqual(sr.parse_decimal('1,250.50')[0], Decimal('1250.50'))
        self.assertEqual(sr.parse_decimal('1.250,50')[0], Decimal('1250.50'))
        self.assertEqual(sr.parse_decimal('$12.5')[0], Decimal('12.5'))
        self.assertEqual(sr.parse_decimal('12,5')[0], Decimal('12.5'))
        self.assertIsNotNone(sr.parse_decimal('غير متوفر')[1])

    def test_parse_date_formats(self):
        self.assertEqual(str(sr.parse_date('31/05/2027')[0]), '2027-05-31')
        self.assertEqual(str(sr.parse_date('05/2027')[0]), '2027-05-31')
        self.assertEqual(str(sr.parse_date('2027-02')[0]), '2027-02-28')

    def test_header_row_detected_after_title_lines(self):
        rows = [['تقرير الأصناف — شركة النور', None, None], [None, None, None], ['تاريخ الطباعة 2026-01-01', None, None],
                ['اسم الصنف', 'الباركود', 'سعر البيع'], ['أرز', 111, 900], ['سكر', 222, 1200]]
        from apps.data_import.schemas import get_product_schema
        aliases = sr.build_alias_index(get_product_schema(self.tenant))
        self.assertEqual(sr.detect_header_row(rows, aliases)[0], 3)


@override_settings(DEEPSEEK_API_KEY='')
class SmartProductImportTests(TenantTestCase):
    subscription_plan = 'pro'

    def setUp(self):
        super().setUp()
        self.drinks = Category.objects.create(tenant=self.tenant, name='مشروبات', is_active=True)
        self.cleaning = Category.objects.create(tenant=self.tenant, name='منظفات', is_active=True)

    def _run(self, data, filename='old_system.xlsx', options=None):
        return product_importer.import_products(self.tenant, SimpleUploadedFile(filename, data), self.user, options)

    def test_old_system_file_second_sheet_after_intro_rows(self):
        data = xlsx(
            ('Summary', [['تقرير المخزون'], ['عدد الأصناف', 2]]),
            ('Items', [['شركة النور للتجارة'], ['قائمة الأصناف'], [],
                       ['Item Name', 'Bar Code', 'Group', 'Retail Price', 'Unit Cost', 'Qty On Hand'],
                       ['عصير برتقال', 6291041500213.0, 'مشروبات', '1,500', '١٢٠٠', 10],
                       ['صابون', 6291041500220, 'منظفات', '900 ج.س', 700, 5]]),
        )
        result = self._run(data)
        self.assertEqual(result['created'], 2, result)
        juice = Item.objects.get(tenant=self.tenant, name='عصير برتقال')
        self.assertEqual(juice.barcode, '6291041500213')
        self.assertEqual(juice.selling_price, Decimal('1500'))
        self.assertEqual(juice.cost_price, Decimal('1200'))
        self.assertEqual(juice.category, self.drinks)
        soap = Item.objects.get(tenant=self.tenant, name='صابون')
        self.assertEqual(soap.selling_price, Decimal('900'))
        self.assertEqual(soap.category, self.cleaning)

    def test_forgotten_category_does_not_block_and_default_applies(self):
        data = xlsx(('المنتجات', [['اسم المنتج', 'التصنيف', 'سعر البيع'], ['شاي', 'مشروبات', 300], ['قهوة', None, 500]]))
        result = self._run(data)
        self.assertEqual(result['created'], 2, result)
        self.assertIsNone(Item.objects.get(tenant=self.tenant, name='قهوة').category)

        Item.objects.filter(tenant=self.tenant).delete()
        result = self._run(data, options={'default_category': str(self.drinks.id)})
        self.assertEqual(Item.objects.get(tenant=self.tenant, name='قهوة').category, self.drinks)

    def test_unknown_and_misspelled_categories(self):
        data = xlsx(('المنتجات', [['الاسم', 'القسم'], ['كولا', 'المشروبات'], ['كيك', 'حلويات'], ['معطر', 'منضفات']]))
        result = self._run(data)
        self.assertEqual(result['created'], 3, result)
        self.assertEqual(Item.objects.get(tenant=self.tenant, name='معطر').category, self.cleaning)
        self.assertEqual(Item.objects.get(tenant=self.tenant, name='كيك').category.name, 'حلويات')
        self.assertEqual(result['categories_created'], 1)

    def test_bad_row_reports_row_and_column_without_stopping_file(self):
        data = xlsx(('المنتجات', [['اسم المنتج', 'سعر البيع', 'الباركود'], ['منتج أ', 100, 1],
                                  [None, 200, 2], ['منتج ج', 'غير معروف', 3], ['الإجمالي', 300, None]]))
        result = self._run(data)
        self.assertEqual(result['created'], 2, result)
        self.assertEqual(len(result['errors']), 1)
        err = result['errors'][0]
        self.assertEqual((err['row'], err['column']), (3, 'A'))
        warn = next(w for w in result['warnings'] if w['row'] == 4)
        self.assertEqual((warn['column'], warn['value']), ('B', 'غير معروف'))
        self.assertFalse(Item.objects.filter(tenant=self.tenant, name='الإجمالي').exists())

    def test_ai_maps_unknown_headers(self):
        data = xlsx(('Sheet1', [['البند', 'س.ب'], ['زيت', 2500]]))

        def fake_ai(columns, schema):
            return {c['index']: {'البند': 'name', 'س.ب': 'selling_price'}[c['header']] for c in columns}

        with mock.patch.object(product_importer, 'smart_map_columns', fake_ai):
            result = self._run(data)
        self.assertEqual(result['created'], 1, result)
        self.assertEqual(Item.objects.get(tenant=self.tenant, name='زيت').selling_price, Decimal('2500'))

    def test_analyze_then_commit_with_user_choices_and_errors_file(self):
        data = xlsx(('Sheet1', [['Product', 'Something', 'Price'], ['ماء', 'x', 100], [None, 'y', 50]]))
        resp = self.client.post(reverse('data_import:product_analyze'),
                                {'file': SimpleUploadedFile('p.xlsx', data)})
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        analysis = body['analysis']
        mapped = {c['header']: c['field'] for c in analysis['columns']}
        self.assertEqual(mapped['Product'], 'name')
        self.assertEqual(mapped['Something'], '')
        self.assertEqual(analysis['summary']['ready'], 1)
        self.assertEqual(Item.objects.filter(tenant=self.tenant).count(), 0)   # المعاينة لا تحفظ شيئاً

        options = {'mapping': {'0': 'name', '1': 'description', '2': 'selling_price'},
                   'default_category': str(self.drinks.id)}
        resp = self.client.post(reverse('data_import:product_commit'),
                                {'token': body['token'], 'options': json.dumps(options)})
        result = resp.json()
        self.assertEqual(result['created'], 1, result)
        item = Item.objects.get(tenant=self.tenant, name='ماء')
        self.assertEqual((item.description, item.category), ('x', self.drinks))
        self.assertTrue(result['errors_url'])
        errors_file = self.client.get(result['errors_url'])
        self.assertEqual(errors_file.status_code, 200)

    def test_token_of_another_user_is_rejected(self):
        resp = self.client.post(reverse('data_import:product_commit'), {'token': 'a' * 32, 'options': '{}'})
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.json().get('expired'))


@override_settings(DEEPSEEK_API_KEY='')
class HardCurrencyTemplateAndAlertTests(TenantTestCase):
    subscription_plan = 'pro'

    def setUp(self):
        super().setUp()
        self.tenant.hard_currency_mode = True
        self.tenant.hard_currency = 'USD'
        self.tenant.exchange_rate = Decimal('2000')
        self.tenant.exchange_rate_updated_at = timezone.now()
        self.tenant.save()
        Category.objects.create(tenant=self.tenant, name='عام', is_active=True)

    def test_template_prices_are_in_local_currency(self):
        from openpyxl import load_workbook
        resp = self.client.get(reverse('data_import:product_template'))
        wb = load_workbook(io.BytesIO(resp.content))
        headers = [c.value for c in wb['المنتجات'][1]]
        self.assertIn('سعر البيع (ج.س)', headers)
        self.assertIn('سعر التكلفة (ج.س)', headers)
        self.assertFalse([h for h in headers if 'عملة صعبة' in (h or '')])

    def test_local_template_import_derives_hard_currency(self):
        data = xlsx(('المنتجات', [['اسم المنتج *', 'سعر التكلفة (ج.س)', 'سعر البيع (ج.س)'], ['شاشة', 300000, 400000]]))
        result = product_importer.import_products(self.tenant, SimpleUploadedFile('p.xlsx', data), self.user)
        self.assertEqual(result['created'], 1, result)
        item = Item.objects.get(tenant=self.tenant, name='شاشة')
        self.assertEqual((item.selling_price, item.selling_price_hc), (Decimal('400000'), Decimal('200')))
        self.assertEqual(item.cost_price_hc, Decimal('150'))

    def test_dollar_header_and_small_values_are_hard_currency(self):
        data = xlsx(('Sheet1', [['Item', 'Price $', 'Cost'], ['راوتر', 25, 20], ['كيبل', 3, 2], ['ماوس', 8, 5]]))
        result = product_importer.import_products(self.tenant, SimpleUploadedFile('p.xlsx', data), self.user)
        self.assertEqual(result['created'], 3, result)
        router = Item.objects.get(tenant=self.tenant, name='راوتر')
        self.assertEqual((router.selling_price_hc, router.selling_price), (Decimal('25'), Decimal('50000')))
        self.assertEqual((router.cost_price_hc, router.cost_price), (Decimal('20'), Decimal('40000')))

    def test_alert_only_when_rate_is_stale(self):
        from apps.core.market_rates import exchange_rate_alert
        self.assertIsNone(exchange_rate_alert(self.tenant))
        self.tenant.exchange_rate_updated_at = timezone.now() - timedelta(days=3)
        self.assertEqual(exchange_rate_alert(self.tenant)['reason'], 'stale')
        self.tenant.save()
        self.assertContains(self.client.get(reverse('data_import:product_page')), 'حدّث سعر الصرف قبل إدخال الأسعار')
        self.assertContains(self.client.get(reverse('items:list')), 'حدّث سعر الصرف قبل إدخال الأسعار')
        self.tenant.hard_currency_mode = False
        self.assertIsNone(exchange_rate_alert(self.tenant))

    def test_no_alert_for_normal_tenant_pages(self):
        self.tenant.hard_currency_mode = False
        self.tenant.exchange_rate_updated_at = None
        self.tenant.save()
        self.assertNotContains(self.client.get(reverse('data_import:product_page')), 'حدّث سعر الصرف قبل إدخال الأسعار')


@override_settings(DEEPSEEK_API_KEY='')
class PharmacyBatchImportTests(TenantTestCase):
    """الصيدلية: رقم التشغيلة وتاريخ الانتهاء بصيغ الأنظمة الأخرى (05/2027) يُنشئان دفعة الرصيد الافتتاحي."""
    subscription_plan = 'pro'

    def setUp(self):
        super().setUp()
        from apps.core.models import TenantCapabilities
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant, defaults={'has_expiry_dates': True, 'has_batch_numbers': True})
        self.tenant.refresh_from_db()
        Category.objects.create(tenant=self.tenant, name='مسكنات', is_active=True)

    def test_batch_and_month_year_expiry(self):
        from apps.items.models import ItemBatch
        data = xlsx(('الأدوية', [['اسم الدواء', 'التصنيف', 'الرصيد', 'Lot No', 'Exp'],
                                 ['بنادول 500', 'مسكنات', 20, 'B123', '05/2027']]))
        result = product_importer.import_products(self.tenant, SimpleUploadedFile('p.xlsx', data), self.user)
        self.assertEqual(result['created'], 1, result)
        batch = ItemBatch.objects.get(tenant=self.tenant, item__name='بنادول 500')
        self.assertEqual((batch.batch_number, str(batch.expiry_date)), ('B123', '2027-05-31'))


@override_settings(DEEPSEEK_API_KEY='')
class SameGenericDifferentProductTests(TenantTestCase):
    """نفس الاسم العلمي باسمين تجاريين مختلفين = منتجان (كان الثاني يُحدّث الأول ويُفقد)."""
    subscription_plan = 'pro'

    def setUp(self):
        super().setUp()
        from apps.core.models import TenantCapabilities
        TenantCapabilities.objects.update_or_create(tenant=self.tenant, defaults={'has_drug_classification': True})
        self.tenant.refresh_from_db()
        Category.objects.create(tenant=self.tenant, name='موضعي', is_active=True)

    def test_brands_sharing_generic_name_are_both_created(self):
        data = xlsx(('Products', [['الاسم', 'الاسم العلمي', 'سعر البيع', 'العبوة'],
                                  ['FUCIDEM OINT', 'fucidic acid', '11,000.00', 'UNIT'],
                                  ['FUCIDEM CREAM', 'fucidic acid', '9,500.00', 'قطعة']]))
        result = product_importer.import_products(self.tenant, SimpleUploadedFile('p.xlsx', data), self.user)
        self.assertEqual((result['created'], result['updated']), (2, 0), result)
        self.assertEqual(Item.objects.get(tenant=self.tenant, name='FUCIDEM CREAM').selling_price, Decimal('9500'))
        self.assertEqual(Item.objects.get(tenant=self.tenant, name='FUCIDEM OINT').selling_price, Decimal('11000'))


@override_settings(DEEPSEEK_API_KEY='')
class InFileMergeReportTests(TenantTestCase):
    """صف مكرر داخل نفس الملف يُذكر كـ«دمج» برقم الصف الأول، لا كتحديث لمنتج موجود مسبقاً."""

    def setUp(self):
        super().setUp()
        Category.objects.create(tenant=self.tenant, name='عام', is_active=True)

    def test_repeated_row_is_reported_as_merged(self):
        data = xlsx(('S', [['الاسم', 'سعر البيع'], ['منتج واحد', 100], ['منتج واحد', 120]]))
        result = product_importer.import_products(self.tenant, SimpleUploadedFile('p.xlsx', data), self.user)
        self.assertEqual((result['created'], result['updated'], result['merged']), (1, 0, 1), result)
        self.assertTrue(any('الصف 2' in w['message'] for w in result['warnings'] if w['row'] == 3))


@override_settings(DEEPSEEK_API_KEY='')
class OldXlsFormatTests(TenantTestCase):
    """ملفات Excel القديمة (.xls) من الأنظمة السابقة تُقرأ مباشرة دون تحويلها إلى xlsx."""

    def setUp(self):
        super().setUp()
        Category.objects.create(tenant=self.tenant, name='عام', is_active=True)

    def test_xls_file_from_old_system(self):
        from pathlib import Path
        data = (Path(__file__).parent / 'test_files' / 'old_system.xls').read_bytes()
        result = product_importer.import_products(self.tenant, SimpleUploadedFile('old_system.xls', data), self.user)
        self.assertEqual(result['created'], 2, result)
        rice = Item.objects.get(tenant=self.tenant, name='أرز بسمتي')
        self.assertEqual((rice.barcode, rice.selling_price), ('6291041500213', Decimal('2500')))
        self.assertEqual(Item.objects.get(tenant=self.tenant, name='زيت طعام').selling_price, Decimal('3750'))
