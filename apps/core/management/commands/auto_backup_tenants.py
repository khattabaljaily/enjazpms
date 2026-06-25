"""
Management command: auto_backup_tenants
Backs up active tenants according to their plan's backup quota and removes old backups.

Plan tiers:
  basic      → manual only (auto backup excluded)
  pro        → once daily  (run with --slot=1)
  enterprise → twice daily (run with --slot=1 and --slot=2)

Recommended cron setup:
  Once at 02:00 for Pro + Enterprise:
    0 2 * * *  python manage.py auto_backup_tenants --slot=1
  Once at 14:00 for Enterprise only:
    0 14 * * * python manage.py auto_backup_tenants --slot=2

--slot=1 backs up plans where auto_backup_daily >= 1  (pro + enterprise)
--slot=2 backs up plans where auto_backup_daily >= 2  (enterprise only)
"""
from django.core.management.base import BaseCommand

from apps.core.models import Tenant
from apps.core.backup_service import create_backup, cleanup_old_backups


class Command(BaseCommand):
    help = 'Create automatic backups for eligible tenants and clean up old ones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant',
            type=str,
            help='Backup a specific tenant by slug (optional)',
        )
        parser.add_argument(
            '--slot',
            type=int,
            default=1,
            help=(
                'Backup slot number (1 or 2). '
                'Slot 1 backs up pro+enterprise; slot 2 backs up enterprise only.'
            ),
        )
        parser.add_argument(
            '--retention-days',
            type=int,
            default=7,
            help='Number of days to keep backups (default: 7)',
        )
        parser.add_argument(
            '--skip-cleanup',
            action='store_true',
            help='Skip deleting old backups',
        )

    def handle(self, *args, **options):
        slug = options.get('tenant')
        slot = options['slot']
        retention = options['retention_days']

        if slug:
            tenants = Tenant.objects.filter(slug=slug, is_active=True)
            if not tenants.exists():
                self.stderr.write(self.style.ERROR(f'Tenant "{slug}" not found or inactive'))
                return
        else:
            # Filter tenants whose plan allows at least `slot` auto backups per day
            eligible_tenants = [
                t for t in Tenant.objects.filter(is_active=True)
                if t.auto_backup_daily_count() >= slot
            ]
            tenants = eligible_tenants

        count = len(tenants) if isinstance(tenants, list) else tenants.count()
        self.stdout.write(f'Starting auto-backup (slot={slot}) for {count} tenant(s)...')

        success_count = 0
        fail_count = 0

        for tenant in tenants:
            self.stdout.write(f'  Backing up: {tenant.name} ({tenant.slug})', ending=' ... ')
            try:
                record = create_backup(tenant, backup_type='auto')
                if record.status == 'completed':
                    self.stdout.write(self.style.SUCCESS(f'OK ({record.file_size_display})'))
                    success_count += 1
                else:
                    self.stdout.write(self.style.ERROR('FAILED'))
                    fail_count += 1
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f'ERROR: {exc}'))
                fail_count += 1

        if not options['skip_cleanup']:
            self.stdout.write(f'\nCleaning up backups older than {retention} days...')
            deleted = cleanup_old_backups(days=retention)
            self.stdout.write(self.style.SUCCESS(f'  Deleted {deleted} old backup(s)'))

        self.stdout.write(
            self.style.SUCCESS(f'\nDone. Success: {success_count}, Failed: {fail_count}')
        )
