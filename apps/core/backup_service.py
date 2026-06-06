"""
Tenant Backup Service
Handles per-tenant mysqldump backup, restore, and cleanup.
"""
import os
import subprocess
import logging
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import connection
from django.utils import timezone

if TYPE_CHECKING:
    from .models import TenantBackup

logger = logging.getLogger(__name__)

BACKUP_ROOT = Path(settings.BASE_DIR) / 'backups'
RETENTION_DAYS = 7


def _backup_dir(tenant) -> Path:
    d = BACKUP_ROOT / tenant.slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_config() -> dict:
    """Return raw DB config from secrets.json (via Django settings)."""
    return settings.DATABASES['default']


def _build_conn_args(db: dict) -> list[str]:
    """
    Build MySQL connection CLI args from the DB config.
    Only adds --host / --port when they have actual values,
    so local socket connections (empty HOST) work correctly.
    """
    args = [f'--user={db["USER"]}']
    host = db.get('HOST', '').strip()
    port = str(db.get('PORT', '')).strip()
    if host:
        args.append(f'--host={host}')
    if port:
        args.append(f'--port={port}')
    return args


def _mysql_env(db: dict) -> dict:
    """
    Pass password via MYSQL_PWD environment variable instead of --password=
    so it doesn't appear in the process list.
    """
    env = os.environ.copy()
    env['MYSQL_PWD'] = db.get('PASSWORD', '')
    return env


def _tenant_tables() -> list[str]:
    """Return all table names that have a tenant_id column."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'tenant_id' "
            "ORDER BY TABLE_NAME"
        )
        return [row[0] for row in cursor.fetchall()]


def _run_mysqldump(db: dict, extra_args: list, tables: list, out_path: Path) -> bool:
    """Run mysqldump and append output to out_path. Returns True on success."""
    cmd = (
        ['mysqldump']
        + _build_conn_args(db)
        + [
            '--no-create-info',
            '--no-create-db',
            '--skip-add-drop-table',
            '--insert-ignore',
            '--complete-insert',
            '--single-transaction',
            '--skip-lock-tables',
        ]
        + extra_args
        + [db['NAME']]
        + tables
    )
    try:
        with out_path.open('ab') as f:
            result = subprocess.run(
                cmd,
                stdout=f,
                stderr=subprocess.PIPE,
                env=_mysql_env(db),
                timeout=300,
            )
        if result.returncode != 0:
            logger.error("mysqldump error: %s", result.stderr.decode())
            return False
        return True
    except Exception as exc:
        logger.exception("mysqldump failed: %s", exc)
        return False


def _run_mysql(db: dict, sql: str | None = None, file_path: Path | None = None) -> tuple[bool, str]:
    """
    Run a mysql command with either inline SQL or a file.
    Returns (success, error_message).
    """
    cmd = ['mysql'] + _build_conn_args(db) + [db['NAME']]
    try:
        if file_path:
            with open(file_path, 'rb') as f:
                result = subprocess.run(
                    cmd,
                    stdin=f,
                    capture_output=True,
                    env=_mysql_env(db),
                    timeout=300,
                )
        else:
            result = subprocess.run(
                cmd,
                input=sql.encode() if sql else b'',
                capture_output=True,
                env=_mysql_env(db),
                timeout=120,
            )
        if result.returncode != 0:
            return False, result.stderr.decode()
        return True, ''
    except Exception as exc:
        logger.exception("mysql command failed: %s", exc)
        return False, str(exc)


def create_backup(tenant, backup_type: str = 'manual') -> 'TenantBackup':
    """Create a backup for a single tenant. Returns the TenantBackup instance."""
    from .models import TenantBackup

    now = timezone.localtime(timezone.now())
    timestamp = now.strftime('%Y-%m-%d_%H-%M-%S')
    filename = f"backup_{tenant.slug}_{timestamp}_{backup_type}.sql"
    file_path = _backup_dir(tenant) / filename

    record = TenantBackup.objects.create(
        tenant=tenant,
        filename=filename,
        file_path=str(file_path),
        backup_type=backup_type,
        status='in_progress',
    )

    db = _db_config()

    # Header
    with file_path.open('w', encoding='utf-8') as f:
        f.write("-- EnjazIMS Tenant Backup\n")
        f.write(f"-- Tenant : {tenant.name} (id={tenant.id}, slug={tenant.slug})\n")
        f.write(f"-- DB Host: {db.get('HOST', 'socket') or 'socket'}\n")
        f.write(f"-- DB Name: {db['NAME']}\n")
        f.write(f"-- Created: {now.isoformat()}\n")
        f.write(f"-- Type   : {backup_type}\n\n")
        f.write("SET FOREIGN_KEY_CHECKS=0;\n\n")

    # 1. Backup the tenant row itself
    tenant_table = tenant.__class__._meta.db_table
    ok1 = _run_mysqldump(db, [f'--where=id={tenant.id}'], [tenant_table], file_path)

    # 2. Backup all tenant-scoped tables (exclude backup metadata — managed separately)
    tables = [t for t in _tenant_tables() if t != 'tenant_backups']
    ok2 = _run_mysqldump(db, [f'--where=tenant_id={tenant.id}'], tables, file_path)

    # Footer
    with file_path.open('a', encoding='utf-8') as f:
        f.write("\nSET FOREIGN_KEY_CHECKS=1;\n")

    if ok1 and ok2 and file_path.exists():
        record.status = 'completed'
        record.file_size = file_path.stat().st_size
    else:
        record.status = 'failed'
        if file_path.exists():
            file_path.unlink()

    record.save()
    return record


def restore_backup(backup_id: int) -> tuple[bool, str]:
    """
    Restore a tenant from a backup record.
    Automatically creates a pre_restore snapshot first.
    Returns (success, message).
    """
    from .models import TenantBackup

    try:
        backup = TenantBackup.objects.select_related('tenant').get(pk=backup_id)
    except TenantBackup.DoesNotExist:
        return False, "النسخة الاحتياطية غير موجودة"

    if backup.status != 'completed':
        return False, "النسخة الاحتياطية غير مكتملة"

    if not backup.file_exists:
        return False, "ملف النسخة الاحتياطية غير موجود على القرص"

    tenant = backup.tenant

    # Auto-snapshot before restore
    pre_record = create_backup(tenant, backup_type='pre_restore')
    if pre_record.status != 'completed':
        return False, "فشل إنشاء نسخة احتياطية قبل الاستعادة"

    db = _db_config()
    # Exclude tenant_backups — preserve backup history across restores
    tables = [t for t in _tenant_tables() if t != 'tenant_backups']

    # Purge orphaned records: in_progress (stuck) and failed with missing files
    stale_qs = TenantBackup.objects.filter(tenant=tenant).exclude(pk=pre_record.pk)
    for r in stale_qs.filter(status__in=['in_progress', 'failed']):
        if not Path(r.file_path).exists():
            r.delete()

    # Build DELETE SQL for all tenant tables + the tenant row itself
    delete_parts = ["SET FOREIGN_KEY_CHECKS=0;\n"]
    for table in tables:
        delete_parts.append(f"DELETE FROM `{table}` WHERE tenant_id = {tenant.id};\n")
    delete_parts.append(f"DELETE FROM `{tenant.__class__._meta.db_table}` WHERE id = {tenant.id};\n")
    delete_parts.append("SET FOREIGN_KEY_CHECKS=1;\n")

    ok, err = _run_mysql(db, sql="".join(delete_parts))
    if not ok:
        return False, f"فشل حذف البيانات الحالية: {err}"

    ok, err = _run_mysql(db, file_path=Path(backup.file_path))
    if not ok:
        return False, f"فشل استيراد النسخة الاحتياطية: {err}"

    return True, f"تم الاستعادة بنجاح. نسخة احتياطية قبل الاستعادة: {pre_record.filename}"


def delete_backup(backup_id: int) -> tuple[bool, str]:
    """Delete a backup record and its file."""
    from .models import TenantBackup

    try:
        backup = TenantBackup.objects.get(pk=backup_id)
    except TenantBackup.DoesNotExist:
        return False, "النسخة غير موجودة"

    file_path = Path(backup.file_path)
    if file_path.exists():
        file_path.unlink()

    backup.delete()
    return True, "تم حذف النسخة الاحتياطية"


def cleanup_old_backups(days: int = RETENTION_DAYS) -> int:
    """Delete backups older than `days` days. Returns count deleted."""
    from .models import TenantBackup

    cutoff = timezone.now() - timedelta(days=days)
    old = TenantBackup.objects.filter(created_at__lt=cutoff)
    count = 0
    for backup in old:
        file_path = Path(backup.file_path)
        if file_path.exists():
            file_path.unlink()
        backup.delete()
        count += 1
    return count


def get_tenant_backup_stats(tenant) -> dict:
    """Return summary stats for a tenant's backups."""
    from .models import TenantBackup

    qs = TenantBackup.objects.filter(tenant=tenant, status='completed')
    total = qs.count()
    total_size = sum(b.file_size for b in qs)
    latest = qs.first()
    return {
        'total': total,
        'total_size': total_size,
        'latest': latest,
    }
