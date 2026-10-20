import os
import tempfile
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings

from apps.catalog.models import MasterDrug, MasterDrugAlias
from apps.core.models import BusinessType


class SafeFlushTests(TransactionTestCase):
    def setUp(self):
        User = get_user_model()
        self.tmp = tempfile.mkdtemp()
        self._patch = override_settings(BASE_DIR=self.tmp)
        self._patch.enable()
        BusinessType.objects.all().delete()
        BusinessType.objects.create(name='Pharmacy', name_ar='صيدلية', slug='pharmacy')
        User.objects.create_superuser('root', 'root@example.com', 'pw-12345')
        other = User.objects.create_user('staff', 'staff@example.com', 'pw-12345')
        drug = MasterDrug.objects.create(generic_name='Acarbose', created_by=other)
        MasterDrug.objects.create(generic_name='Acarbose 2', merged_into=drug)
        MasterDrugAlias.objects.create(master_drug=drug, trade_name='Glucar', created_by=other)

    def tearDown(self):
        self._patch.disable()

    def _flush(self, *args):
        call_command('flush', '--noinput', *args, stdout=StringIO())

    def test_test_database_keeps_stock_behaviour(self):
        self._flush()  # قاعدة test_* => سلوك Django الأصلي، لا شيء يبقى
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(MasterDrug.objects.count(), 0)

    def test_wipe_all_removes_everything(self):
        self._flush('--wipe-all', '--force-keep')
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(MasterDrug.objects.count(), 0)

    def test_flush_keeps_catalog_business_types_and_superuser_only(self):
        self._flush('--force-keep')
        User = get_user_model()
        self.assertEqual(list(User.objects.values_list('username', flat=True)), ['root'])
        self.assertTrue(User.objects.get(username='root').check_password('pw-12345'))
        self.assertEqual(list(BusinessType.objects.values_list('slug', flat=True)), ['pharmacy'])
        self.assertEqual(MasterDrug.objects.count(), 2)
        self.assertEqual(MasterDrugAlias.objects.get().trade_name, 'Glucar')
        self.assertIsNone(MasterDrug.objects.get(generic_name='Acarbose').created_by)
        self.assertEqual(MasterDrug.objects.get(generic_name='Acarbose 2').merged_into.generic_name, 'Acarbose')
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, 'backups'))), 1)

    def test_interactive_without_yes_aborts(self):
        with mock.patch('builtins.input', return_value='no'):
            with self.assertRaises(CommandError):
                call_command('flush', '--force-keep', stdout=StringIO())
        self.assertTrue(get_user_model().objects.filter(username='staff').exists())
