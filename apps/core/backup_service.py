"""
Tenant Backup Service
Pure-Python backup using Django's database connection (no mysqldump/mysql dependency).
"""
import datetime
import decimal
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


def _tenant_tables() -> list[str]:
    """Return all table names that have a tenant_id column."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = 'tenant_id' "
            "ORDER BY TABLE_NAME"
        )
        return [row[0] for row in cursor.fetchall()]


def _escape_value(value) -> str:
    """Escape a Python value to a safe SQL literal."""
    if value is None:
        return 'NULL'
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, datetime.datetime):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, datetime.date):
        return f"'{value.strftime('%Y-%m-%d')}'"
    if isinstance(value, datetime.time):
        return f"'{value.strftime('%H:%M:%S')}'"
    if isinstance(value, datetime.timedelta):
        total_seconds = int(value.total_seconds())
        h, rem = divmod(abs(total_seconds), 3600)
        m, s = divmod(rem, 60)
        return f"'{h:02d}:{m:02d}:{s:02d}'"
    if isinstance(value, (bytes, bytearray)):
        return f"X'{value.hex()}'"
    s = str(value)
    s = s.replace('\\', '\\\\')
    s = s.replace("'", "\\'")
    s = s.replace('\0', '\\0')
    s = s.replace('\n', '\\n')
    s = s.replace('\r', '\\r')
    s = s.replace('\x1a', '\\Z')
    return f"'{s}'"


def _rows_to_insert(table: str, cols: list[str], rows: list) -> str:
    col_str = ', '.join(f'`{c}`' for c in cols)
    parts = []
    for row in rows:
        vals = ', '.join(_escape_value(v) for v in row)
        parts.append(f"INSERT IGNORE INTO `{table}` ({col_str}) VALUES ({vals});\n")
    return ''.join(parts)


def _dump_table_where(f, table: str, where_col: str, where_val) -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM `{table}` WHERE `{where_col}` = %s", [where_val])
        rows = cursor.fetchall()
        if rows:
            cols = [d[0] for d in cursor.description]
            f.write(f"-- {table}\n")
            f.write(_rows_to_insert(table, cols, rows))
            f.write('\n')


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

    try:
        with file_path.open('w', encoding='utf-8') as f:
            f.write("-- EnjazIMS Tenant Backup\n")
            f.write(f"-- Tenant : {tenant.name} (id={tenant.id}, slug={tenant.slug})\n")
            f.write(f"-- Created: {now.isoformat()}\n")
            f.write(f"-- Type   : {backup_type}\n\n")
            f.write("SET FOREIGN_KEY_CHECKS=0;\n\n")

            # Tenant row itself
            tenant_table = tenant.__class__._meta.db_table
            _dump_table_where(f, tenant_table, 'id', tenant.id)

            # All tenant-scoped tables (exclude backup metadata)
            tables = [t for t in _tenant_tables() if t != 'tenant_backups']
            for table in tables:
                _dump_table_where(f, table, 'tenant_id', tenant.id)

            f.write("SET FOREIGN_KEY_CHECKS=1;\n")

        record.status = 'completed'
        record.file_size = file_path.stat().st_size
    except Exception as exc:
        logger.exception("Backup failed for tenant %s: %s", tenant.slug, exc)
        record.status = 'failed'
        if file_path.exists():
            file_path.unlink()

    record.save()
    return record


def _execute_sql_file(file_path: Path) -> tuple[bool, str]:
    """Execute SQL statements from a backup file via Django's connection."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Split on `;\n` — safe because our generator puts each statement on one line
        raw_chunks = content.split(';\n')
        statements = []
        for chunk in raw_chunks:
            lines = [
                ln for ln in chunk.splitlines()
                if ln.strip() and not ln.strip().startswith('--')
            ]
            stmt = '\n'.join(lines).strip()
            if stmt:
                statements.append(stmt)

        with connection.cursor() as cursor:
            for stmt in statements:
                cursor.execute(stmt)

        return True, ''
    except Exception as exc:
        logger.exception("SQL execution failed: %s", exc)
        return False, str(exc)


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

    tables = [t for t in _tenant_tables() if t != 'tenant_backups']

    # Purge orphaned records: in_progress (stuck) and failed with missing files
    stale_qs = TenantBackup.objects.filter(tenant=tenant).exclude(pk=pre_record.pk)
    for r in stale_qs.filter(status__in=['in_progress', 'failed']):
        if not Path(r.file_path).exists():
            r.delete()

    # Delete current tenant data
    try:
        tenant_table = tenant.__class__._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            for table in tables:
                cursor.execute(f"DELETE FROM `{table}` WHERE tenant_id = %s", [tenant.id])
            cursor.execute(f"DELETE FROM `{tenant_table}` WHERE id = %s", [tenant.id])
            cursor.execute("SET FOREIGN_KEY_CHECKS=1")
    except Exception as exc:
        logger.exception("Delete failed during restore for tenant %s: %s", tenant.slug, exc)
        return False, f"فشل حذف البيانات الحالية: {exc}"

    ok, err = _execute_sql_file(Path(backup.file_path))
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
