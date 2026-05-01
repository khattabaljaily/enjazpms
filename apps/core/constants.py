DEFAULT_COUNTRY = 'السودان'
DEFAULT_TIMEZONE = 'Africa/Khartoum'

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
    ('لبنان', 'لبنان'),
    ('العراق', 'العراق'),
    ('سوريا', 'سوريا'),
    ('اليمن', 'اليمن'),
    ('المغرب', 'المغرب'),
    ('الجزائر', 'الجزائر'),
    ('تونس', 'تونس'),
    ('ليبيا', 'ليبيا'),
    ('تركيا', 'تركيا'),
    ('إيران', 'إيران'),
    ('باكستان', 'باكستان'),
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
    'لبنان': 'Asia/Beirut',
    'العراق': 'Asia/Baghdad',
    'سوريا': 'Asia/Damascus',
    'اليمن': 'Asia/Aden',
    'المغرب': 'Africa/Casablanca',
    'الجزائر': 'Africa/Algiers',
    'تونس': 'Africa/Tunis',
    'ليبيا': 'Africa/Tripoli',
    'تركيا': 'Europe/Istanbul',
    'إيران': 'Asia/Tehran',
    'باكستان': 'Asia/Karachi',
}


def get_timezone_for_country(country):
    return COUNTRY_TIMEZONE_MAP.get(str(country).strip(), DEFAULT_TIMEZONE)
