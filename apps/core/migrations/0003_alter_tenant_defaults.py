from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_alter_tenant_country'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tenant',
            name='country',
            field=models.CharField(blank=True, default='السودان', max_length=100, verbose_name='البلد'),
        ),
        migrations.AlterField(
            model_name='tenant',
            name='timezone',
            field=models.CharField(default='Africa/Khartoum', max_length=50, verbose_name='المنطقة الزمنية'),
        ),
    ]
