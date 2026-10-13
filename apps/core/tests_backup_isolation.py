"""
النسخ الاحتياطي والاسترجاع لكل مشترك: العزل بين المشتركين، وعضويات المجموعات (many-to-many)،
وبقاء الاشتراك والباقة، والملفات المرفوعة، وذرّية الاسترجاع.
"""
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone

from apps.accounts.models import PermissionGroup, User
from apps.core import backup_service
from apps.core.models import Tenant, TenantBackup
from apps.core.test_utils import TenantTestCase, make_customer


class BackupRestoreIsolationTests(TenantTestCase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.media = Path(self._tmp.name) / 'media'
        self.media.mkdir()
        patchers = [
            mock.patch.object(backup_service, 'BACKUP_ROOT', Path(self._tmp.name) / 'backups'),
            override_settings(MEDIA_ROOT=str(self.media)),
        ]
        for p in patchers:
            p.enable() if hasattr(p, 'enable') else p.start()
            self.addCleanup(p.disable if hasattr(p, 'disable') else p.stop)

        self.other = Tenant.objects.create(
            name='المشترك الآخر', business_type=self.tenant.business_type,
            subscription_plan='pro', version_type=self.tenant.version_type,
            terms_version=settings.TERMS_VERSION, terms_accepted_at=timezone.now())
        self.other_user = User.objects.create_user(
            username='other-admin', password='x', tenant=self.other, is_tenant_admin=True)

    # ── مساعدات ──
    def names(self, tenant):
        from apps.customers.models import Customer
        mgr = getattr(Customer, 'unscoped', Customer.objects)
        return sorted(mgr.filter(tenant=tenant).values_list('name', flat=True))

    def backup(self, tenant=None):
        record = backup_service.create_backup(tenant or self.tenant, 'manual')
        self.assertEqual(record.status, 'completed')
        return record

    # ── 1) النسخة تخص المشترك وحده ──
    def test_backup_file_contains_only_that_tenants_rows(self):
        make_customer(self.tenant, name='عميل-أ')
        make_customer(self.other, name='عميل-ب')
        text = Path(self.backup().file_path).read_text(encoding='utf-8')
        self.assertIn('عميل-أ', text)
        self.assertNotIn('عميل-ب', text)
        self.assertNotIn('other-admin', text)

    # ── 2) الاسترجاع يمس المشترك وحده ──
    def test_restore_returns_only_this_tenants_data(self):
        make_customer(self.tenant, name='قديم')
        make_customer(self.other, name='آخر-1')
        record = self.backup()

        from apps.customers.models import Customer
        Customer.unscoped.filter(tenant=self.tenant).delete()
        make_customer(self.tenant, name='بعد-النسخة')
        make_customer(self.other, name='آخر-2')

        ok, msg = backup_service.restore_backup(record.pk)
        self.assertTrue(ok, msg)
        self.assertEqual(self.names(self.tenant), ['قديم'])
        self.assertEqual(self.names(self.other), ['آخر-1', 'آخر-2'])
        self.assertTrue(User.objects.filter(username='other-admin').exists())

    # ── 3) عضويات المجموعات (many-to-many) ──
    def test_group_memberships_are_backed_up_and_restored_per_tenant(self):
        group = PermissionGroup.objects.create(tenant=self.tenant, name='مجموعة أ')
        group.users.add(self.user)
        other_group = PermissionGroup.objects.create(tenant=self.other, name='مجموعة ب')
        other_group.users.add(self.other_user)
        record = self.backup()
        self.assertIn('permission_groups_users', Path(record.file_path).read_text(encoding='utf-8'))

        group.users.remove(self.user)
        other_group.users.remove(self.other_user)
        ok, msg = backup_service.restore_backup(record.pk)
        self.assertTrue(ok, msg)
        self.assertEqual(list(PermissionGroup.objects.get(tenant=self.tenant, name='مجموعة أ').users.all()),
                         [self.user])
        # عضوية المشترك الآخر لم تُمس (ظلّت محذوفة كما تركناها)
        self.assertEqual(list(PermissionGroup.objects.get(tenant=self.other, name='مجموعة ب').users.all()), [])

    # ── 4) الاشتراك والباقة يبقيان ──
    def test_restore_keeps_current_subscription_but_restores_tenant_data(self):
        self.tenant.name = 'الاسم وقت النسخة'
        self.tenant.subscription_plan = 'basic'
        self.tenant.subscription_expires = date(2026, 1, 1)
        self.tenant.max_users = 5
        self.tenant.save()
        record = self.backup()

        Tenant.objects.filter(pk=self.tenant.pk).update(
            name='اسم جديد', subscription_plan='enterprise',
            subscription_expires=date(2030, 1, 1), max_users=50)
        ok, msg = backup_service.restore_backup(record.pk)
        self.assertTrue(ok, msg)
        t = Tenant.objects.get(pk=self.tenant.pk)
        self.assertEqual(t.name, 'الاسم وقت النسخة')                 # من النسخة
        self.assertEqual(t.subscription_plan, 'enterprise')           # الحالي
        self.assertEqual(t.subscription_expires, date(2030, 1, 1))    # الحالي
        self.assertEqual(t.max_users, 50)                             # الحالي

    # ── 5) الملفات المرفوعة ──
    def test_media_files_are_backed_up_restored_and_scoped(self):
        self.tenant.logo.save('mine.png', ContentFile(b'mine'), save=True)
        self.other.logo.save('theirs.png', ContentFile(b'theirs'), save=True)
        record = self.backup()
        zip_path = backup_service._media_zip_path(record.file_path)
        with zipfile.ZipFile(zip_path) as zf:
            self.assertEqual(zf.namelist(), [self.tenant.logo.name])

        mine = self.media / self.tenant.logo.name
        theirs = self.media / self.other.logo.name
        mine.unlink()
        theirs.write_bytes(b'changed')
        ok, msg = backup_service.restore_backup(record.pk)
        self.assertTrue(ok, msg)
        self.assertEqual(mine.read_bytes(), b'mine')
        self.assertEqual(theirs.read_bytes(), b'changed')   # ملف مشترك آخر لم يُلمس

    def test_download_bundles_database_and_media(self):
        self.tenant.logo.save('mine.png', ContentFile(b'mine'), save=True)
        record = self.backup()
        fileobj, filename, ctype = backup_service.build_backup_download(record)
        self.assertEqual(ctype, 'application/zip')
        with zipfile.ZipFile(fileobj) as zf:
            self.assertIn('database.sql', zf.namelist())
            self.assertIn(f'media/{self.tenant.logo.name}', zf.namelist())

    def test_download_is_plain_sql_without_media(self):
        record = self.backup()
        fileobj, filename, ctype = backup_service.build_backup_download(record)
        self.assertEqual(ctype, 'application/sql')
        fileobj.close()

    def test_delete_removes_media_companion(self):
        self.tenant.logo.save('mine.png', ContentFile(b'mine'), save=True)
        record = self.backup()
        zip_path = backup_service._media_zip_path(record.file_path)
        self.assertTrue(zip_path.exists())
        backup_service.delete_backup(record.pk)
        self.assertFalse(zip_path.exists())
        self.assertFalse(Path(record.file_path).exists())

    # ── 6) ذرّية الاسترجاع وعدم التخطي الصامت ──
    def test_conflict_rolls_everything_back(self):
        make_customer(self.tenant, name='عميل-قبل')
        record = self.backup()
        old_username = self.user.username

        # بعد النسخة: المستخدم يغيّر اسمه ويأخذ مشترك آخر اسمه القديم، فيتعارض إدخاله عند الاسترجاع
        User.objects.filter(pk=self.user.pk).update(username='renamed-user')
        User.objects.create_user(username=old_username, password='x', tenant=self.other)
        make_customer(self.tenant, name='عميل-بعد')

        ok, msg = backup_service.restore_backup(record.pk)
        self.assertFalse(ok)
        self.assertIn('التراجع', msg)
        # لا شيء تغيّر: بيانات المشترك الحالية كما هي، وليس استرجاعاً جزئياً
        self.assertEqual(self.names(self.tenant), ['عميل-بعد', 'عميل-قبل'])
        self.assertTrue(User.objects.filter(pk=self.user.pk, username='renamed-user').exists())
        from django.db import connection
        with connection.cursor() as c:
            c.execute('SELECT @@FOREIGN_KEY_CHECKS')
            self.assertEqual(c.fetchone()[0], 1)

    def test_old_insert_ignore_backups_become_strict(self):
        stmts = backup_service._load_statements(self._write_sql(
            "INSERT IGNORE INTO `customers` (`id`) VALUES (1);\n"
            "INSERT INTO `support_tickets` (`id`) VALUES (1);\n"
            "SET FOREIGN_KEY_CHECKS=1;\n"))
        self.assertEqual(stmts, ["INSERT INTO `customers` (`id`) VALUES (1)"])

    def _write_sql(self, text):
        p = Path(self._tmp.name) / 'old.sql'
        p.write_text(text, encoding='utf-8')
        return p
