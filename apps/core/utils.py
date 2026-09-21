CURRENCY_SYMBOLS = {
    'SDG': 'ج.س', 'USD': '$', 'CNY': '¥', 'AED': 'د.إ',
    'SAR': 'ر.س', 'EGP': 'ج.م', 'EUR': '€', 'GBP': '£',
    'JOD': 'د.أ', 'KWD': 'د.ك', 'QAR': 'ر.ق', 'BHD': 'د.ب', 'OMR': 'ر.ع',
}

CURRENCY_NAMES_AR = {
    'USD': 'الدولار الأمريكي', 'CNY': 'اليوان الصيني', 'AED': 'الدرهم الإماراتي',
    'SAR': 'الريال السعودي', 'EGP': 'الجنيه المصري', 'EUR': 'اليورو',
    'GBP': 'الجنيه الإسترليني', 'JOD': 'الدينار الأردني', 'KWD': 'الدينار الكويتي',
    'QAR': 'الريال القطري', 'BHD': 'الدينار البحريني', 'OMR': 'الريال العماني',
    'SDG': 'الجنيه السوداني',
}


def currency_symbol(code):
    return CURRENCY_SYMBOLS.get((code or '').upper(), code or '')


def convert_arabic_numerals(value):
    """Convert Arabic numerals to English in a string."""
    arabic_digits = '٠١٢٣٤٥٦٧٨٩'
    english_digits = '0123456789'
    return ''.join(english_digits[arabic_digits.index(c)] if c in arabic_digits else c for c in str(value))


def filter_by_branch_via(qs, branch, field='stock__branch'):
    """
    فلترة queryset حسب الفرع عبر علاقة غير مباشرة (مثال: فاتورة → مخزن → فرع).
    branch=None لا يفلتر شيء. السجلات المرتبطة بمخزن بدون فرع تظل ظاهرة.
    """
    if branch is None:
        return qs
    from django.db.models import Q
    return qs.filter(Q(**{field: branch}) | Q(**{f'{field}__isnull': True}))