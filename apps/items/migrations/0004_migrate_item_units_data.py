from django.db import migrations


def migrate_units_forward(apps, schema_editor):
    Item     = apps.get_model('items', 'Item')
    ItemUnit = apps.get_model('items', 'ItemUnit')

    for item in Item.objects.select_related('unit', 'purchase_unit').all():
        u  = item.unit
        pu = item.purchase_unit

        if not u:
            continue

        # Base unit (smallest) — always factor=1
        base, _ = ItemUnit.objects.get_or_create(
            item=item, name=u.name,
            defaults={'tenant': item.tenant, 'factor': 1}
        )

        # If purchase unit is different from sale unit, add it too
        if pu and pu.pk != u.pk:
            factor = pu.conversion_factor if pu.conversion_factor else 1
            ItemUnit.objects.get_or_create(
                item=item, name=pu.name,
                defaults={'tenant': item.tenant, 'factor': factor}
            )
            item.has_multiple_units = True
            item.save(update_fields=['has_multiple_units'])


def migrate_units_backward(apps, schema_editor):
    ItemUnit = apps.get_model('items', 'ItemUnit')
    ItemUnit.objects.all().delete()
    apps.get_model('items', 'Item').objects.update(has_multiple_units=False)


class Migration(migrations.Migration):

    dependencies = [
        ('items', '0003_add_item_unit_model'),
    ]

    operations = [
        migrations.RunPython(migrate_units_forward, migrate_units_backward),
    ]
