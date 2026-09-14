"""
مصدر اسم المشروع الوحيد — يُقرأ من branding/brand.txt (راجع branding/README.md).
يُخزَّن في الذاكرة طوال عمر العملية (نفس أسلوب secrets.json في PROJECT/settings.py)
فتغيير الملف يحتاج إعادة تشغيل الخدمة ليظهر أثره — هذا متوقَّع ومتّسق مع أن
استبدال محتوى مجلد branding/ أصلاً خطوة تُنفَّذ وقت النشر، لا وقت التشغيل.
"""
from pathlib import Path

from django.conf import settings

_DEFAULT_BRAND_NAME = 'ENJAZ'
_brand_name_cache = None


def get_brand_name():
    global _brand_name_cache
    if _brand_name_cache is None:
        brand_file = Path(settings.BASE_DIR) / 'branding' / 'brand.txt'
        try:
            lines = brand_file.read_text(encoding='utf-8').strip().splitlines()
            _brand_name_cache = lines[0].strip() if lines else _DEFAULT_BRAND_NAME
        except (FileNotFoundError, IndexError):
            _brand_name_cache = _DEFAULT_BRAND_NAME
    return _brand_name_cache
