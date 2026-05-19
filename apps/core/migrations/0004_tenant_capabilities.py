from django.db import migrations, models
import django.db.models.deletion


def create_capabilities_for_existing_tenants(apps, schema_editor):
    Tenant = apps.get_model('core', 'Tenant')
    TenantCapabilities = apps.get_model('core', 'TenantCapabilities')

    # Old feature key aliases → new capability field names
    key_map = {
        'has_expiry_dates': 'has_expiry_dates',
        'has_batch_numbers': 'has_batch_numbers',
        'has_serial_numbers': 'has_serial_numbers',
        'has_weight_items': 'has_weight_items',
        'has_variants': 'has_variants',
        'has_services': 'has_services',
        'has_manufacturing': 'has_manufacturing',
        'has_work_orders': 'has_work_orders',
        # old names from the 10-type seed (backwards compat)
        'supports_weight_items': 'has_weight_items',
        'supports_batch_numbers': 'has_batch_numbers',
        'supports_serial_numbers': 'has_serial_numbers',
        'supports_size_color_variants': 'has_variants',
        'supports_service_items': 'has_services',
    }

    for tenant in Tenant.objects.select_related('business_type').all():
        if TenantCapabilities.objects.filter(tenant=tenant).exists():
            continue
        features = {}
        if tenant.business_type:
            features = tenant.business_type.features or {}

        caps = {
            'has_expiry_dates': False,
            'has_batch_numbers': False,
            'has_serial_numbers': False,
            'has_weight_items': False,
            'has_variants': False,
            'has_services': False,
            'has_manufacturing': False,
            'has_work_orders': False,
        }
        for src_key, dest_key in key_map.items():
            if src_key in features:
                caps[dest_key] = bool(features[src_key])

        TenantCapabilities.objects.create(tenant=tenant, **caps)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_alter_tenant_defaults'),
    ]

    operations = [
        migrations.CreateModel(
            name='TenantCapabilities',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('has_expiry_dates', models.BooleanField(default=False, verbose_name='تواريخ انتهاء الصلاحية')),
                ('has_batch_numbers', models.BooleanField(default=False, verbose_name='أرقام الدُفعات / الباتش')),
                ('has_serial_numbers', models.BooleanField(default=False, verbose_name='أرقام تسلسلية')),
                ('has_weight_items', models.BooleanField(default=False, verbose_name='منتجات بالوزن أو الحجم')),
                ('has_variants', models.BooleanField(default=False, verbose_name='متغيرات (مقاسات / ألوان)')),
                ('has_services', models.BooleanField(default=False, verbose_name='بنود الخدمة')),
                ('has_manufacturing', models.BooleanField(default=False, verbose_name='التصنيع والوصفات من مواد خام')),
                ('has_work_orders', models.BooleanField(default=False, verbose_name='أوامر العمل')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('tenant', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='capabilities',
                    to='core.tenant',
                    verbose_name='العميل',
                )),
            ],
            options={
                'verbose_name': 'قدرات النشاط',
                'verbose_name_plural': 'قدرات الأنشطة',
                'db_table': 'tenant_capabilities',
            },
        ),
        migrations.RunPython(
            create_capabilities_for_existing_tenants,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
