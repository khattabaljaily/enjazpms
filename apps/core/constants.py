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
