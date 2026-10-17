from django.db import migrations, models


def mark_existing_branch_main(apps, schema_editor):
    """أقدم خزينة محلية لكل فرع (تلك التي أُنشئت تلقائيًا مع الفرع) تصبح «الرئيسية»."""
    Treasury = apps.get_model('treasury', 'Treasury')
    seen = set()
    for t in (Treasury.objects.filter(branch__isnull=False, is_hard_currency=False, is_head_office=False)
              .order_by('branch_id', 'id')):
        if t.branch_id in seen:
            continue
        seen.add(t.branch_id)
        Treasury.objects.filter(pk=t.pk).update(is_branch_main=True)


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0008_treasury_is_head_office_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='treasury',
            name='is_branch_main',
            field=models.BooleanField(default=False, verbose_name='خزينة الفرع الرئيسية'),
        ),
        migrations.RunPython(mark_existing_branch_main, migrations.RunPython.noop),
    ]
