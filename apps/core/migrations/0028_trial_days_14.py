from django.db import migrations, models


def set_trial_14(apps, schema_editor):
    PlatformSettings = apps.get_model('core', 'PlatformSettings')
    PlatformSettings.objects.filter(default_trial_days=30).update(default_trial_days=14)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_add_branch_manager'),
    ]

    operations = [
        migrations.AlterField(
            model_name='platformsettings',
            name='default_trial_days',
            field=models.IntegerField(default=14, verbose_name='أيام التجربة المجانية'),
        ),
        migrations.RunPython(set_trial_14, migrations.RunPython.noop),
    ]
