"""
Initial Data for Marketing Social Media Posts
"""
from django.core.management.base import BaseCommand
from apps.core.social_media_posts_seed import sync_social_media_posts


class Command(BaseCommand):
    help = 'زرع منشورات التسويق الجاهزة من ملف JSON (مرة واحدة فقط)'

    def handle(self, *args, **options):
        result = sync_social_media_posts(verbose=True, stdout=self.stdout, style=self.style)

        if result['skipped']:
            self.stdout.write(self.style.WARNING('لم يتم إنشاء منشورات جديدة.'))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n✅ تم إنشاء {result['created']} منشوراً بنجاح"))
