"""
Tenant Backup Service
Pure-Python backup using Django's database connection (no mysqldump/mysql dependency).
"""
import datetime
import decimal
import logging
import re
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from django.apps import apps as django_apps
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

if TYPE_CHECKING:
    from .models import TenantBackup

logger = logging.getLogger(__name__)

BACKUP_ROOT = Path(settings.BASE_DIR) / 'backups'
RETENTION_DAYS = 7

# جداول تخص المنصة لا المشترك: لا تدخل النسخة ولا تُمسّ عند الاسترجاع
# (تذاكر الدعم ورسائلها، وقدرات المشترك التي تحددها الإدارة، وسجل النسخ نفسه).
PLATFORM_OWNED_TABLES = {'tenant_backups', 'support_tickets', 'tenant_capabilities'}

# حقول صف المشترك التي تتحكم فيها إدارة المنصة (الاشتراك والباقة والحدود): تبقى بقيمها
# الحالية عند الاسترجاع؛ أما بيانات المشترك نفسه (الاسم، العملة، الشعار...) فتعود من النسخة.
PLATFORM_CONTROLLED_TENANT_FIELDS = (
    'subscription_plan', 'subscription_start', 'subscription_expires',
    'is_active', 'is_demo', 'is_approved', 'approved_at',
    'version_type', 'max_branches', 'max_stocks', 'max_users',
    'purchasing_mode', 'purchasing_mode_locked',
    'terms_accepted_at', 'terms_version',
)

MEDIA_SUFFIX = '.media.zip'


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


def _data_tables() -> list[str]:
    """جداول بيانات المشترك (بعمود tenant_id) دون جداول المنصة."""
    return [t for t in _tenant_tables() if t not in PLATFORM_OWNED_TABLES]


def _m2m_tables() -> list[tuple[str, str, str, str]]:
    """
    جداول الربط (many-to-many) التي مالكها موديل تابع لمشترك. هذه الجداول بلا عمود
    tenant_id فلا تظهر في _tenant_tables، ومنها عضوية المستخدمين في مجموعات الصلاحيات.
    يرجع [(جدول الربط، عمود المالك فيه، جدول المالك، عمود tenant في جدول المالك)].
    """
    out = []
    for model in django_apps.get_models():
        if not any(f.name == 'tenant' for f in model._meta.fields):
            continue
        tenant_col = model._meta.get_field('tenant').column
        for field in model._meta.many_to_many:
            through = field.remote_field.through
            if not through._meta.auto_created:
                continue
            out.append((through._meta.db_table, field.m2m_column_name(),
                        model._meta.db_table, tenant_col))
    return out


def _media_zip_path(file_path) -> Path:
    p = Path(file_path)
    return p.with_name(p.stem + MEDIA_SUFFIX)


def _tenant_media_files(tenant) -> list[str]:
    """مسارات (نسبة إلى MEDIA_ROOT) الملفات المرفوعة التابعة لهذا المشترك وحده."""
    names = set()
    for model in django_apps.get_models():
        file_fields = [f.name for f in model._meta.fields if f.get_internal_type() in ('FileField', 'ImageField')]
        if not file_fields:
            continue
        if model._meta.label == 'core.Tenant':
            qs = model._base_manager.filter(pk=tenant.pk)
        elif any(f.name == 'tenant' for f in model._meta.fields):
            qs = model._base_manager.filter(tenant=tenant)
        else:
            continue  # ملفات المنصة نفسها لا تخص مشتركاً
        for field in file_fields:
            names.update(n for n in qs.exclude(**{field: ''}).exclude(**{f'{field}__isnull': True})
                         .values_list(field, flat=True) if n)
    return sorted(names)


def _safe_media_path(root: Path, name: str):
    """مسار الملف داخل MEDIA_ROOT، أو None لو حاول الخروج منه."""
    target = (root / name).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def _write_media_zip(tenant, zip_path: Path) -> int:
    """يضغط ملفات المشترك الموجودة فعلاً على القرص. يرجع عددها (ولا ينشئ ملفاً لو صفراً)."""
    root = Path(settings.MEDIA_ROOT)
    files = []
    for name in _tenant_media_files(tenant):
        src = _safe_media_path(root, name)
        if src is not None and src.is_file():
            files.append((name, src))
    if not files:
        return 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, src in files:
            zf.write(src, arcname=name)
    return len(files)


def _restore_media_zip(zip_path: Path) -> int:
    """يعيد ملفات المشترك إلى MEDIA_ROOT (يكتب فوق المطابق ولا يحذف غيره)."""
    root = Path(settings.MEDIA_ROOT)
    restored = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = _safe_media_path(root, info.filename)
            if target is None:
                logger.warning("Skipped unsafe media path in backup: %s", info.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, 'wb') as dst:
                dst.write(src.read())
            restored += 1
    return restored


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
        parts.append(f"INSERT INTO `{table}` ({col_str}) VALUES ({vals});\n")
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


def _dump_m2m(f, join_table: str, owner_col: str, owner_table: str, tenant_col: str, tenant_id) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT * FROM `{join_table}` WHERE `{owner_col}` IN "
            f"(SELECT id FROM `{owner_table}` WHERE `{tenant_col}` = %s)", [tenant_id])
        rows = cursor.fetchall()
        if rows:
            cols = [d[0] for d in cursor.description]
            f.write(f"-- {join_table}\n")
            f.write(_rows_to_insert(join_table, cols, rows))
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
            f.write("-- ENJAZ Tenant Backup\n")
            f.write(f"-- Tenant : {tenant.name} (id={tenant.id}, slug={tenant.slug})\n")
            f.write(f"-- Created: {now.isoformat()}\n")
            f.write(f"-- Type   : {backup_type}\n\n")
            f.write("SET FOREIGN_KEY_CHECKS=0;\n\n")

            # Tenant row itself
            tenant_table = tenant.__class__._meta.db_table
            _dump_table_where(f, tenant_table, 'id', tenant.id)

            # All tenant-scoped tables (exclude platform-owned ones)
            for table in _data_tables():
                _dump_table_where(f, table, 'tenant_id', tenant.id)

            # many-to-many join tables (no tenant_id column of their own)
            for join_table, owner_col, owner_table, tenant_col in _m2m_tables():
                _dump_m2m(f, join_table, owner_col, owner_table, tenant_col, tenant.id)

            f.write("SET FOREIGN_KEY_CHECKS=1;\n")

        media_zip = _media_zip_path(file_path)
        _write_media_zip(tenant, media_zip)

        record.status = 'completed'
        record.file_size = file_path.stat().st_size + (media_zip.stat().st_size if media_zip.exists() else 0)
    except Exception as exc:
        logger.exception("Backup failed for tenant %s: %s", tenant.slug, exc)
        record.status = 'failed'
        for leftover in (file_path, _media_zip_path(file_path)):
            if leftover.exists():
                leftover.unlink()

    record.save()
    return record


_INSERT_TABLE_RE = re.compile(r'^INSERT\s+(?:IGNORE\s+)?INTO\s+`([^`]+)`', re.IGNORECASE)


def _load_statements(file_path: Path) -> list[str]:
    """
    يقرأ عبارات ملف النسخة. العبارات القديمة كانت INSERT IGNORE فتُحوَّل إلى INSERT عادية
    حتى لا يُتخطّى أي صف بصمت عند تعارض (مثل اسم مستخدم صار لمشترك آخر)، وعبارات جداول
    المنصة تُسقط حتى لو كانت في نسخة قديمة، وعبارات SET FOREIGN_KEY_CHECKS يديرها الاسترجاع.
    """
    content = Path(file_path).read_text(encoding='utf-8')
    statements = []
    # Split on `;\n` — safe because our generator puts each statement on one line
    for chunk in content.split(';\n'):
        lines = [ln for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith('--')]
        stmt = '\n'.join(lines).strip()
        if not stmt or stmt.upper().startswith('SET FOREIGN_KEY_CHECKS'):
            continue
        m = _INSERT_TABLE_RE.match(stmt)
        if m and m.group(1) in PLATFORM_OWNED_TABLES:
            continue
        statements.append(re.sub(r'^INSERT\s+IGNORE\s+INTO', 'INSERT INTO', stmt, count=1, flags=re.IGNORECASE))
    return statements


def _tenant_platform_values(tenant_table: str, tenant_id) -> dict:
    """القيم الحالية لحقول صف المشترك التي تتحكم فيها المنصة."""
    from .models import Tenant
    columns = [Tenant._meta.get_field(n).column for n in PLATFORM_CONTROLLED_TENANT_FIELDS
               if any(f.name == n for f in Tenant._meta.fields)]
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(f'`{c}`' for c in columns)} FROM `{tenant_table}` WHERE id = %s", [tenant_id])
        row = cursor.fetchone()
    return dict(zip(columns, row)) if row else {}


def restore_backup(backup_id: int) -> tuple[bool, str]:
    """
    Restore a tenant from a backup record.
    Automatically creates a pre_restore snapshot first.
    The database part runs in ONE transaction: any failure rolls everything back,
    so the tenant is never left half-restored.
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

    try:
        statements = _load_statements(Path(backup.file_path))
    except Exception as exc:
        logger.exception("Could not read backup file %s: %s", backup.file_path, exc)
        return False, f"تعذّرت قراءة ملف النسخة الاحتياطية: {exc}"

    # Auto-snapshot before restore
    pre_record = create_backup(tenant, backup_type='pre_restore')
    if pre_record.status != 'completed':
        return False, "فشل إنشاء نسخة احتياطية قبل الاستعادة"

    tables = _data_tables()
    m2m = _m2m_tables()
    tenant_table = tenant.__class__._meta.db_table
    platform_values = _tenant_platform_values(tenant_table, tenant.id)

    # Purge orphaned records: in_progress (stuck) and failed with missing files
    stale_qs = TenantBackup.objects.filter(tenant=tenant).exclude(pk=pre_record.pk)
    for r in stale_qs.filter(status__in=['in_progress', 'failed']):
        if not Path(r.file_path).exists():
            r.delete()

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET FOREIGN_KEY_CHECKS=0")
                try:
                    # join tables first: they are located through their owner rows
                    for join_table, owner_col, owner_table, tenant_col in m2m:
                        cursor.execute(
                            f"DELETE FROM `{join_table}` WHERE `{owner_col}` IN "
                            f"(SELECT id FROM `{owner_table}` WHERE `{tenant_col}` = %s)", [tenant.id])
                    for table in tables:
                        cursor.execute(f"DELETE FROM `{table}` WHERE tenant_id = %s", [tenant.id])
                    cursor.execute(f"DELETE FROM `{tenant_table}` WHERE id = %s", [tenant.id])

                    for stmt in statements:
                        cursor.execute(stmt)

                    # الاشتراك والباقة والحدود تبقى بقيمها الحالية لا بقيم وقت النسخة
                    if platform_values:
                        assignments = ', '.join(f'`{c}` = %s' for c in platform_values)
                        cursor.execute(
                            f"UPDATE `{tenant_table}` SET {assignments} WHERE id = %s",
                            [*platform_values.values(), tenant.id])
                finally:
                    cursor.execute("SET FOREIGN_KEY_CHECKS=1")
    except Exception as exc:
        logger.exception("Restore failed for tenant %s: %s", tenant.slug, exc)
        return False, (f"فشلت الاستعادة وتم التراجع عنها كاملاً (لم تتغيّر أي بيانات): {exc}")

    media_note = ''
    media_zip = _media_zip_path(backup.file_path)
    if media_zip.exists():
        try:
            count = _restore_media_zip(media_zip)
            media_note = f" وأُعيد {count} ملف مرفوع."
        except Exception as exc:
            logger.exception("Media restore failed for tenant %s: %s", tenant.slug, exc)
            media_note = f" لكن تعذّرت استعادة الملفات المرفوعة: {exc}"

    return True, (f"تم الاستعادة بنجاح.{media_note} الاشتراك والباقة بقيا كما هما حالياً. "
                  f"نسخة احتياطية قبل الاستعادة: {pre_record.filename}")


def _remove_backup_files(file_path) -> None:
    for path in (Path(file_path), _media_zip_path(file_path)):
        if path.exists():
            path.unlink()


def build_backup_download(backup):
    """
    ملف التنزيل: .sql كما هو لو لا ملفات مرفوعة، وإلا حزمة zip واحدة فيها database.sql
    وmedia/... . يرجع (كائن ملف، اسم التنزيل، content_type).
    """
    file_path = Path(backup.file_path)
    media_zip = _media_zip_path(file_path)
    if not media_zip.exists():
        return open(file_path, 'rb'), backup.filename, 'application/sql'
    bundle = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as out:
        out.write(file_path, arcname='database.sql')
        with zipfile.ZipFile(media_zip) as media:
            for info in media.infolist():
                if not info.is_dir():
                    out.writestr(f'media/{info.filename}', media.read(info))
    bundle.seek(0)
    return bundle, Path(backup.filename).stem + '.zip', 'application/zip'


def delete_backup(backup_id: int) -> tuple[bool, str]:
    """Delete a backup record and its file."""
    from .models import TenantBackup

    try:
        backup = TenantBackup.objects.get(pk=backup_id)
    except TenantBackup.DoesNotExist:
        return False, "النسخة غير موجودة"

    _remove_backup_files(backup.file_path)

    backup.delete()
    return True, "تم حذف النسخة الاحتياطية"


def cleanup_old_backups(days: int = RETENTION_DAYS) -> int:
    """Delete backups older than `days` days. Returns count deleted."""
    from .models import TenantBackup

    cutoff = timezone.now() - timedelta(days=days)
    old = TenantBackup.objects.filter(created_at__lt=cutoff)
    count = 0
    for backup in old:
        _remove_backup_files(backup.file_path)
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
