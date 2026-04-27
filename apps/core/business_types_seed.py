import json
from pathlib import Path

from apps.core.models import BusinessType


DATA_FILE_PATH = Path(__file__).resolve().parent / 'data' / 'business_types.json'


def load_business_types_data():
    if not DATA_FILE_PATH.exists():
        raise FileNotFoundError(f'Business types file not found: {DATA_FILE_PATH}')

    with DATA_FILE_PATH.open('r', encoding='utf-8') as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError('business_types.json must contain a JSON array')

    return data


def sync_business_types(verbose=False, stdout=None, style=None):
    data = load_business_types_data()

    created_count = 0
    updated_count = 0
    deactivated_count = 0
    seed_slugs = {item['slug'] for item in data}

    for item in data:
        slug = item['slug']
        defaults = {
            'name': item['name'],
            'name_ar': item['name_ar'],
            'icon': item.get('icon', 'fa-store'),
            'description': item.get('description', ''),
            'features': item.get('features', {}),
            'is_active': item.get('is_active', True),
            'display_order': item.get('display_order', 0),
        }

        business_type, created = BusinessType.objects.get_or_create(slug=slug, defaults=defaults)

        if created:
            created_count += 1
            if verbose and stdout and style:
                stdout.write(style.SUCCESS(f'✓ تم إنشاء: {business_type.name_ar}'))
            continue

        changed_fields = []
        for field_name, field_value in defaults.items():
            if getattr(business_type, field_name) != field_value:
                setattr(business_type, field_name, field_value)
                changed_fields.append(field_name)

        if changed_fields:
            business_type.save(update_fields=changed_fields)
            updated_count += 1
            if verbose and stdout and style:
                stdout.write(style.WARNING(f'↻ تم تحديث: {business_type.name_ar}'))
        elif verbose and stdout and style:
            stdout.write(f'- موجود بدون تغيير: {business_type.name_ar}')

    stale_queryset = BusinessType.objects.exclude(slug__in=seed_slugs).filter(is_active=True)
    stale_count = stale_queryset.count()
    if stale_count:
        stale_queryset.update(is_active=False)
        deactivated_count = stale_count
        if verbose and stdout and style:
            stdout.write(style.WARNING(f'⇣ تم تعطيل {deactivated_count} نوع خارج ملف البذور'))

    return {
        'total_in_file': len(data),
        'created': created_count,
        'updated': updated_count,
        'deactivated': deactivated_count,
    }
