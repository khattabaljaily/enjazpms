"""
Initial Data for Business Types
"""
from django.core.management.base import BaseCommand
from apps.core.models import BusinessType


class Command(BaseCommand):
    help = 'إنشاء أنواع الأنشطة التجارية الأساسية'
    
    def handle(self, *args, **options):
        business_types = [
            {
                'name': 'Pharmacy',
                'name_ar': 'صيدلية',
                'slug': 'pharmacy',
                'icon': 'fa-pills',
                'description': 'صيدلية لبيع الأدوية والمستلزمات الطبية',
                'display_order': 1,
                'features': {
                    'prescription_required': True,
                    'expiry_tracking': True,
                    'batch_numbers': True,
                }
            },
            {
                'name': 'Supermarket',
                'name_ar': 'سوبر ماركت',
                'slug': 'supermarket',
                'icon': 'fa-shopping-cart',
                'description': 'سوبر ماركت لبيع المواد الغذائية والاحتياجات اليومية',
                'display_order': 2,
                'features': {
                    'barcode_scanning': True,
                    'multi_unit': True,
                    'promotions': True,
                }
            },
            {
                'name': 'Restaurant',
                'name_ar': 'مطعم',
                'slug': 'restaurant',
                'icon': 'fa-utensils',
                'description': 'مطعم أو كافيه لتقديم الطعام والمشروبات',
                'display_order': 3,
                'features': {
                    'table_management': True,
                    'kitchen_orders': True,
                    'recipes': True,
                }
            },
            {
                'name': 'Retail Store',
                'name_ar': 'محل تجاري',
                'slug': 'retail-store',
                'icon': 'fa-store',
                'description': 'محل تجاري عام لبيع المنتجات المختلفة',
                'display_order': 4,
                'features': {
                    'basic_pos': True,
                    'inventory': True,
                }
            },
            {
                'name': 'Clothing Store',
                'name_ar': 'محل ملابس',
                'slug': 'clothing-store',
                'icon': 'fa-tshirt',
                'description': 'محل لبيع الملابس والإكسسوارات',
                'display_order': 5,
                'features': {
                    'sizes_colors': True,
                    'seasons': True,
                }
            },
            {
                'name': 'Electronics Store',
                'name_ar': 'محل إلكترونيات',
                'slug': 'electronics-store',
                'icon': 'fa-laptop',
                'description': 'محل لبيع الأجهزة الإلكترونية والكهربائية',
                'display_order': 6,
                'features': {
                    'warranty_tracking': True,
                    'serial_numbers': True,
                }
            },
            {
                'name': 'Bakery',
                'name_ar': 'مخبز / حلويات',
                'slug': 'bakery',
                'icon': 'fa-bread-slice',
                'description': 'مخبز أو محل حلويات',
                'display_order': 7,
                'features': {
                    'production_tracking': True,
                    'ingredients': True,
                }
            },
            {
                'name': 'Hardware Store',
                'name_ar': 'محل أدوات',
                'slug': 'hardware-store',
                'icon': 'fa-tools',
                'description': 'محل لبيع الأدوات والمعدات',
                'display_order': 8,
                'features': {
                    'bulk_selling': True,
                }
            },
        ]
        
        created_count = 0
        for bt_data in business_types:
            bt, created = BusinessType.objects.get_or_create(
                slug=bt_data['slug'],
                defaults=bt_data
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'✓ تم إنشاء: {bt.name_ar}'))
            else:
                self.stdout.write(self.style.WARNING(f'- موجود بالفعل: {bt.name_ar}'))
        
        self.stdout.write(self.style.SUCCESS(f'\n✅ تم إنشاء {created_count} نوع نشاط تجاري'))
