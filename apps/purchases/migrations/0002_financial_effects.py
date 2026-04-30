from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseinvoice',
            name='bank_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='المبلغ بنكياً (مختلط)'),
        ),
        migrations.AddField(
            model_name='purchaseinvoice',
            name='bank_reference',
            field=models.CharField(blank=True, max_length=100, verbose_name='مرجع التحويل البنكي'),
        ),
        migrations.AddField(
            model_name='purchaseinvoice',
            name='cancellation_reason',
            field=models.TextField(blank=True, verbose_name='سبب الإلغاء'),
        ),
        migrations.AddField(
            model_name='purchaseinvoice',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='وقت الإلغاء'),
        ),
        migrations.AddField(
            model_name='purchaseinvoice',
            name='cancelled_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cancelled_purchase_invoices', to=settings.AUTH_USER_MODEL, verbose_name='أُلغي بواسطة'),
        ),
        migrations.AddField(
            model_name='purchaseinvoice',
            name='cash_amount',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='المبلغ نقداً (مختلط)'),
        ),
        migrations.AddField(
            model_name='purchaseinvoice',
            name='confirmed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='confirmed_purchase_invoices', to=settings.AUTH_USER_MODEL, verbose_name='أُكد بواسطة'),
        ),
        migrations.CreateModel(
            name='PurchasePayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('payment_method', models.CharField(choices=[('cash', 'نقداً'), ('bank', 'تحويل بنكي')], max_length=10, verbose_name='طريقة الدفع')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='المبلغ')),
                ('payment_date', models.DateField(verbose_name='تاريخ الدفع')),
                ('reference_number', models.CharField(blank=True, max_length=100, verbose_name='رقم مرجعي')),
                ('notes', models.TextField(blank=True, verbose_name='ملاحظات')),
                ('is_reversed', models.BooleanField(default=False, verbose_name='تم عكسها')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchasepayment_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='payments', to='purchases.purchaseinvoice', verbose_name='أمر الشراء')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant', verbose_name='العميل')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='purchasepayment_updated', to=settings.AUTH_USER_MODEL, verbose_name='آخر تعديل بواسطة')),
            ],
            options={
                'verbose_name': 'دفعة شراء',
                'verbose_name_plural': 'دفعات الشراء',
                'db_table': 'purchase_payments',
                'ordering': ['-payment_date', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='SupplierLedger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='تاريخ التحديث')),
                ('entry_type', models.CharField(choices=[('opening', 'رصيد افتتاحي'), ('invoice', 'أمر شراء آجل'), ('payment', 'سداد للمورد'), ('return', 'مرتجع شراء'), ('adjustment', 'تعديل يدوي')], max_length=15, verbose_name='نوع القيد')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14, verbose_name='المبلغ')),
                ('entry_date', models.DateField(verbose_name='تاريخ القيد')),
                ('notes', models.TextField(blank=True, verbose_name='ملاحظات')),
                ('reference_type', models.CharField(blank=True, max_length=50, verbose_name='نوع المرجع')),
                ('reference_id', models.PositiveBigIntegerField(blank=True, null=True, verbose_name='رقم المرجع')),
                ('running_balance', models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name='الرصيد التراكمي')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='supplierledger_created', to=settings.AUTH_USER_MODEL, verbose_name='أنشئ بواسطة')),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='ledger_entries', to='suppliers.supplier', verbose_name='المورد')),
                ('tenant', models.ForeignKey(db_index=True, on_delete=django.db.models.deletion.CASCADE, to='core.tenant', verbose_name='العميل')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='supplierledger_updated', to=settings.AUTH_USER_MODEL, verbose_name='آخر تعديل بواسطة')),
            ],
            options={
                'verbose_name': 'قيد حساب مورد',
                'verbose_name_plural': 'سجل حسابات الموردين',
                'db_table': 'supplier_ledger',
                'ordering': ['-entry_date', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='purchasepayment',
            index=models.Index(fields=['tenant', 'invoice', '-payment_date'], name='purchase_pa_tenant__fcd06f_idx'),
        ),
        migrations.AddIndex(
            model_name='supplierledger',
            index=models.Index(fields=['tenant', 'supplier', '-entry_date'], name='supplier_le_tenant__684fab_idx'),
        ),
        migrations.AddIndex(
            model_name='supplierledger',
            index=models.Index(fields=['reference_type', 'reference_id'], name='supplier_le_referen_42d5aa_idx'),
        ),
    ]
