"""
Backfill Capabilities
======================
يُعيد ضبط قدرات الأنشطة الحالية بعد إضافة أعلام جديدة (خصائص الصيدليات)
لأن تعديل business_types.json يؤثر فقط على tenants الجدد — الموجودين
يحتفظون بقيم TenantCapabilities كما كانت وقت إنشائهم.
"""
from django.core.management.base import BaseCommand

from apps.core.models import Tenant, TenantCapabilities


class Command(BaseCommand):
    help = 'يُعيد ضبط أعلام خصائص الصيدليات في TenantCapabilities لكل الـ tenants الحاليين حسب نوع نشاطهم'

    def handle(self, *args, **options):
        pharmacy_tenants = Tenant.objects.filter(business_type__slug='pharmacy')
        pharmacy_count = TenantCapabilities.objects.filter(tenant__in=pharmacy_tenants).update(
            has_drug_classification=True,
            has_item_alternatives=True,
            has_expiry_alerts=True,
            has_branch_stock_lookup=True,
            has_insurance_billing=True,
        )

        distributor_tenants = Tenant.objects.filter(business_type__slug='medical-distributor')
        distributor_count = TenantCapabilities.objects.filter(tenant__in=distributor_tenants).update(
            has_drug_classification=True,
            has_expiry_alerts=True,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"✅ تم تحديث {pharmacy_count} صيدلية و {distributor_count} شركة توزيع أدوية."
            )
        )
