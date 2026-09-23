"""
generate_permission_reference — يولّد 3 ملفات JSON للقراءة فقط، ملف واحد لكل
نسخة (version_type)، تعرض بالضبط الصلاحيات التي يمكن أن يراها مشترك بهذه
النسخة عند أقصى قدرات ممكنة (كل الباقات/القدرات الاختيارية مفعّلة).

هذه الملفات "مرجع" وليست مصدراً: لا شيء في التطبيق يقرأها وقت التشغيل —
الغرض الوحيد منها مراجعة/تصفّح الفروقات بين النسخ الثلاث بسهولة، بدل قراءة
apps/accounts/permissions_schema.json الموسوم بالكامل. تُولَّد دائماً من ذلك
الملف الواحد (لا تُعدَّل يدوياً أبداً) — استخدم --check للتأكد أنها ليست
قديمة (انظر apps/accounts/tests_permission_schema.py).

الاستخدام:
    python manage.py generate_permission_reference            # يكتب الملفات
    python manage.py generate_permission_reference --check    # لا يكتب، فقط يتحقق (exit 1 لو قديمة)
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.permissions import _apply_tenant_filter, _load_structured_schema


VERSION_TYPES = ['single_store', 'multi_stock', 'multi_branch']
OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / 'permissions_reference'


def build_reference_data(version_type):
    """يرجع محتوى ملف مرجع نسخة واحدة، بأقصى قدرات ممكنة (كل الأعلام True)."""
    sections = _apply_tenant_filter(
        _load_structured_schema(),
        version_type=version_type,
        has_capability=lambda name: True,
        plan_allows=lambda name: True,
        get_flag=lambda name: True,
    )
    return {
        'version_type': version_type,
        'schema_version': 1,
        'sections': [
            {
                'key': section['key'],
                'name': section['name'],
                'role_scope': section.get('role_scope'),
                'permissions': [
                    {'key': perm['key'], 'label': perm['label']}
                    for perm in section['permissions']
                ],
            }
            for section in sections
        ],
    }


def render_reference_json(version_type):
    return json.dumps(build_reference_data(version_type), ensure_ascii=False, indent=2) + '\n'


class Command(BaseCommand):
    help = 'يولّد 3 ملفات JSON مرجعية (single_store/multi_stock/multi_branch) من permissions_schema.json'

    def add_arguments(self, parser):
        parser.add_argument(
            '--check', action='store_true',
            help='لا يكتب الملفات، فقط يتحقق أنها مطابقة للمصدر الحالي (exit code 1 لو قديمة)',
        )

    def handle(self, *args, **options):
        check_only = options['check']
        stale = []

        for version_type in VERSION_TYPES:
            content = render_reference_json(version_type)
            file_path = OUTPUT_DIR / f'{version_type}.json'

            if check_only:
                existing = file_path.read_text(encoding='utf-8') if file_path.exists() else None
                if existing != content:
                    stale.append(version_type)
                continue

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding='utf-8')
            self.stdout.write(self.style.SUCCESS(f'تم تحديث {file_path}'))

        if check_only:
            if stale:
                raise CommandError(
                    'ملفات مرجع الصلاحيات التالية قديمة: ' + ', '.join(stale) +
                    ' — شغّل: python manage.py generate_permission_reference'
                )
            self.stdout.write(self.style.SUCCESS('كل ملفات مرجع الصلاحيات مطابقة للمصدر الحالي.'))
