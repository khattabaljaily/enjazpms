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
