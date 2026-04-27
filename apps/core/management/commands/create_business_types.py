"""
Initial Data for Business Types
"""
from django.core.management.base import BaseCommand
from apps.core.business_types_seed import sync_business_types


class Command(BaseCommand):
    help = 'مزامنة أنواع الأنشطة التجارية من ملف JSON'
    
    def handle(self, *args, **options):
        result = sync_business_types(verbose=True, stdout=self.stdout, style=self.style)

        self.stdout.write(
            self.style.SUCCESS(
                f"\n✅ إجمالي الأنواع في الملف: {result['total_in_file']} | "
                f"تم الإنشاء: {result['created']} | "
                f"تم التحديث: {result['updated']} | "
                f"تم التعطيل: {result['deactivated']}"
            )
        )
