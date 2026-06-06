"""
Management command: auto_backup_tenants
Backs up all active tenants and removes backups older than 7 days.
Schedule via cron: 0 6,18 * * * /path/to/venv/bin/python manage.py auto_backup_tenants
"""
from django.core.management.base import BaseCommand

from apps.core.models import Tenant
from apps.core.backup_service import create_backup, cleanup_old_backups


class Command(BaseCommand):
    help = 'Create automatic backups for all active tenants and clean up old ones'

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant',
            type=str,
            help='Backup a specific tenant by slug (optional)',
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
        retention = options['retention_days']

        if slug:
            tenants = Tenant.objects.filter(slug=slug, is_active=True)
            if not tenants.exists():
                self.stderr.write(self.style.ERROR(f'Tenant "{slug}" not found or inactive'))
                return
        else:
            tenants = Tenant.objects.filter(is_active=True)

        self.stdout.write(f'Starting auto-backup for {tenants.count()} tenant(s)...')

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
