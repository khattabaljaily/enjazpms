"""
backfill_head_office_treasuries — يضمن وجود خزينة الإدارة المركزية (محلية +
عملة صعبة إن كانت مفعّلة) لكل مشترك Enterprise موجود بالفعل قبل إضافة هذه
الميزة. المشتركون الجدد يحصلون عليها تلقائياً عبر create_tenant_defaults؛
هذا الأمر لمرة واحدة فقط لتغطية المشتركين الحاليين.

الاستخدام:
    python manage.py backfill_head_office_treasuries
"""
from django.core.management.base import BaseCommand

from apps.core.models import Tenant
from apps.core.signals import _ensure_head_office_treasuries


class Command(BaseCommand):
    help = 'يضمن وجود خزينة الإدارة المركزية لكل مشترك Enterprise حالي'

    def handle(self, *args, **options):
        tenants = Tenant.objects.filter(version_type='multi_branch')
        count = 0
        for tenant in tenants:
            _ensure_head_office_treasuries(tenant)
            count += 1
            self.stdout.write(f'  {tenant.id} — {tenant.name}')
        self.stdout.write(self.style.SUCCESS(f'تم فحص/إنشاء خزائن الإدارة المركزية لـ {count} مشترك.'))
