from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0004_rename_purchase_in_tenant__f25795_idx_purchase_in_tenant__d1f248_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='purchaseinvoiceline',
            name='batch_number',
            field=models.CharField(blank=True, max_length=100, verbose_name='رقم الدفعة'),
        ),
        migrations.AddField(
            model_name='purchaseinvoiceline',
            name='serial_number',
            field=models.CharField(blank=True, max_length=100, verbose_name='الرقم التسلسلي'),
        ),
        migrations.AddField(
            model_name='purchaseinvoiceline',
            name='expiry_date',
            field=models.DateField(blank=True, null=True, verbose_name='تاريخ انتهاء الصلاحية'),
        ),
    ]
