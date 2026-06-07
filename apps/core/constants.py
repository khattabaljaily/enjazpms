DEFAULT_COUNTRY = 'السودان'
DEFAULT_TIMEZONE = 'Africa/Khartoum'

# ── العملات ─────────────────────────────────────────────────
CURRENCY_AR = {
    'SDG': 'ج.س',
    'SAR': 'ر.س',
    'AED': 'د.إ',
    'USD': '$',
    'EUR': '€',
    'EGP': 'ج.م',
    'KWD': 'د.ك',
    'QAR': 'ر.ق',
    'BHD': 'د.ب',
    'OMR': 'ر.ع',
    'JOD': 'د.أ',
    'LYD': 'د.ل',
    'TND': 'د.ت',
    'DZD': 'د.ج',
    'MAD': 'د.م',
    'IQD': 'د.ع',
    'SYP': 'ل.س',
    'LBP': 'ل.ل',
    'YER': 'ر.ي',
    'GBP': '£',
}

CURRENCY_CHOICES = [(code, f'{name} — {code}') for code, name in CURRENCY_AR.items()]

# country name (Arabic) → default currency code
COUNTRY_CURRENCY_MAP = {
    'السودان':                     'SDG',
    'مصر':                         'EGP',
    'المملكة العربية السعودية':    'SAR',
    'الإمارات العربية المتحدة':    'AED',
    'الكويت':                      'KWD',
    'قطر':                         'QAR',
    'البحرين':                     'BHD',
    'عمان':                        'OMR',
    'الأردن':                      'JOD',
    'فلسطين':                      'JOD',
    'لبنان':                       'LBP',
    'سوريا':                       'SYP',
    'العراق':                      'IQD',
    'اليمن':                       'YER',
    'المغرب':                      'MAD',
    'الجزائر':                     'DZD',
    'تونس':                        'TND',
    'ليبيا':                       'LYD',
    'موريتانيا':                   'USD',
    'الصومال':                     'USD',
    'جيبوتي':                      'USD',
    'جزر القمر':                   'USD',
}

# IANA timezone → default currency code (derived from COUNTRY_CURRENCY_MAP + COUNTRY_TIMEZONE_MAP)
TIMEZONE_CURRENCY_MAP = {
    'Africa/Khartoum':   'SDG',
    'Africa/Cairo':      'EGP',
    'Asia/Riyadh':       'SAR',
    'Asia/Dubai':        'AED',
    'Asia/Kuwait':       'KWD',
    'Asia/Qatar':        'QAR',
    'Asia/Bahrain':      'BHD',
    'Asia/Muscat':       'OMR',
    'Asia/Amman':        'JOD',
    'Asia/Gaza':         'JOD',
    'Asia/Beirut':       'LBP',
    'Asia/Damascus':     'SYP',
    'Asia/Baghdad':      'IQD',
    'Asia/Aden':         'YER',
    'Africa/Casablanca': 'MAD',
    'Africa/Algiers':    'DZD',
    'Africa/Tunis':      'TND',
    'Africa/Tripoli':    'LYD',
    'Africa/Nouakchott': 'USD',
    'Africa/Mogadishu':  'USD',
    'Africa/Djibouti':   'USD',
    'Indian/Comoro':     'USD',
}

COUNTRY_CHOICES = [
    (DEFAULT_COUNTRY, 'السودان'),
    ('مصر', 'مصر'),
    ('المملكة العربية السعودية', 'المملكة العربية السعودية'),
    ('الإمارات العربية المتحدة', 'الإمارات العربية المتحدة'),
    ('الكويت', 'الكويت'),
    ('قطر', 'قطر'),
    ('البحرين', 'البحرين'),
    ('عمان', 'عمان'),
    ('الأردن', 'الأردن'),
    ('فلسطين', 'فلسطين'),
    ('لبنان', 'لبنان'),
    ('سوريا', 'سوريا'),
    ('العراق', 'العراق'),
    ('اليمن', 'اليمن'),
    ('المغرب', 'المغرب'),
    ('الجزائر', 'الجزائر'),
    ('تونس', 'تونس'),
    ('ليبيا', 'ليبيا'),
    ('موريتانيا', 'موريتانيا'),
    ('الصومال', 'الصومال'),
    ('جيبوتي', 'جيبوتي'),
    ('جزر القمر', 'جزر القمر'),
]

COUNTRY_TIMEZONE_MAP = {
    'السودان': 'Africa/Khartoum',
    'مصر': 'Africa/Cairo',
    'المملكة العربية السعودية': 'Asia/Riyadh',
    'الإمارات العربية المتحدة': 'Asia/Dubai',
    'الكويت': 'Asia/Kuwait',
    'قطر': 'Asia/Qatar',
    'البحرين': 'Asia/Bahrain',
    'عمان': 'Asia/Muscat',
    'الأردن': 'Asia/Amman',
    'فلسطين': 'Asia/Gaza',
    'لبنان': 'Asia/Beirut',
    'سوريا': 'Asia/Damascus',
    'العراق': 'Asia/Baghdad',
    'اليمن': 'Asia/Aden',
    'المغرب': 'Africa/Casablanca',
    'الجزائر': 'Africa/Algiers',
    'تونس': 'Africa/Tunis',
    'ليبيا': 'Africa/Tripoli',
    'موريتانيا': 'Africa/Nouakchott',
    'الصومال': 'Africa/Mogadishu',
    'جيبوتي': 'Africa/Djibouti',
    'جزر القمر': 'Indian/Comoro',
}


def get_timezone_for_country(country):
    return COUNTRY_TIMEZONE_MAP.get(str(country).strip(), DEFAULT_TIMEZONE)
