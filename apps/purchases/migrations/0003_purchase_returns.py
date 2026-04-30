from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0002_financial_effects'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='purchaseinvoice',
            name='status',
            field=models.CharField(choices=[('draft', 'مسودة'), ('confirmed', 'مؤكدة'), ('partially_returned', 'مرتجعة جزئياً'), ('returned', 'مرتجعة كلياً'), ('cancelled', 'ملغاة')], default='draft', max_length=20, verbose_name='الحالة'),
        ),
        migrations.AddField(
            model_name='purchaseinvoiceline',
            name='returned_quantity',
            field=models.DecimalField(decimal_places=3, default=0, max_digits=12, verbose_name='الكمية المُرتجعة'),
        ),
        migrations.CreateModel(
            name='PurchaseReturn',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('return_number', models.CharField(blank=True, max_length=20, verbose_name='رقم المرتجع')),
                ('return_date', models.DateField(verbose_name='تاريخ المرتجع')),
                ('status', models.CharField(choices=[('draft', 'مسودة'), ('confirmed', 'مؤكد'), ('cancelled', 'ملغي')], default='draft', max_length=20, verbose_name='الحالة')),
                ('refund_method', models.CharField(choices=[('cash', 'نقدي'), ('bank', 'بنكي'), ('balance', 'رصيد مورد')], default='balance', max_length=10, verbose_name='طريقة التسوية')),
                ('total_returned', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='إجمالي المرتجع')),
                ('reason', models.TextField(blank=True, verbose_name='السبب')),
                ('notes', models.TextField(blank=True, verbose_name='ملاحظات')),
                ('confirmed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='confirmed_purchase_returns', to=settings.AUTH_USER_MODEL, verbose_name='أُكد بواسطة')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchasereturn_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('original_invoice', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_returns', to='purchases.purchaseinvoice', verbose_name='أمر الشراء الأصلي')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant', verbose_name='العميل')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchasereturn_updated', to=settings.AUTH_USER_MODEL, verbose_name='آخر تعديل بواسطة')),
            ],
            options={
                'verbose_name': 'مرتجع شراء',
                'verbose_name_plural': 'مرتجعات الشراء',
                'db_table': 'purchase_returns',
                'ordering': ['-return_date', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PurchaseReturnLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('returned_quantity', models.DecimalField(decimal_places=3, max_digits=12, verbose_name='الكمية المرتجعة')),
                ('unit_cost', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='سعر الوحدة')),
                ('line_total', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='إجمالي السطر')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchasereturnline_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('invoice_line', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='return_lines', to='purchases.purchaseinvoiceline', verbose_name='بند أمر الشراء')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_return_lines', to='items.item', verbose_name='المنتج')),
                ('purchase_return', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='purchases.purchasereturn', verbose_name='المرتجع')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant', verbose_name='العميل')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchasereturnline_updated', to=settings.AUTH_USER_MODEL, verbose_name='آخر تعديل بواسطة')),
            ],
            options={
                'verbose_name': 'بند مرتجع شراء',
                'verbose_name_plural': 'بنود مرتجعات الشراء',
                'db_table': 'purchase_return_lines',
                'ordering': ['id'],
            },
        ),
        migrations.AddIndex(
            model_name='purchasereturn',
            index=models.Index(fields=['tenant', 'status'], name='purchase_re_tenant__f59f99_idx'),
        ),
        migrations.AddIndex(
            model_name='purchasereturn',
            index=models.Index(fields=['tenant', 'return_date'], name='purchase_re_tenant__8e35cd_idx'),
        ),
        migrations.AddIndex(
            model_name='purchasereturn',
            index=models.Index(fields=['tenant', 'return_number'], name='purchase_re_tenant__791a93_idx'),
        ),
    ]
