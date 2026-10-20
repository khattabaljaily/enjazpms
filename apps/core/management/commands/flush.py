"""
flush الآمن: يُبقي الكتالوج المركزي وأنواع الأنشطة (business_types) والسوبر يوزر.

هذا الأمر يحلّ محل `manage.py flush` الأصلي في المشروع، فلا حاجة لتذكّر أمر آخر.
  python manage.py flush              # يمسح كل شيء عدا البيانات الأساسية
  python manage.py flush --wipe-all   # flush كامل كما في Django الأصلي

قبل المسح تُكتب نسخة JSON في backups/essentials_<الوقت>.json؛ إن فشلت الاستعادة
تُرجَع بـ: python manage.py loaddata backups/essentials_<الوقت>.json
قواعد الاختبار (test_*) تبقى على السلوك الأصلي لأن اختبارات Django تستدعي flush بين الاختبارات.
"""
import json
import os
from datetime import datetime

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import serializers
from django.core.management.base import CommandError
from django.core.management.commands.flush import Command as DjangoFlushCommand
from django.db import connections, transaction

from apps.catalog.models import MasterCategory, MasterDrug, MasterDrugAlias
from apps.core.models import BusinessType

# حقول تشير إلى بيانات ستُحذف بالـ flush، فتُفرَّغ عند الاستعادة.
_NULL_FIELDS = {
    'accounts.user': ('tenant', 'branch'),
    'catalog.masterdrug': ('created_by', 'updated_by'),
    'catalog.masterdrugalias': ('created_by', 'source_batch'),
}
# علاقات many-to-many تشير إلى بيانات قد لا تبقى بعد الـ flush.
_DROP_M2M = {'accounts.user': ('groups', 'user_permissions')}


def _querysets(db):
    User = get_user_model()
    return [
        BusinessType.objects.using(db).order_by('pk'),
        MasterCategory.objects.using(db).order_by('pk'),
        MasterDrug.objects.using(db).order_by('pk'),
        MasterDrugAlias.objects.using(db).order_by('pk'),
        User.objects.using(db).filter(is_superuser=True).order_by('pk'),
    ]


def _serialize(db):
    records = []
    for qs in _querysets(db):
        records.extend(json.loads(serializers.serialize('json', qs)))
    for rec in records:
        for name in _NULL_FIELDS.get(rec['model'], ()):
            rec['fields'][name] = None
        for name in _DROP_M2M.get(rec['model'], ()):
            rec['fields'].pop(name, None)
    return records


class Command(DjangoFlushCommand):
    help = DjangoFlushCommand.help + ' (يُبقي الكتالوج وأنواع الأنشطة والسوبر يوزر؛ استعمل --wipe-all للمسح الكامل)'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--wipe-all', action='store_true', help='امسح كل شيء بما فيه البيانات الأساسية')
        parser.add_argument('--force-keep', action='store_true', help=argparse_suppress())

    def handle(self, **options):
        db = options['database']
        is_test_db = connections[db].settings_dict['NAME'].startswith('test_')
        if options.get('wipe_all') or (is_test_db and not options.get('force_keep')):
            return super().handle(**options)

        records = _serialize(db)
        counts = {}
        for rec in records:
            counts[rec['model']] = counts.get(rec['model'], 0) + 1
        self.stdout.write('سيُبقى بعد الـ flush: ' + '، '.join(f'{m} = {n}' for m, n in sorted(counts.items())))
        if not counts.get('accounts.user'):
            self.stdout.write(self.style.WARNING('تنبيه: لا يوجد سوبر يوزر ليُحفظ'))

        if options['interactive']:
            # flush الأصلي لا يرمي خطأ عند الرفض (يطبع Cancelled ويكمل)، فنسأل نحن قبل أي شيء.
            name = connections[db].settings_dict['NAME']
            answer = input(f'سيُمسح كل شيء في قاعدة "{name}" عدا ما ذُكر أعلاه. للمتابعة اكتب yes: ')
            if answer.strip().lower() != 'yes':
                raise CommandError('أُلغي الأمر، لم يُمسح شيء.')
            options['interactive'] = False

        backup_dir = os.path.join(settings.BASE_DIR, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f'essentials_{datetime.now():%Y%m%d_%H%M%S}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(records, fh, ensure_ascii=False)

        super().handle(**options)

        connection = connections[db]
        try:
            with transaction.atomic(using=db):
                with connection.constraint_checks_disabled():
                    # الـ flush نفسه يعيد زرع بعض الجداول (مثل business_types) عبر post_migrate،
                    # فنفرّغ ما سنستعيده أولاً كي تبقى نسختك أنت لا نسخة الزرع.
                    for qs in reversed(_querysets(db)):
                        qs.model.objects.using(db).all()._raw_delete(db)
                    for obj in serializers.deserialize('json', json.dumps(records), using=db):
                        obj.save(using=db)
                connection.check_constraints()
        except Exception as exc:
            raise CommandError(
                f'تم الـ flush لكن فشلت استعادة البيانات الأساسية ({exc}). أرجعها بـ: '
                f'python manage.py loaddata {path}'
            ) from exc
        self.stdout.write(self.style.SUCCESS('أُعيدت البيانات الأساسية (كتالوج، أنواع الأنشطة، السوبر يوزر).'))


def argparse_suppress():
    import argparse
    return argparse.SUPPRESS
