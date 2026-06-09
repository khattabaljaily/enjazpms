from django.db import migrations
import uuid
from django.utils.text import slugify


def fix_empty_slugs(apps, schema_editor):
    Tenant = apps.get_model('core', 'Tenant')
    for tenant in Tenant.objects.filter(slug=''):
        base_slug = slugify(tenant.name, allow_unicode=True) or str(uuid.uuid4())[:8]
        slug = base_slug
        counter = 1
        while Tenant.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
        tenant.slug = slug
        tenant.save(update_fields=['slug'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_admin_notification'),
    ]

    operations = [
        migrations.RunPython(fix_empty_slugs, migrations.RunPython.noop),
    ]
