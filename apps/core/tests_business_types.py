"""
قاعدة الاختبار تُبنى بـ migrate، فوجود أنواع النشاط هنا يثبت أن مستقبِل
post_migrate (apps/core/apps.py) يعمل فعلاً — كان مربوطاً بمرجع ضعيف فيُجمع
أحياناً قبل التشغيل، فتبقى قاعدة جديدة بلا أي نوع نشاط ويفشل التسجيل.
"""
from django.test import TestCase

from apps.core.business_types_seed import load_business_types_data
from apps.core.models import BusinessType


class BusinessTypeSeedTests(TestCase):
    def test_business_types_exist_after_migrate(self):
        seed_slugs = {item['slug'] for item in load_business_types_data()}
        self.assertTrue(seed_slugs)
        present = set(BusinessType.objects.filter(slug__in=seed_slugs).values_list('slug', flat=True))
        self.assertEqual(present, seed_slugs)


class CsrfFailurePageTests(TestCase):
    """فشل التحقق من CSRF يعرض صفحة 403 مفهومة لا خطأ خادم 500."""

    def test_csrf_failure_renders_403(self):
        from django.test import Client
        resp = Client(enforce_csrf_checks=True).post('/accounts/login/', {'username': 'x', 'password': 'y'})
        self.assertEqual(resp.status_code, 403)
        self.assertIn('انتهت صلاحية الصفحة', resp.content.decode())


class PermissionGroupDuplicateNameTests(TestCase):
    """تكرار اسم مجموعة الصلاحيات يعطي رسالة واضحة لا خطأ خادم 500."""

    def test_duplicate_group_name_returns_message(self):
        from apps.accounts.models import PermissionGroup
        from django.conf import settings
        from django.utils import timezone
        from apps.accounts.models import User
        from apps.core.models import Tenant
        bt = BusinessType.objects.create(name='bt-dup', name_ar='نوع', slug='bt-dup')
        tenant = Tenant.objects.create(name='Dup', business_type=bt, currency='SDG',
                                       terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        admin = User.objects.create_user(username='dup-admin', password='s3cret123', tenant=tenant, is_tenant_admin=True)
        PermissionGroup.objects.create(tenant=tenant, name='كاشير')
        self.client.force_login(admin)
        resp = self.client.post('/accounts/groups/api/create/', {'name': 'كاشير'})
        self.assertNotEqual(resp.status_code, 500)
        self.assertFalse(resp.json()['success'])
        self.assertEqual(PermissionGroup.objects.filter(tenant=tenant, name='كاشير').count(), 1)
