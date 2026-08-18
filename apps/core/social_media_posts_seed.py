import json
from pathlib import Path

from apps.core.models import SocialMediaPost


DATA_FILE_PATH = Path(__file__).resolve().parent / 'data' / 'social_media_posts.json'


def load_social_media_posts_data():
    if not DATA_FILE_PATH.exists():
        raise FileNotFoundError(f'Social media posts file not found: {DATA_FILE_PATH}')

    with DATA_FILE_PATH.open('r', encoding='utf-8') as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError('social_media_posts.json must contain a JSON array')

    return data


def sync_social_media_posts(verbose=False, stdout=None, style=None):
    """يزرع منشورات التسويق الجاهزة مرة واحدة فقط — لا يكرر الزرع إن كانت المنشورات موجودة أصلاً."""
    if SocialMediaPost.objects.exists():
        if verbose and stdout and style:
            stdout.write(style.WARNING('- توجد منشورات في قاعدة البيانات بالفعل، تم تخطي الزرع.'))
        return {'created': 0, 'skipped': True}

    data = load_social_media_posts_data()

    created_count = 0
    for item in data:
        SocialMediaPost.objects.create(
            category=item['category'],
            content=item['content'],
        )
        created_count += 1

    if verbose and stdout and style:
        stdout.write(style.SUCCESS(f'✓ تم إنشاء {created_count} منشوراً'))

    return {'created': created_count, 'skipped': False}
