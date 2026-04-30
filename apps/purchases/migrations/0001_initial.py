from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('items', '0001_initial'),
        ('stocks', '0002_stockquantity'),
        ('suppliers', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PurchaseInvoice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('invoice_number', models.CharField(blank=True, max_length=20, verbose_name='رقم أمر الشراء')),
                ('invoice_date', models.DateField(verbose_name='تاريخ الأمر')),
                ('status', models.CharField(choices=[('draft', 'مسودة'), ('confirmed', 'مؤكدة'), ('cancelled', 'ملغاة')], default='draft', max_length=20, verbose_name='الحالة')),
                ('payment_method', models.CharField(choices=[('cash', 'نقداً'), ('bank', 'تحويل بنكي'), ('credit', 'آجل'), ('mixed', 'مختلط')], default='cash', max_length=10, verbose_name='طريقة الدفع')),
                ('subtotal', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='المجموع الفرعي')),
                ('tax_amount', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='الضريبة')),
                ('grand_total', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='الإجمالي')),
                ('notes', models.TextField(blank=True, verbose_name='ملاحظات')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchaseinvoice_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('supplier', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='purchase_invoices', to='suppliers.supplier', verbose_name='المورد')),
                ('stock', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_invoices', to='stocks.stock', verbose_name='المخزن')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant', verbose_name='العميل')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchaseinvoice_updated', to=settings.AUTH_USER_MODEL, verbose_name='آخر تعديل بواسطة')),
            ],
            options={
                'verbose_name': 'أمر شراء',
                'verbose_name_plural': 'أوامر الشراء',
                'db_table': 'purchase_invoices',
                'ordering': ['-invoice_date', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PurchaseInvoiceLine',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('quantity', models.DecimalField(decimal_places=3, max_digits=12, verbose_name='الكمية')),
                ('unit_cost', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='سعر الوحدة')),
                ('tax_rate', models.DecimalField(decimal_places=2, default=0, max_digits=5, verbose_name='نسبة الضريبة')),
                ('tax_amount', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='مبلغ الضريبة')),
                ('line_subtotal', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='المجموع قبل الضريبة')),
                ('line_total', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='المجموع النهائي')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchaseinvoiceline_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lines', to='purchases.purchaseinvoice', verbose_name='أمر الشراء')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='purchase_lines', to='items.item', verbose_name='المنتج')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant', verbose_name='العميل')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchaseinvoiceline_updated', to=settings.AUTH_USER_MODEL, verbose_name='آخر تعديل بواسطة')),
            ],
            options={
                'verbose_name': 'بند شراء',
                'verbose_name_plural': 'بنود الشراء',
                'db_table': 'purchase_invoice_lines',
                'ordering': ['id'],
            },
        ),
        migrations.AddIndex(
            model_name='purchaseinvoice',
            index=models.Index(fields=['tenant', 'status'], name='purchase_in_tenant__f25795_idx'),
        ),
        migrations.AddIndex(
            model_name='purchaseinvoice',
            index=models.Index(fields=['tenant', 'invoice_number'], name='purchase_in_tenant__6ab724_idx'),
        ),
        migrations.AddIndex(
            model_name='purchaseinvoice',
            index=models.Index(fields=['tenant', 'invoice_date'], name='purchase_in_tenant__6a6ec1_idx'),
        ),
    ]
