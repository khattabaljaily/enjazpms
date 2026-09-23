from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile

from django.test import Client, TestCase
from django.utils import timezone
from django.conf import settings
from django.urls import reverse

from apps.accounts.models import PermissionGroup, User
from apps.core.backup_service import create_backup
from apps.core.backup_service import restore_backup
from apps.core.models import Branch, BusinessType, Tenant, TenantBackup, TenantCapabilities
from apps.customers.models import Customer
from apps.items.models import Category, Item
from apps.notifications.models import Notification
from apps.stocks.models import Stock
from apps.stocks.models import StockQuantity
from apps.notifications.services import generate_low_stock_notifications
from apps.data_import.schemas import get_product_schema


class SystemSurfaceTests(TestCase):
    def setUp(self):
        business_type = BusinessType.objects.create(
            name='retail', name_ar='متجر', slug='surface-test-retail'
        )
        self.tenant = Tenant.objects.create(
            name='Surface Test Tenant', slug='surface-test-tenant',
            business_type=business_type, subscription_plan='enterprise',
            version_type='multi_branch', max_stocks=20, max_branches=20,
            currency='SDG', hard_currency_mode=False,
            terms_accepted_at=timezone.now(), terms_version=settings.TERMS_VERSION,
        )
        self.user = User.objects.create_user(
            username='surface-admin', password='secret123', tenant=self.tenant,
            is_tenant_admin=True,
        )
        self.client = Client()
        self.client.force_login(self.user)
        TenantCapabilities.objects.update_or_create(
            tenant=self.tenant, defaults={'has_insurance_billing': True}
        )
        Category.objects.create(tenant=self.tenant, name='تصنيف اختبار')
        # نسخة المؤسسات (multi_branch) لم يعد لها مخزن افتراضي يُنشأ تلقائياً
        # عند التسجيل (راجع apps/core/signals.py::create_tenant_defaults) —
        # ننشئه هنا صراحة قبل المنتج حتى يُنشئ signal المنتج سجل StockQuantity
        # المقابل له.
        self.stock = Stock.objects.create(
            tenant=self.tenant, name='مخزن اختبار', code='SUR-STK', is_active=True, is_default=True,
        )
        self.item = Item.objects.create(
            tenant=self.tenant, name='منتج استيراد', sku='SURFACE-001',
            cost_price='10', selling_price='20', tax_rate='0',
        )

    def get(self, path, **kwargs):
        return self.client.get(path, HTTP_HOST='127.0.0.1', **kwargs)

    def post(self, path, data=None, **kwargs):
        return self.client.post(path, data=data or {}, HTTP_HOST='127.0.0.1', **kwargs)

    def test_main_pages_render_shared_help_and_training_panels(self):
        # ملاحظة: insurance:company_list استُبدل بـ stocks:list — مدير النشاط
        # في نسخة المؤسسات (fixture هذا الاختبار) لم يعد له وصول للتأمين، وهو
        # عمداً ضمن عمليات الفرع المستبعدة (راجع
        # ENTERPRISE_OWNER_EXCLUDED_CATEGORIES في apps/accounts/permissions.py).
        for route_name in ('core:dashboard', 'data_import:product_page', 'stocks:list'):
            response = self.get(reverse(route_name), follow=True)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'training-fab')
            self.assertContains(response, 'help-fab')

    def test_product_import_template_and_missing_file_validation(self):
        template_response = self.get(reverse('data_import:product_template'))
        self.assertEqual(template_response.status_code, 200)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            template_response.get('Content-Type', ''),
        )
        self.assertTrue(template_response.content)

        missing_file = self.post(reverse('data_import:product_commit'))
        self.assertEqual(missing_file.status_code, 400)
        self.assertJSONEqual(
            missing_file.content,
            {'success': False, 'message': 'لم يتم رفع أي ملف'},
        )

    def test_product_import_creates_one_record_from_xlsx(self):
        from openpyxl import Workbook
        from io import BytesIO

        workbook = Workbook()
        sheet = workbook.active
        schema = get_product_schema(self.tenant)
        sheet.append([field['header_ar'] for field in schema])
        values = {
            'name': 'منتج مستورد', 'sku': 'IMPORT-001',
            'cost_price': 12, 'selling_price': 25,
        }
        sheet.append([values.get(field['field'], '') for field in schema])
        payload = BytesIO()
        workbook.save(payload)
        payload.seek(0)

        upload = SimpleUploadedFile(
            'products.xlsx', payload.getvalue(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response = self.post(
            reverse('data_import:product_commit'),
            data={'file': upload},
            format='multipart',
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result['success'])
        self.assertEqual(result['created'], 1, result)
        self.assertTrue(Item.objects.filter(tenant=self.tenant, sku='IMPORT-001').exists())

    def test_notifications_list_api_read_and_tenant_isolation(self):
        own = Notification.objects.create(
            tenant=self.tenant, user=self.user, notification_type='general',
            priority='medium', title='إشعار الاختبار', message='رسالة الاختبار',
        )
        other_type = BusinessType.objects.create(
            name='other', name_ar='آخر', slug='surface-other-type'
        )
        other_tenant = Tenant.objects.create(
            name='Other Tenant', slug='surface-other-tenant', business_type=other_type,
        )
        Notification.objects.create(
            tenant=other_tenant, notification_type='general',
            priority='high', title='بيانات لا تظهر', message='خاصة بمستأجر آخر',
        )

        api = self.get(reverse('notifications:api'))
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertEqual(body['unread'], 1)
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(body['items'][0]['id'], own.id)

        mark = self.post(reverse('notifications:mark_read', args=[own.id]))
        self.assertEqual(mark.status_code, 200)
        own.refresh_from_db()
        self.assertTrue(own.is_read)

        list_response = self.get(reverse('notifications:list'))
        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, 'بيانات لا تظهر')

    def test_permission_denies_user_without_permission_but_allows_authorized_user(self):
        limited = User.objects.create_user(
            username='surface-limited', password='secret123', tenant=self.tenant,
            is_tenant_admin=False,
        )
        group = PermissionGroup.objects.create(
            tenant=self.tenant, name='إشعارات فقط', permissions={'view_notifications': True}
        )
        group.users.add(limited)

        self.client.force_login(limited)
        allowed = self.get(reverse('notifications:list'))
        self.assertEqual(allowed.status_code, 200)
        denied = self.get(reverse('data_import:product_page'))
        self.assertEqual(denied.status_code, 302)
        self.assertIn(reverse('core:no_permission'), denied['Location'])

    def test_report_page_renders_for_tenant_admin(self):
        response = self.get(reverse('reports:summary_report'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'المبيعات')

    def test_backup_creates_completed_tenant_file(self):
        with TemporaryDirectory() as directory:
            with patch('apps.core.backup_service.BACKUP_ROOT', Path(directory)):
                backup = create_backup(self.tenant, backup_type='manual')

            backup.refresh_from_db()
            self.assertEqual(backup.status, 'completed')
            self.assertGreater(backup.file_size, 0)
            self.assertTrue(Path(backup.file_path).exists())
            self.assertTrue(Path(backup.file_path).read_text(encoding='utf-8').startswith('-- ENJAZ Tenant Backup'))

    def test_backup_restore_removes_post_backup_data_and_keeps_backup_successful(self):
        with TemporaryDirectory() as directory:
            with patch('apps.core.backup_service.BACKUP_ROOT', Path(directory)):
                backup = create_backup(self.tenant, backup_type='manual')
                self.assertEqual(backup.status, 'completed')
                Item.objects.create(
                    tenant=self.tenant, name='بيانات بعد النسخة', sku='AFTER-BACKUP',
                    cost_price='1', selling_price='2', tax_rate='0',
                )
                ok, message = restore_backup(backup.id)

            self.assertTrue(ok, message)
            self.assertTrue(Item.objects.filter(tenant=self.tenant, sku='SURFACE-001').exists())
            self.assertFalse(Item.objects.filter(tenant=self.tenant, sku='AFTER-BACKUP').exists())

    def test_low_stock_notification_is_created_once(self):
        stock = Stock.objects.filter(tenant=self.tenant, is_active=True).first()
        quantity = StockQuantity.objects.get(stock=stock, item=self.item)
        quantity.min_quantity = 2
        quantity.quantity = 1
        quantity.save(update_fields=['min_quantity', 'quantity', 'updated_at'])

        self.assertEqual(generate_low_stock_notifications(self.tenant), 1)
        self.assertEqual(generate_low_stock_notifications(self.tenant), 0)
        self.assertEqual(
            Notification.objects.filter(tenant=self.tenant, notification_type='low_stock').count(),
            1,
        )

    def test_branch_crud_endpoints_create_update_and_delete_empty_branch(self):
        create_response = self.post(
            reverse('core:branch_create_api'),
            data={'name': 'فرع اختبار', 'code': 'SUR-BR-1', 'address': 'عنوان'},
        )
        self.assertEqual(create_response.status_code, 200, create_response.json())
        branch_id = create_response.json()['data']['id']

        detail = self.get(reverse('core:branch_detail_api', args=[branch_id]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['data']['name'], 'فرع اختبار')

        update = self.post(
            reverse('core:branch_update_api', args=[branch_id]),
            data={'name': 'فرع محدث', 'code': 'SUR-BR-1', 'address': 'عنوان جديد'},
        )
        self.assertEqual(update.status_code, 200)
        self.assertEqual(Branch.objects.get(pk=branch_id).name, 'فرع محدث')

        delete = self.post(reverse('core:branch_delete_api', args=[branch_id]))
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(Branch.objects.filter(pk=branch_id).exists())
