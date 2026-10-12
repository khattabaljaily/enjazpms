"""حذف نسخة احتياطية من إعدادات النشاط — للمشترك صاحب النسخة فقط."""
import tempfile
from pathlib import Path

from django.urls import reverse

from apps.core.models import Tenant, TenantBackup
from apps.core.test_utils import TenantTestCase


class TenantBackupDeleteTests(TenantTestCase):
    def _backup(self, tenant, file_path):
        return TenantBackup.objects.create(
            tenant=tenant, filename='b.sql', file_path=str(file_path),
            file_size=3, status='completed',
        )

    def _delete(self, backup):
        return self.client.post(reverse('core:tenant_backup_delete_api', args=[backup.pk]))

    def test_own_backup_is_deleted_with_its_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'b.sql'
            f.write_text('sql')
            b = self._backup(self.tenant, f)
            r = self._delete(b)
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()['success'])
            self.assertFalse(TenantBackup.objects.filter(pk=b.pk).exists())
            self.assertFalse(f.exists())

    def test_other_tenant_backup_is_not_deleted(self):
        other = Tenant.objects.create(
            name='Other tenant', business_type=self.tenant.business_type,
            subscription_plan=self.tenant.subscription_plan, version_type=self.tenant.version_type,
        )
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'o.sql'
            f.write_text('sql')
            b = self._backup(other, f)
            self.assertEqual(self._delete(b).status_code, 404)
            self.assertTrue(TenantBackup.objects.filter(pk=b.pk).exists())
            self.assertTrue(f.exists())

    def test_get_not_allowed(self):
        b = self._backup(self.tenant, '/nonexistent/x.sql')
        r = self.client.get(reverse('core:tenant_backup_delete_api', args=[b.pk]))
        self.assertEqual(r.status_code, 405)
