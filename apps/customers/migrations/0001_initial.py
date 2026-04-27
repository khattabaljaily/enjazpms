from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Customer',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('code', models.CharField(blank=True, max_length=20, verbose_name='كود العميل')),
                ('name', models.CharField(max_length=200, verbose_name='اسم العميل')),
                ('phone', models.CharField(blank=True, max_length=20, verbose_name='رقم الهاتف')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='البريد الإلكتروني')),
                ('city', models.CharField(blank=True, max_length=100, verbose_name='المدينة')),
                ('address', models.TextField(blank=True, verbose_name='العنوان')),
                ('notes', models.TextField(blank=True, verbose_name='ملاحظات')),
                ('opening_balance', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='الرصيد الافتتاحي')),
                ('credit_limit', models.DecimalField(decimal_places=2, default=0, max_digits=12, verbose_name='الحد الائتماني')),
                ('is_active', models.BooleanField(default=True, verbose_name='نشط')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant', verbose_name='العميل')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='customer_updated', to=settings.AUTH_USER_MODEL, verbose_name='عُدل بواسطة')),
            ],
            options={
                'verbose_name': 'عميل',
                'verbose_name_plural': 'العملاء',
                'db_table': 'customers',
                'ordering': ['-created_at'],
                'unique_together': {('tenant', 'code')},
            },
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['tenant', 'name'], name='customers_cu_tenant__0ea6c0_idx'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['tenant', 'phone'], name='customers_cu_tenant__faaf04_idx'),
        ),
        migrations.AddIndex(
            model_name='customer',
            index=models.Index(fields=['tenant', 'is_active'], name='customers_cu_tenant__f9fef1_idx'),
        ),
    ]
