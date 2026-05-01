from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tenant',
            name='country',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='البلد'),
        ),
    ]
