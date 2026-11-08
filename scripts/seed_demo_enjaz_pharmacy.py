# -*- coding: utf-8 -*-
"""
بيانات تجريبية شاملة لمشترك جديد «صيدلية انجاز» (نسخة المؤسسات / مشتريات هجين) بفرعين: الخرطوم وعطبرة.
يُنشئ المشترك ومديره (انجاز) وفرعيه ثم يملؤه ببيانات شهر كامل للخلف، بأسماء أدوية وتصنيفات إنجليزية،
و40% من الأصناف مأخوذة من الكتالوج المركزي (التصنيفات = الأشكال الدوائية في الكتالوج).
مبني على seed_demo_haramain.py.

التشغيل (من مجلد المشروع، بيئة المشروع الافتراضية):
    .env/bin/python manage.py shell < scripts/seed_demo_haramain.py

متغيرات بيئة اختيارية:
    SEED_DRY_RUN=1        تنفيذ كامل ثم تراجع (rollback) بدل الحفظ
    SEED_SCALE=0.2        تصغير حجم المبيعات للتجربة السريعة (الافتراضي 1.0)
    SEED_SKIP_BACKUP=1    عدم إنشاء ملفي النسخ الاحتياطي في النهاية

الضمانات:
  * كل شيء داخل transaction.atomic() واحدة: أي خطأ = لا شيء يُحفظ.
  * يتوقف بلا تغيير إن لم يوجد المشترك، أو إن وُجدت لديه منتجات/فواتير (علامة منع التكرار).
  * لا يعدّل ولا يحذف أي سجل موجود (الفروع/التصنيفات/الخزائن/المستخدمون/الإعدادات تُعاد استعمالها).
  * لا ينشئ مستخدمين لتسجيل الدخول؛ الموظفون سجلات HR فقط.
  * يمرّ على خدمات المشروع (confirm_sale_invoice، confirm_purchase_invoice، الشحنات، الخزينة...)
    فتتسق الكميات والدفاتر وأرصدة الخزائن، وتُشغَّل ساعة افتراضية لتوزيع التواريخ على آخر ~91 يوماً.
  * يفحص في النهاية: لا مخزون سالب، وربح موجب وهامش مجمل ≥ 30% لكل الفترات والفروع، وإلا يتراجع كله.
"""
import os
import math
import random
import datetime as dt
import traceback
import zoneinfo
from collections import defaultdict
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR

from django.apps import apps as django_apps
from django.db import transaction
from django.db.models import Sum, F, DecimalField, ExpressionWrapper
from django.db.models.signals import post_save
from django.utils import timezone as djtz

TENANT_NAME = 'صيدلية انجاز'
ADMIN_USERNAME = 'انجاز'
ADMIN_PASSWORD = '12345'
CATALOG_SHARE = 0.40         # نسبة الأصناف المأخوذة من الكتالوج المركزي
HISTORY_DAYS = 31
SCALE = float(os.environ.get('SEED_SCALE', '1.0'))
DRY_RUN = os.environ.get('SEED_DRY_RUN') == '1'
SKIP_BACKUP = os.environ.get('SEED_SKIP_BACKUP') == '1'
BASE_INVOICES_MAIN = 15      # فواتير/يوم للفرع الرئيسي (قبل الأثر الأسبوعي والاتجاه)
BASE_INVOICES_OTHER = 10
TARGET_MAIN = 840000         # إيراد يومي مستهدف للفرع الرئيسي (قبل أثر الأسبوع والاتجاه)
TARGET_OTHER = 560000
CAP_PURCHASES = 0.70         # سقف المشتريات الشهرية من المبيعات الشهرية (للحاكم الداخلي)
NO_PURCHASE_LAST_DAYS = 3


class SeedAbort(Exception):
    pass


class _State:
    pass


S = _State()

# ──────────────────────────────────────────────────────────────────────────
#  الساعة الافتراضية
# ──────────────────────────────────────────────────────────────────────────
_REAL_NOW = djtz.now
_REAL_LOCALDATE = djtz.localdate
SIM = {'dt': None}


def _sim_now():
    return SIM['dt'] if SIM['dt'] is not None else _REAL_NOW()


def _sim_localdate(value=None, timezone=None):
    if value is None and SIM['dt'] is not None:
        return SIM['dt'].astimezone(S.tz).date()
    return _REAL_LOCALDATE(value, timezone) if value is not None else _REAL_LOCALDATE()


def set_clock(day, minutes):
    """يضبط الساعة الافتراضية إلى (day, minutes من منتصف الليل) بتوقيت المشترك، دون تجاوز الوقت الحقيقي."""
    minutes = int(minutes)
    when = dt.datetime(day.year, day.month, day.day, tzinfo=S.tz) + dt.timedelta(minutes=minutes)
    real = _REAL_NOW().astimezone(S.tz)
    if when > real:
        when = real
    SIM['dt'] = when
    return when


def clock_install():
    djtz.now = _sim_now
    djtz.localdate = _sim_localdate


def clock_restore():
    djtz.now = _REAL_NOW
    djtz.localdate = _REAL_LOCALDATE
    SIM['dt'] = None


# ──────────────────────────────────────────────────────────────────────────
#  أدوات عامة
# ──────────────────────────────────────────────────────────────────────────
def log(msg):
    print(msg, flush=True)


def d0(x):
    return D(str(x))


def round_up(x, step):
    step = D(str(step))
    return (D(str(x)) / step).to_integral_value(rounding=ROUND_CEILING) * step


def round_down(x, step):
    step = D(str(step))
    return (D(str(x)) / step).to_integral_value(rounding=ROUND_FLOOR) * step


def phone(rng):
    return '+249' + rng.choice(['91', '92', '90', '96', '99', '12', '11']) + ''.join(str(rng.randint(0, 9)) for _ in range(7))


def month_key(day):
    return (day.year, day.month)


def add_months(day, months):
    y = day.year + (day.month - 1 + months) // 12
    m = (day.month - 1 + months) % 12 + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(day.day, last))


def month_last_day(y, m):
    return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day


# ──────────────────────────────────────────────────────────────────────────
#  بيانات المنتجات
#  (key, اسم, اسم إنجليزي, مادة فعالة, شكل, تركيز, عبوة, فئة, مصنّع, منشأ, تكلفة, نسبة, شعبية, مصدر, علامات, خطوة شراء)
#  المصدر: c = يشترى مركزياً ويوزَّع بشحنات، l = يشتريه الفرع محلياً.
#  العلامات: rx وصفة، cold ثلاجة، noins خارج التأمين، nb بلا تتبع دفعات
# ──────────────────────────────────────────────────────────────────────────
ITEMS = [
    # مسكنات وخافضات حرارة
    ('panadol', 'بانادول 500 مجم أقراص', 'Panadol 500mg', 'Paracetamol', 'أقراص', '500 مجم', 'علبة 24 قرص', 'analgesic', 'GSK', 'بريطانيا', 2400, 1.70, 30, 'c', '', 24),
    ('adol', 'أدول 500 مجم أقراص', 'Adol 500mg', 'Paracetamol', 'أقراص', '500 مجم', 'علبة 20 قرص', 'analgesic', 'Julphar', 'الإمارات', 1700, 1.75, 16, 'l', '', 24),
    ('cetamol', 'سيتامول 500 مجم أقراص', 'Cetamol 500mg', 'Paracetamol', 'أقراص', '500 مجم', 'علبة 20 قرص', 'analgesic', 'Neelain Pharma', 'السودان', 1300, 1.80, 12, 'l', '', 24),
    ('panadol_extra', 'بانادول إكسترا أقراص', 'Panadol Extra', 'Paracetamol + Caffeine', 'أقراص', '500/65 مجم', 'علبة 24 قرص', 'analgesic', 'GSK', 'بريطانيا', 3600, 1.70, 12, 'c', '', 12),
    ('panadol_baby', 'بانادول بيبي شراب', 'Panadol Baby Suspension', 'Paracetamol', 'شراب', '120 مجم/5 مل', 'زجاجة 100 مل', 'analgesic', 'GSK', 'بريطانيا', 5500, 1.65, 9, 'c', '', 12),
    ('brufen400', 'بروفين 400 مجم أقراص', 'Brufen 400mg', 'Ibuprofen', 'أقراص', '400 مجم', 'علبة 30 قرص', 'analgesic', 'Abbott', 'أمريكا', 3200, 1.65, 16, 'c', '', 12),
    ('ibuprofen_nl', 'إيبوبروفين 400 مجم أقراص', 'Ibuprofen 400mg', 'Ibuprofen', 'أقراص', '400 مجم', 'علبة 30 قرص', 'analgesic', 'Neelain Pharma', 'السودان', 2100, 1.80, 7, 'l', '', 12),
    ('brufen_syr', 'بروفين شراب 100 مجم/5 مل', 'Brufen Syrup', 'Ibuprofen', 'شراب', '100 مجم/5 مل', 'زجاجة 100 مل', 'analgesic', 'Abbott', 'أمريكا', 4500, 1.65, 8, 'c', '', 12),
    ('voltaren50', 'فولتارين 50 مجم أقراص', 'Voltaren 50mg', 'Diclofenac', 'أقراص', '50 مجم', 'علبة 20 قرص', 'analgesic', 'Novartis', 'سويسرا', 5800, 1.60, 9, 'c', '', 12),
    ('voltaren_gel', 'فولتارين إميلجيل 1% جل', 'Voltaren Emulgel 1%', 'Diclofenac', 'جل', '1%', 'أنبوب 50 جم', 'analgesic', 'Novartis', 'سويسرا', 9500, 1.65, 7, 'c', '', 6),
    ('cataflam', 'كاتافلام 50 مجم أقراص', 'Cataflam 50mg', 'Diclofenac Potassium', 'أقراص', '50 مجم', 'علبة 20 قرص', 'analgesic', 'Novartis', 'سويسرا', 6500, 1.60, 6, 'c', '', 12),
    ('aspirin100', 'أسبرين بروتكت 100 مجم', 'Aspirin Protect 100mg', 'Acetylsalicylic Acid', 'أقراص', '100 مجم', 'علبة 30 قرص', 'analgesic', 'Bayer', 'ألمانيا', 4200, 1.60, 7, 'c', '', 12),
    # مضادات حيوية
    ('augmentin1g', 'أوغمنتين 1 جم أقراص', 'Augmentin 1g', 'Amoxicillin + Clavulanic Acid', 'أقراص', '875/125 مجم', 'علبة 14 قرص', 'antibiotic', 'GSK', 'بريطانيا', 18500, 1.55, 9, 'c', 'rx', 6),
    ('augmentin_syr', 'أوغمنتين شراب 457 مجم/5 مل', 'Augmentin Suspension', 'Amoxicillin + Clavulanic Acid', 'شراب', '457 مجم/5 مل', 'زجاجة 70 مل', 'antibiotic', 'GSK', 'بريطانيا', 14500, 1.55, 5, 'c', 'rx', 6),
    ('amoxil500', 'أموكسيل 500 مجم كبسولات', 'Amoxil 500mg', 'Amoxicillin', 'كبسولات', '500 مجم', 'علبة 16 كبسولة', 'antibiotic', 'Neelain Pharma', 'السودان', 5500, 1.65, 10, 'l', 'rx', 12),
    ('zithromax', 'زيثروماكس 500 مجم أقراص', 'Zithromax 500mg', 'Azithromycin', 'أقراص', '500 مجم', 'علبة 3 أقراص', 'antibiotic', 'Pfizer', 'أمريكا', 15500, 1.55, 6, 'c', 'rx', 6),
    ('cipro500', 'سيبروباي 500 مجم أقراص', 'Ciprobay 500mg', 'Ciprofloxacin', 'أقراص', '500 مجم', 'علبة 10 أقراص', 'antibiotic', 'Bayer', 'ألمانيا', 11000, 1.55, 5, 'c', 'rx', 6),
    ('flagyl', 'فلاجيل 500 مجم أقراص', 'Flagyl 500mg', 'Metronidazole', 'أقراص', '500 مجم', 'علبة 20 قرص', 'antibiotic', 'Sanofi', 'فرنسا', 4800, 1.65, 7, 'c', 'rx', 12),
    ('zinnat', 'زينات 500 مجم أقراص', 'Zinnat 500mg', 'Cefuroxime', 'أقراص', '500 مجم', 'علبة 10 أقراص', 'antibiotic', 'GSK', 'بريطانيا', 24000, 1.50, 4, 'c', 'rx', 6),
    ('doxy100', 'دوكسيسيكلين 100 مجم كبسولات', 'Doxycycline 100mg', 'Doxycycline', 'كبسولات', '100 مجم', 'علبة 10 كبسولات', 'antibiotic', 'Julphar', 'الإمارات', 3800, 1.70, 3, 'l', 'rx', 12),
    # الجهاز الهضمي
    ('omez20', 'أوميز 20 مجم كبسولات', 'Omez 20mg', 'Omeprazole', 'كبسولات', '20 مجم', 'علبة 14 كبسولة', 'gi', "Dr. Reddy's", 'الهند', 6800, 1.65, 12, 'c', '', 12),
    ('omeprazole_jl', 'أوميبرازول 20 مجم كبسولات', 'Omeprazole 20mg', 'Omeprazole', 'كبسولات', '20 مجم', 'علبة 14 كبسولة', 'gi', 'Julphar', 'الإمارات', 4200, 1.80, 6, 'l', '', 12),
    ('nexium40', 'نيكسيوم 40 مجم أقراص', 'Nexium 40mg', 'Esomeprazole', 'أقراص', '40 مجم', 'علبة 14 قرص', 'gi', 'AstraZeneca', 'السويد', 21000, 1.50, 6, 'c', 'rx', 6),
    ('gaviscon', 'جافيسكون شراب', 'Gaviscon Liquid', 'Sodium Alginate', 'شراب', '', 'زجاجة 150 مل', 'gi', 'Reckitt', 'بريطانيا', 8900, 1.65, 8, 'c', '', 6),
    ('buscopan', 'بسكوبان 10 مجم أقراص', 'Buscopan 10mg', 'Hyoscine Butylbromide', 'أقراص', '10 مجم', 'علبة 20 قرص', 'gi', 'Sanofi', 'فرنسا', 7200, 1.65, 8, 'c', '', 12),
    ('imodium', 'إيموديوم 2 مجم كبسولات', 'Imodium 2mg', 'Loperamide', 'كبسولات', '2 مجم', 'علبة 6 كبسولات', 'gi', 'Janssen', 'بلجيكا', 3900, 1.70, 5, 'l', '', 12),
    ('ors', 'أملاح الجفاف ORS', 'ORS Sachets', 'Oral Rehydration Salts', 'مسحوق', '', 'علبة 10 أكياس', 'gi', 'Khartoum Pharma', 'السودان', 1800, 1.90, 9, 'l', '', 24),
    ('duspatalin', 'دوسباتالين 135 مجم أقراص', 'Duspatalin 135mg', 'Mebeverine', 'أقراص', '135 مجم', 'علبة 30 قرص', 'gi', 'Abbott', 'أمريكا', 16000, 1.55, 4, 'c', '', 6),
    # الأمراض المزمنة
    ('glucophage850', 'جلوكوفاج 850 مجم أقراص', 'Glucophage 850mg', 'Metformin', 'أقراص', '850 مجم', 'علبة 30 قرص', 'chronic', 'Merck', 'ألمانيا', 5200, 1.50, 15, 'c', 'rx', 12),
    ('metformin500_jl', 'ميتفورمين 500 مجم أقراص', 'Metformin 500mg', 'Metformin', 'أقراص', '500 مجم', 'علبة 50 قرص', 'chronic', 'Julphar', 'الإمارات', 3600, 1.55, 8, 'l', 'rx', 12),
    ('diamicron', 'دياميكرون 60 مجم MR', 'Diamicron MR 60mg', 'Gliclazide', 'أقراص', '60 مجم', 'علبة 30 قرص', 'chronic', 'Servier', 'فرنسا', 12000, 1.50, 8, 'c', 'rx', 6),
    ('norvasc5', 'نورفاسك 5 مجم أقراص', 'Norvasc 5mg', 'Amlodipine', 'أقراص', '5 مجم', 'علبة 30 قرص', 'chronic', 'Pfizer', 'أمريكا', 7500, 1.50, 12, 'c', 'rx', 12),
    ('amlodipine_jl', 'أملوديبين 5 مجم أقراص', 'Amlodipine 5mg', 'Amlodipine', 'أقراص', '5 مجم', 'علبة 30 قرص', 'chronic', 'Julphar', 'الإمارات', 4300, 1.60, 6, 'l', 'rx', 12),
    ('concor5', 'كونكور 5 مجم أقراص', 'Concor 5mg', 'Bisoprolol', 'أقراص', '5 مجم', 'علبة 30 قرص', 'chronic', 'Merck', 'ألمانيا', 14500, 1.48, 8, 'c', 'rx', 6),
    ('cozaar50', 'كوزار 50 مجم أقراص', 'Cozaar 50mg', 'Losartan', 'أقراص', '50 مجم', 'علبة 28 قرص', 'chronic', 'MSD', 'أمريكا', 16500, 1.48, 6, 'c', 'rx', 6),
    ('lipitor20', 'ليبيتور 20 مجم أقراص', 'Lipitor 20mg', 'Atorvastatin', 'أقراص', '20 مجم', 'علبة 30 قرص', 'chronic', 'Pfizer', 'أمريكا', 24000, 1.45, 7, 'c', 'rx', 6),
    ('mixtard', 'ميكستارد 30 قلم إنسولين', 'Mixtard 30 FlexPen', 'Human Insulin', 'حقن', '100 وحدة/مل', 'قلم 3 مل', 'chronic', 'Novo Nordisk', 'الدنمارك', 38000, 1.45, 4, 'c', 'rx cold', 4),
    ('lantus', 'لانتوس سولوستار قلم', 'Lantus SoloStar', 'Insulin Glargine', 'حقن', '100 وحدة/مل', 'قلم 3 مل', 'chronic', 'Sanofi', 'فرنسا', 62000, 1.42, 2, 'c', 'rx cold', 4),
    ('eltroxin', 'إلتروكسين 100 مكغ أقراص', 'Eltroxin 100mcg', 'Levothyroxine', 'أقراص', '100 مكغ', 'علبة 100 قرص', 'chronic', 'Aspen', 'جنوب أفريقيا', 6000, 1.55, 4, 'c', 'rx', 6),
    ('plavix75', 'بلافيكس 75 مجم أقراص', 'Plavix 75mg', 'Clopidogrel', 'أقراص', '75 مجم', 'علبة 28 قرص', 'chronic', 'Sanofi', 'فرنسا', 29000, 1.43, 3, 'c', 'rx', 6),
    ('accuchek_strips', 'شرائط فحص السكر أكيو-تشيك أكتف', 'Accu-Chek Active Strips', '', 'شرائط', '', 'علبة 50 شريط', 'chronic', 'Roche', 'ألمانيا', 21000, 1.55, 5, 'c', 'noins', 6),
    # الجهاز التنفسي والحساسية
    ('ventolin', 'فنتولين بخاخ', 'Ventolin Inhaler', 'Salbutamol', 'بخاخ', '100 مكغ', 'عبوة 200 جرعة', 'resp', 'GSK', 'بريطانيا', 8500, 1.60, 5, 'c', '', 6),
    ('claritin', 'كلاريتين 10 مجم أقراص', 'Claritin 10mg', 'Loratadine', 'أقراص', '10 مجم', 'علبة 10 أقراص', 'resp', 'Bayer', 'ألمانيا', 4800, 1.70, 9, 'c', '', 12),
    ('zyrtec', 'زيرتك 10 مجم أقراص', 'Zyrtec 10mg', 'Cetirizine', 'أقراص', '10 مجم', 'علبة 10 أقراص', 'resp', 'UCB', 'بلجيكا', 6000, 1.65, 7, 'c', '', 12),
    ('cetirizine_jl', 'سيتريزين 10 مجم أقراص', 'Cetirizine 10mg', 'Cetirizine', 'أقراص', '10 مجم', 'علبة 10 أقراص', 'resp', 'Julphar', 'الإمارات', 2500, 1.85, 6, 'l', '', 12),
    ('prospan', 'بروسبان شراب السعال', 'Prospan Cough Syrup', 'Ivy Leaf Extract', 'شراب', '', 'زجاجة 100 مل', 'resp', 'Engelhard', 'ألمانيا', 9800, 1.65, 6, 'c', '', 6),
    ('vicks', 'فيكس فابورب مرهم', 'Vicks VapoRub', 'Menthol + Camphor', 'مرهم', '', 'علبة 50 جم', 'resp', 'P&G', 'أمريكا', 4500, 1.80, 8, 'l', '', 12),
    ('otrivin', 'أوتريفين بخاخ أنف', 'Otrivin Nasal Spray', 'Xylometazoline', 'بخاخ', '0.1%', 'عبوة 10 مل', 'resp', 'Novartis', 'سويسرا', 6200, 1.70, 7, 'c', '', 12),
    ('congestal', 'كونجستال أقراص البرد', 'Congestal', 'Paracetamol + Chlorpheniramine + Pseudoephedrine', 'أقراص', '', 'علبة 12 قرص', 'resp', 'Pharco', 'مصر', 3000, 1.80, 10, 'l', '', 24),
    # فيتامينات ومكملات
    ('redoxon', 'ريدوكسون فيتامين سي فوار', 'Redoxon Vitamin C', 'Ascorbic Acid', 'أقراص فوارة', '1000 مجم', 'علبة 10 أقراص', 'vit', 'Bayer', 'ألمانيا', 9500, 1.85, 8, 'c', 'noins', 12),
    ('centrum', 'سنترم أدلت فيتامينات متعددة', 'Centrum Adults', 'Multivitamins', 'أقراص', '', 'علبة 30 قرص', 'vit', 'Pfizer', 'أمريكا', 28000, 1.75, 3, 'c', 'noins', 6),
    ('calcium_d3', 'كالسيوم + فيتامين د3 فوار', 'Calcium + D3 Effervescent', 'Calcium + Cholecalciferol', 'أقراص فوارة', '', 'علبة 20 قرص', 'vit', 'Sandoz', 'سويسرا', 14000, 1.75, 4, 'c', 'noins', 6),
    ('omega3', 'أوميغا 3 كبسولات', 'Omega-3 Fish Oil', 'Omega-3', 'كبسولات', '1000 مجم', 'علبة 30 كبسولة', 'vit', 'Seven Seas', 'بريطانيا', 15500, 1.85, 4, 'l', 'noins', 6),
    ('zinc50', 'زنك 50 مجم أقراص', 'Zinc 50mg', 'Zinc Sulfate', 'أقراص', '50 مجم', 'علبة 30 قرص', 'vit', 'Julphar', 'الإمارات', 6500, 1.95, 4, 'l', 'noins', 12),
    ('vitd3', 'فيتامين د3 50000 وحدة كبسولات', 'Vitamin D3 50000 IU', 'Cholecalciferol', 'كبسولات', '50000 وحدة', 'علبة 4 كبسولات', 'vit', 'Amipharma', 'السودان', 7500, 1.85, 7, 'c', '', 12),
    ('feroglobin', 'فيروغلوبين B12 كبسولات', 'Feroglobin B12', 'Iron + Vitamin B12', 'كبسولات', '', 'علبة 30 كبسولة', 'vit', 'Vitabiotics', 'بريطانيا', 17500, 1.75, 4, 'c', 'noins', 6),
    ('folic5', 'حمض الفوليك 5 مجم أقراص', 'Folic Acid 5mg', 'Folic Acid', 'أقراص', '5 مجم', 'علبة 28 قرص', 'vit', 'Khartoum Pharma', 'السودان', 1800, 2.00, 6, 'l', '', 24),
    # الأم والطفل
    ('bebelac1', 'حليب ببيلاك 1 للرضع', 'Bebelac 1', 'Infant Formula', 'مسحوق', '', 'علبة 400 جم', 'baby', 'Nutricia', 'هولندا', 19000, 1.48, 5, 'c', 'noins nb', 6),
    ('cerelac', 'سيريلاك قمح وحليب', 'Cerelac Wheat & Milk', 'Infant Cereal', 'مسحوق', '', 'علبة 400 جم', 'baby', 'Nestle', 'سويسرا', 9800, 1.55, 6, 'c', 'noins nb', 6),
    ('pampers4', 'حفاضات بامبرز مقاس 4', 'Pampers Size 4', '', 'حفاضات', '', 'عبوة 44 حفاضة', 'baby', 'P&G', 'أمريكا', 42000, 1.48, 4, 'c', 'noins nb', 4),
    ('wipes', 'مناديل مبللة للأطفال', 'Baby Wet Wipes', '', 'مناديل', '', 'عبوة 80 منديل', 'baby', 'Johnson & Johnson', 'أمريكا', 4500, 1.95, 7, 'l', 'noins nb', 12),
    ('bepanthen', 'بيبانثين مرهم الحفاض', 'Bepanthen Ointment', 'Dexpanthenol', 'مرهم', '5%', 'أنبوب 30 جم', 'baby', 'Bayer', 'ألمانيا', 12000, 1.65, 4, 'c', '', 6),
    # العناية الشخصية والتجميل
    ('cerave', 'سيرافي غسول الوجه الرغوي', 'CeraVe Foaming Cleanser', '', 'غسول', '', 'زجاجة 236 مل', 'care', 'L\'Oreal', 'فرنسا', 36000, 1.75, 3, 'l', 'noins nb', 4),
    ('lrp_spf', 'لاروش بوزيه واقي شمس SPF50', 'La Roche-Posay Anthelios SPF50', '', 'كريم', '', 'أنبوب 50 مل', 'care', 'L\'Oreal', 'فرنسا', 52000, 1.70, 2, 'l', 'noins nb', 4),
    ('nivea', 'نيفيا كريم مرطب', 'Nivea Soft Cream', '', 'كريم', '', 'علبة 200 مل', 'care', 'Beiersdorf', 'ألمانيا', 11000, 1.95, 5, 'l', 'noins nb', 6),
    ('hns', 'هيد آند شولدرز شامبو', 'Head & Shoulders Shampoo', '', 'شامبو', '', 'زجاجة 400 مل', 'care', 'P&G', 'أمريكا', 14500, 1.75, 3, 'l', 'noins nb', 6),
    ('sensodyne', 'سنسوداين معجون أسنان', 'Sensodyne Toothpaste', '', 'معجون', '', 'أنبوب 75 مل', 'care', 'GSK', 'بريطانيا', 11500, 1.80, 5, 'l', 'noins nb', 6),
    ('toothbrush', 'فرشاة أسنان أورال بي', 'Oral-B Toothbrush', '', 'فرشاة', '', 'قطعة', 'care', 'P&G', 'أمريكا', 4500, 1.95, 4, 'l', 'noins nb', 12),
    # أدوية موضعية
    ('fucidin', 'فيوسيدين كريم', 'Fucidin Cream 2%', 'Fusidic Acid', 'كريم', '2%', 'أنبوب 15 جم', 'topical', 'Leo Pharma', 'الدنمارك', 11000, 1.60, 4, 'c', 'rx', 6),
    ('canesten', 'كانستين كريم', 'Canesten Cream 1%', 'Clotrimazole', 'كريم', '1%', 'أنبوب 20 جم', 'topical', 'Bayer', 'ألمانيا', 8500, 1.65, 5, 'c', '', 6),
    ('hydrocort', 'هيدروكورتيزون 1% كريم', 'Hydrocortisone 1% Cream', 'Hydrocortisone', 'كريم', '1%', 'أنبوب 15 جم', 'topical', 'Julphar', 'الإمارات', 3200, 1.85, 4, 'l', 'rx', 12),
    ('systane', 'سيستين ألترا قطرة عيون', 'Systane Ultra Eye Drops', 'Polyethylene Glycol', 'قطرة', '', 'عبوة 10 مل', 'topical', 'Alcon', 'أمريكا', 18000, 1.65, 3, 'c', '', 6),
    ('betadine', 'بيتادين محلول مطهر', 'Betadine Solution', 'Povidone Iodine', 'محلول', '10%', 'زجاجة 120 مل', 'topical', 'Mundipharma', 'ألمانيا', 8000, 1.75, 6, 'c', '', 12),
    # أجهزة ومستلزمات
    ('omron_m2', 'جهاز قياس الضغط أومرون M2', 'Omron M2 BP Monitor', '', 'جهاز', '', 'قطعة', 'device', 'Omron', 'اليابان', 165000, 1.50, 1, 'c', 'noins nb', 2),
    ('accuchek_meter', 'جهاز قياس السكر أكيو-تشيك أكتف', 'Accu-Chek Active Meter', '', 'جهاز', '', 'قطعة', 'device', 'Roche', 'ألمانيا', 48000, 1.55, 2, 'c', 'noins nb', 3),
    ('thermometer', 'ترمومتر رقمي', 'Digital Thermometer', '', 'جهاز', '', 'قطعة', 'device', 'Microlife', 'سويسرا', 6500, 2.00, 6, 'l', 'noins nb', 12),
    ('masks', 'كمامات طبية 3 طبقات', 'Surgical Face Masks', '', 'كمامات', '', 'علبة 50 كمامة', 'device', 'Medtex', 'الصين', 6000, 1.85, 5, 'l', 'noins nb', 12),
    ('gloves', 'قفازات فحص لاتكس', 'Latex Examination Gloves', '', 'قفازات', '', 'علبة 100 قطعة', 'device', 'Medtex', 'ماليزيا', 14000, 1.75, 3, 'l', 'noins nb', 6),
    ('syringes', 'محاقن بلاستيك 5 مل', 'Syringes 5ml', '', 'محاقن', '', 'علبة 100 قطعة', 'device', 'Medtex', 'الصين', 9500, 1.65, 3, 'l', 'noins nb', 6),
    ('bandaids', 'لاصق جروح', 'Adhesive Bandages', '', 'لاصق', '', 'علبة 100 قطعة', 'device', 'Johnson & Johnson', 'أمريكا', 5500, 1.95, 5, 'l', 'noins nb', 12),
    ('saline500', 'محلول ملحي 0.9% للغسول', 'Normal Saline 0.9%', 'Sodium Chloride', 'محلول', '0.9%', 'كيس 500 مل', 'device', 'Khartoum Pharma', 'السودان', 3500, 1.75, 3, 'l', 'nb', 12),
]

CATEGORY_DEFS = {
    # key: (اسم افتراضي إن لم يوجد، كلمات مطابقة لتصنيف قائم)
    'analgesic': ('مسكنات وخافضات حرارة', ['مسكن', 'خافض']),
    'antibiotic': ('مضادات حيوية', ['مضاد', 'حيوي']),
    'gi': ('أدوية الجهاز الهضمي', ['هضم', 'معدة']),
    'chronic': ('أدوية الأمراض المزمنة', ['مزمن', 'سكر', 'ضغط', 'قلب']),
    'resp': ('أدوية الجهاز التنفسي والحساسية', ['تنفس', 'حساسية', 'سعال']),
    'vit': ('فيتامينات ومكملات', ['فيتامين', 'مكمل']),
    'baby': ('مستلزمات الأم والطفل', ['أطفال', 'طفل']),
    'care': ('العناية الشخصية والتجميل', ['تجميل', 'عناية']),
    'topical': ('أدوية موضعية وعيون', ['موضع', 'جلد', 'عيون']),
    'device': ('أجهزة ومستلزمات طبية', ['أجهزة', 'جهاز', 'مستلزمات طبية', 'مستلزمات']),
}
GENERAL_DRUG_KEYWORDS = ['أدوية', 'دواء', 'أدويه']
GENERAL_DRUG_CATS = ('analgesic', 'antibiotic', 'gi', 'chronic', 'resp', 'topical')

CENTRAL_SUPPLIERS = [
    ('شركة النيل للأدوية والمستلزمات الطبية', 'الخرطوم', 'analgesic antibiotic'),
    ('مخازن الأمين الطبية', 'أم درمان', 'chronic gi'),
    ('الشركة المتحدة للتوزيع الدوائي', 'الخرطوم بحري', 'resp topical'),
    ('مؤسسة الشروق للمستحضرات الطبية', 'الخرطوم', 'vit baby'),
    ('شركة السلام للأجهزة والمستلزمات الطبية', 'الخرطوم', 'device'),
    ('شركة الرحاب لمستحضرات العناية', 'أم درمان', 'care'),
]
BRANCH_SUPPLIER_NAMES = [
    ['مخازن بحري الطبية', 'شركة أم درمان للتوزيع', 'مؤسسة الخرطوم للمستلزمات الطبية'],
    ['مخازن عطبرة الطبية', 'شركة نهر النيل للتوزيع الدوائي', 'مؤسسة الشمالية للأدوية'],
]
EXPENSE_CATEGORIES = ['إيجار', 'كهرباء ومياه', 'اتصالات وإنترنت', 'نظافة وصيانة', 'مواصلات وتوصيل',
                      'قرطاسية ومطبوعات', 'دعاية وإعلان', 'ضيافة', 'رسوم وتراخيص', 'اشتراكات برامج']
INSURERS = [
    ('شركة النيل للتأمين الصحي', 80, 30),
    ('الأمان للتأمين التكافلي', 70, 45),
    ('شركة الواحة للتأمين الصحي', 90, 30),
    ('الإخلاص للتأمين الطبي', 75, 60),
]
FIRST = ['محمد', 'أحمد', 'عثمان', 'إبراهيم', 'عبد الله', 'حسن', 'ياسر', 'معاوية', 'خالد', 'إسماعيل', 'الصادق', 'مصعب',
         'عمر', 'يوسف', 'عبد الرحمن', 'صلاح', 'فيصل', 'بشير', 'آمنة', 'فاطمة', 'سارة', 'مها', 'تهاني', 'نجلاء',
         'هنادي', 'رشا', 'سلمى', 'عائشة', 'زينب', 'إيمان', 'ابتسام', 'بشرى']
FAMILY = ['الطيب', 'إدريس', 'النور', 'بابكر', 'الحسن', 'آدم', 'موسى', 'حمدان', 'الفاضل', 'يوسف', 'عبد الله', 'سليمان',
          'عوض الكريم', 'التجاني', 'مختار', 'أبكر', 'دفع الله', 'عبد القادر', 'الشيخ', 'كرار']
ORG_CUSTOMERS = ['عيادة النور التخصصية', 'مركز الشفاء الطبي', 'مستشفى الأمل الأهلي', 'مدرسة الرواد الخاصة',
                 'شركة الفجر للاستثمار', 'مؤسسة البركة التجارية', 'مجمع الأطباء الطبي', 'دار الرعاية للمسنين',
                 'روضة البراعم', 'مركز الحياة لغسيل الكلى']
CLAIM_NOTES = ['تم تقديم المطالبة مع الوصفات الأصلية', 'بانتظار مراجعة الشركة', 'مطابقة للوثائق']


# ──────────────────────────────────────────────────────────────────────────
#  إنشاء المشترك وأدواته (جديد في هذا السكربت)
# ──────────────────────────────────────────────────────────────────────────
BRANCH_DEFS = [   # (الاسم، الرمز، العنوان)
    ('الخرطوم', 'KRT', 'الخرطوم — شارع الجمهورية'),
    ('عطبرة', 'ATB', 'عطبرة — السوق الكبير'),
]
EXCHANGE_PATH = [(30, '2480.00'), (25, '2495.00'), (20, '2520.00'), (15, '2540.00'), (10, '2560.00'), (6, '2575.00'), (3, '2590.00'), (1, '2600.00')]
FORM_AR_EN = {'أقراص': 'Tablet', 'أقراص فوارة': 'Tablet', 'كبسولات': 'Capsule', 'شراب': 'Syrup', 'كريم': 'Cream', 'مسحوق': 'Powder',
              'حقن': 'Injection', 'مرهم': 'Ointment', 'محلول': 'Solution', 'جل': 'Gel', 'قطرة': 'Drops', 'بخاخ': 'Inhaler'}
COUNTRY_AR_EN = {'بريطانيا': 'UK', 'الإمارات': 'UAE', 'السودان': 'Sudan', 'أمريكا': 'USA', 'سويسرا': 'Switzerland', 'فرنسا': 'France',
                 'ألمانيا': 'Germany', 'الهند': 'India', 'السويد': 'Sweden', 'بلجيكا': 'Belgium', 'الصين': 'China', 'ماليزيا': 'Malaysia',
                 'مصر': 'Egypt', 'الأردن': 'Jordan', 'السعودية': 'Saudi Arabia', 'هولندا': 'Netherlands', 'إيطاليا': 'Italy', 'إسبانيا': 'Spain'}
STRENGTH_AR_EN = [('مكجم', 'mcg'), ('مجم', 'mg'), ('جم', 'g'), ('مل', 'ml'), ('وحدة', 'IU'), ('قرص', 'tab')]
PACK_AR_EN = [('علبة', 'Box of'), ('زجاجة', 'Bottle'), ('أنبوب', 'Tube'), ('عبوة', 'Pack'), ('كيس', 'Sachet'), ('قلم', 'Pen'),
              ('كبسولات', 'caps'), ('كبسولة', 'caps'), ('أقراص', 'tabs'), ('قرص', 'tabs'), ('أمبولات', 'amp'), ('أمبول', 'amp'),
              ('مل', 'ml'), ('جم', 'g'), ('قطعة', 'pcs'), ('شريط', 'strip')]
PICK_FORMS = ['Tablet', 'Capsule', 'Syrup', 'Injection', 'Cream', 'Drops', 'Ointment', 'Gel', 'Solution', 'Suspension', 'Inhaler']
COST_BY_FORM = {'Tablet': (1500, 9000), 'Capsule': (2000, 12000), 'Syrup': (3000, 9500), 'Injection': (1800, 15000), 'Cream': (3000, 11000),
                'Drops': (2500, 8000), 'Ointment': (3000, 9000), 'Gel': (3500, 9500), 'Solution': (2500, 6500), 'Suspension': (4000, 10000),
                'Inhaler': (9000, 26000)}
CAT_BY_FORM = {'Tablet': ['chronic', 'gi', 'analgesic', 'antibiotic', 'vit'], 'Capsule': ['gi', 'antibiotic', 'vit', 'chronic'],
               'Syrup': ['resp', 'analgesic', 'vit'], 'Injection': ['antibiotic', 'analgesic'], 'Cream': ['topical'], 'Drops': ['topical'],
               'Ointment': ['topical'], 'Gel': ['topical', 'analgesic'], 'Solution': ['gi', 'topical'], 'Suspension': ['antibiotic', 'resp'],
               'Inhaler': ['resp']}
CONTROLLED_GENERICS = ['tramadol', 'diazepam', 'alprazolam', 'codeine', 'clonazepam', 'phenobarbital']
_AR = __import__('re').compile(r'[؀-ۿ]')


def _en_strength(s):
    for a, b in STRENGTH_AR_EN:
        s = s.replace(a, b)
    return s.replace('  ', ' ').strip()


def _en_pack(p, form):
    for a, b in PACK_AR_EN:
        p = p.replace(a, b)
    p = p.strip()
    return f'1 {form.lower()}' if _AR.search(p) or not p else p


def bootstrap_tenant():
    """ينشئ المشترك + مديره + الفرعين + مشرفيهما + مجموعتي صلاحيات (كما تفعل شاشة إنشاء المشترك)."""
    from django.conf import settings as dj
    from apps.core.models import Tenant, Branch, Settings, BusinessType
    from apps.accounts.models import User, PermissionGroup
    from apps.accounts.permissions import get_permission_keys, branch_assignable_permission_keys
    if Tenant.objects.filter(name=TENANT_NAME).exists():
        log(f'المشترك «{TENANT_NAME}» موجود — سيُستعمل كما هو.')
        return
    if User.objects.filter(username=ADMIN_USERNAME).exists():
        raise SeedAbort(f'اسم المستخدم «{ADMIN_USERNAME}» مستخدم بالفعل لمشترك آخر.')
    bt = BusinessType.objects.filter(slug='pharmacy').first()
    if bt is None:
        raise SeedAbort('نوع النشاط «صيدلية» غير موجود.')
    real_now = _REAL_NOW()
    today = real_now.astimezone(zoneinfo.ZoneInfo('Africa/Khartoum')).date()
    limits = Tenant.PLAN_LIMITS['enterprise']
    t = Tenant.objects.create(
        name=TENANT_NAME, business_type=bt, email='', phone='+249912345678', city='الخرطوم', address='الخرطوم — شارع الجمهورية',
        subscription_plan='enterprise', subscription_start=today - dt.timedelta(days=HISTORY_DAYS),
        subscription_expires=today + dt.timedelta(days=365), is_active=True, is_demo=False, is_approved=True, approved_at=real_now,
        version_type='multi_branch', max_branches=limits['max_branches'], max_stocks=limits['max_stocks'], max_users=limits['max_users'],
        timezone='Africa/Khartoum', currency='SDG', hard_currency_mode=True, hard_currency='USD',
        exchange_rate=D(EXCHANGE_PATH[-1][1]), exchange_rate_updated_at=real_now,
        purchasing_mode='hybrid', purchasing_mode_locked=True,
        terms_accepted_at=real_now, terms_version=dj.TERMS_VERSION)
    t.save()   # الحفظ الثاني يُنشئ خزائن الإدارة المركزية والعملة الصعبة (إشارات المشترك)
    Settings.objects.create(tenant=t, tax_enabled=False, tax_value=0)
    owner = User.objects.create_user(username=ADMIN_USERNAME, email='', password=ADMIN_PASSWORD, first_name='مدير', last_name='صيدلية انجاز',
                                     tenant=t, is_tenant_admin=True)
    PermissionGroup.create_owner_group(t, name='مدير النشاط').users.add(owner)
    branches = []
    for i, (nm, code, addr) in enumerate(BRANCH_DEFS):
        b = Branch.objects.create(tenant=t, name=nm, code=code, address=addr, phone=f'+2499{i + 1}1234567', is_active=True, is_default=(i == 0))
        branches.append(b)
    sup_names = {'KRT': ('enjaz-khartoum', 'مشرف', 'فرع الخرطوم'), 'ATB': ('enjaz-atbara', 'مشرف', 'فرع عطبرة')}
    for b in branches:
        uname, fn, ln = sup_names[b.code]
        if User.objects.filter(username=uname).exists():
            raise SeedAbort(f'اسم المستخدم «{uname}» مستخدم بالفعل.')
        u = User.objects.create_user(username=uname, email='', password=ADMIN_PASSWORD, first_name=fn, last_name=ln, tenant=t, branch=b,
                                     is_branch_supervisor=True)
        Branch.objects.filter(pk=b.pk).update(manager=u)
    # مجموعتا صلاحيات (محاسب / مدير مخزون) لفروع المشترك + مستخدمان بكلمة مرور غير قابلة للاستعمال (للعرض فقط)
    allowed = set(branch_assignable_permission_keys(central=True, branch_items_allowed=False))
    groups = {
        'محاسب': [k for k in allowed if any(w in k for w in ('treasur', 'bank', 'expense', 'report', 'salar', 'advance', 'incentive', 'ledger', 'income'))],
        'مدير مخزون': [k for k in allowed if any(w in k for w in ('stock', 'shipment', 'destruction', 'item', 'transfer', 'view_dashboard'))],
    }
    for (gname, keys), b, uname in zip(groups.items(), branches, ['enjaz-accountant', 'enjaz-stockmgr']):
        g = PermissionGroup.objects.create(tenant=t, name=gname, scope='branch', description=f'مجموعة {gname} لموظفي الفرع',
                                           permissions={k: True for k in keys}, is_active=True)
        if User.objects.filter(username=uname).exists():
            continue
        u = User(username=uname, tenant=t, branch=b, first_name=gname, last_name=b.name)
        u.set_unusable_password()
        u.save()
        g.users.add(u)
    Tenant.objects.filter(pk=t.pk).update(created_at=real_now - dt.timedelta(days=HISTORY_DAYS))
    log(f'أُنشئ المشترك #{t.pk} «{t.name}» | مديره «{ADMIN_USERNAME}» | فروع: {[b.name for b in branches]}')


def build_item_rows():
    """قائمة الأصناف: المنتقاة يدوياً (بأسماء إنجليزية، أدوية فقط) + منتقاة من الكتالوج المركزي لتبلغ حصته 40%."""
    from apps.catalog.models import MasterDrug
    from apps.catalog.matching import normalize_text, normalize_strength
    rng = S.rng
    rows, seen_keys, linked = [], set(), 0
    for r in ITEMS:
        (key, name, name_en, generic, form, strength, pack, catk, manu, origin, cost, ratio, pop, src, flags, step) = r
        form_en = FORM_AR_EN.get(form)
        if form_en is None:
            continue                       # أجهزة/مستلزمات/عناية: ليست من أشكال الكتالوج الدوائية، تُستبعد
        if form == 'بخاخ':
            form_en = 'Nasal Spray' if any(w in name_en for w in ('Nasal', 'Otrivin', 'Rhinocort')) else 'Inhaler'
        st_en = _en_strength(strength)
        rows.append((key, name_en, name_en, generic, form_en, st_en, _en_pack(pack, form_en), catk, manu,
                     COUNTRY_AR_EN.get(origin, origin if not _AR.search(origin) else ''), cost, ratio, pop, src, flags, step, {}))
        seen_keys.add((normalize_text(generic), normalize_text(form_en), normalize_strength(st_en)))
        if generic and MasterDrug.objects.filter(generic_name_normalized=normalize_text(generic), dosage_form_normalized=normalize_text(form_en),
                                                 strength_normalized=normalize_strength(st_en), status='active').exists():
            linked += 1
    n_cur = len(rows)
    need = max(0, math.ceil((CATALOG_SHARE * n_cur - linked) / (1 - CATALOG_SHARE)) + 1)
    cands = list(MasterDrug.objects.filter(status='active', dosage_form__in=PICK_FORMS, aliases__isnull=False).exclude(strength='')
                 .values_list('id', flat=True).distinct())
    rng.shuffle(cands)
    ctrl_ids = [i for i in cands if any(g in (MasterDrug.objects.only('generic_name').get(pk=i).generic_name or '').lower() for g in CONTROLLED_GENERICS)][:6]
    order = ctrl_ids + [i for i in cands if i not in ctrl_ids]
    picked = 0
    names_used = {r[1].lower() for r in rows}
    for mid in order:
        if picked >= need:
            break
        md = MasterDrug.objects.get(pk=mid)
        key3 = (md.generic_name_normalized, md.dosage_form_normalized, md.strength_normalized)
        if key3 in seen_keys:
            continue
        al = md.aliases.exclude(trade_name='').order_by('-is_primary', 'id').first()
        if al is None or _AR.search(al.trade_name) or not __import__('re').match(r"^[A-Za-z][A-Za-z0-9 \-\.\+'/%]{2,60}$", al.trade_name.strip()):
            continue
        if _AR.search(md.generic_name or '') or _AR.search(md.strength or ''):
            continue
        nm = f'{al.trade_name.strip()} {md.strength}'.strip()
        if nm.lower() in names_used:
            continue
        names_used.add(nm.lower())
        seen_keys.add(key3)
        form_en = md.dosage_form
        lo, hi = COST_BY_FORM[form_en]
        cost = int(round(rng.uniform(lo, hi) / 50) * 50)
        catk = rng.choice(CAT_BY_FORM[form_en])
        controlled = mid in ctrl_ids
        flags = ' '.join(f for f in (['rx'] if (form_en == 'Injection' or controlled or catk == 'antibiotic') else []) + (['cold'] if form_en == 'Injection' and rng.random() < 0.2 else []))
        pack = (al.pack_size or '').strip()
        rows.append((f'cat{md.id}', nm, nm, md.generic_name, form_en, md.strength, pack if (pack and not _AR.search(pack)) else f'1 {form_en.lower()}', catk,
                     al.manufacturer, al.country_of_origin if not _AR.search(al.country_of_origin or '') else '', cost, round(rng.uniform(1.55, 1.85), 2),
                     rng.choice([2, 3, 3, 4, 5]), rng.choice(['c', 'c', 'c', 'l']), flags, rng.choice([6, 12]),
                     {'md': md, 'controlled': controlled, 'agent': al.sudan_agent}))
        picked += 1
    log(f'قائمة الأصناف: {n_cur} منتقاة يدوياً (منها {linked} مطابقة للكتالوج) + {picked} من الكتالوج = {len(rows)} | حصة الكتالوج المتوقعة ≈ {(linked + picked) / len(rows):.0%}')
    return rows


def seed_exchange_history():
    """سجل تغيّر سعر الصرف خلال الشهر (للشاشة وللمخطط) — آخر سعر = سعر المشترك الحالي."""
    from apps.core.models import ExchangeRateHistory
    for days_ago, rate in EXCHANGE_PATH:
        when = dt.datetime.combine(S.today - dt.timedelta(days=days_ago), dt.time(9, 30), tzinfo=S.tz)
        h = ExchangeRateHistory.objects.create(tenant=S.tenant, rate=D(rate), changed_by=S.owner,
                                               notes='تحديث السعر الابتدائي' if days_ago == EXCHANGE_PATH[0][0] else 'تحديث يدوي')
        ExchangeRateHistory.objects.filter(pk=h.pk).update(changed_at=when)
    S.counters['exchange_rate_history'] += len(EXCHANGE_PATH)


def setup_store():
    """تفعيل المتجر الإلكتروني وقائمة الأسعار العامة للمشترك وفرعيه."""
    from apps.store.models import StoreSettings
    t = S.tenant
    ss, _ = StoreSettings.objects.get_or_create(
        tenant=t, defaults=dict(slug='enjaz-pharmacy', is_enabled=True, display_name='صيدلية انجاز',
                                description='صيدلية متكاملة بفرعي الخرطوم وعطبرة — أدوية أصلية وخدمة توصيل.',
                                whatsapp='+249912345678', show_price_list=True, show_prices=True,
                                bank_details='بنك الخرطوم — حساب رقم 1234567890 — صيدلية انجاز', created_by=S.owner))
    if not ss.is_enabled:
        ss.is_enabled = True
        ss.show_price_list = True
        ss.save()


def add_expired_batches():
    """يجعل دفعات من الأصناف بطيئة الحركة منتهية الصلاحية (بكمية متبقية)، ويُتلف جزءاً منها بسجل إتلاف مؤكد."""
    from apps.items.models import ItemBatch
    from apps.stocks.models import StockDestruction, StockDestructionLine
    from apps.stocks.services import confirm_stock_destruction
    rng = S.rng
    qs = ItemBatch.objects.filter(tenant=S.tenant, quantity_remaining__gte=4, stock__is_central=False, expiry_date__gt=S.today,
                                  item__selling_price__lt=60000).order_by('?')
    seen, made = set(), []
    for b in qs:
        if (b.stock_id, b.item_id) in seen:
            continue
        oldest = ItemBatch.objects.filter(tenant=S.tenant, stock=b.stock, item=b.item, quantity_remaining__gt=0).order_by('expiry_date').first()
        if oldest is None or oldest.pk != b.pk:
            continue
        seen.add((b.stock_id, b.item_id))
        b.expiry_date = S.today - dt.timedelta(days=rng.randint(4, 110))
        b.notes = 'دفعة منتهية الصلاحية — بانتظار الإتلاف'
        b.save(update_fields=['expiry_date', 'notes'])
        made.append(b)
        if len(made) >= 16:
            break
    S.counters['expired_batches'] = len(made)
    # إتلاف مؤكد لدفعتين منتهيتين لكل فرع (بسبب «منتهي الصلاحية»)
    by_stock = defaultdict(list)
    for b in made:
        by_stock[b.stock_id].append(b)
    set_clock(S.today - dt.timedelta(days=2), 900)
    for br in S.branches:
        st = S.stock_of[br.id]
        pick = by_stock.get(st.id, [])[:2]
        if not pick:
            continue
        user = S.branch_user[br.id]
        with transaction.atomic():
            de = StockDestruction.objects.create(
                tenant=S.tenant, stock=st, destruction_date=S.today - dt.timedelta(days=2), status='draft', reason='expired',
                witness_name='لجنة الجرد بالفرع', reference_number=f'محضر إتلاف منتهي الصلاحية {S.today:%Y%m%d}-{br.code}',
                notes='إتلاف أدوية انتهت صلاحيتها', created_by=user)
            n_lines = 0
            for b in pick:
                avail = S.qty[(st.id, b.item_id)] - S.qty_reserved[(st.id, b.item_id)]
                q = min(b.quantity_remaining, avail)
                if q <= 0:
                    continue
                StockDestructionLine.objects.create(
                    tenant=S.tenant, destruction=de, item=b.item, batch=b, batch_number_snapshot=b.batch_number,
                    expiry_date_snapshot=b.expiry_date, quantity=q, unit_cost_snapshot=b.item.cost_price, created_by=user)
                n_lines += 1
            if not n_lines:
                de.delete()
                continue
            confirm_stock_destruction(de, user)
            for ln in de.lines.all():
                S.qty[(st.id, ln.item_id)] -= ln.quantity
        S.counters['destructions_expired_confirmed'] += 1
    clock_restore()


# ──────────────────────────────────────────────────────────────────────────
#  المراحل
# ──────────────────────────────────────────────────────────────────────────
def preflight():
    from apps.core.models import Tenant, Branch
    from apps.items.models import Item
    from apps.sales.models import SaleInvoice
    qs = Tenant.objects.filter(name=TENANT_NAME)
    if qs.count() == 0:
        raise SeedAbort(f'لا يوجد مشترك باسم «{TENANT_NAME}» في هذه القاعدة.')
    if qs.count() > 1:
        raise SeedAbort(f'يوجد أكثر من مشترك باسم «{TENANT_NAME}»: {list(qs.values_list("id", flat=True))}.')
    t = qs.get()
    if not t.is_enterprise():
        raise SeedAbort('المشترك ليس بنسخة المؤسسات (multi_branch).')
    if Item.objects.filter(tenant=t).exists() or SaleInvoice.objects.filter(tenant=t).exists():
        raise SeedAbort('المشترك لديه منتجات أو فواتير بالفعل — يبدو أنه مُعبَّأ مسبقاً، لن يُنفَّذ شيء.')
    branches = list(Branch.objects.filter(tenant=t, is_active=True).order_by('id'))
    if not branches:
        raise SeedAbort('لا توجد فروع نشطة للمشترك.')
    S.tenant = t
    t.exchange_rate_updated_at = None   # في الذاكرة فقط (لا يُحفظ): يتفادى فحص «تغيّر سعر الصرف» مع التواريخ الافتراضية
    S.branches = branches
    S.hybrid = t.is_hybrid_purchasing()
    S.tz = zoneinfo.ZoneInfo(t.timezone or 'Africa/Khartoum')
    S.rng = random.Random(20261020)
    S.today = _REAL_NOW().astimezone(S.tz).date()
    S.start = S.today - dt.timedelta(days=HISTORY_DAYS - 1)
    S.open_day = S.start - dt.timedelta(days=1)
    # الفرع الرئيسي: اسمه يحوي «الخرطوم»، وإلا الأول
    main = next((b for b in branches if 'الخرطوم' in b.name), branches[0])
    S.main_branch = main
    log(f'المشترك #{t.pk} «{t.name}» | الفروع: {[b.name for b in branches]} | هجين={S.hybrid} | العملة={t.currency}'
        f' | عملة صعبة={t.hard_currency_mode} | المنطقة={t.timezone} | اليوم={S.today} | البداية={S.start}')


def count_snapshot():
    out = {}
    for model in django_apps.get_models():
        if any(f.name == 'tenant' for f in model._meta.fields):
            try:
                out[model._meta.label] = model.objects.filter(tenant=S.tenant).count() if hasattr(model.objects, 'filter') else 0
            except Exception:
                try:
                    out[model._meta.label] = model._base_manager.filter(tenant=S.tenant).count()
                except Exception:
                    pass
    # نماذج ترتبط بالمشترك عبر علاقة
    from apps.core.models import SupportMessage
    out['core.SupportMessage'] = SupportMessage.objects.filter(ticket__tenant=S.tenant).count()
    return out


def setup_users_and_accounts():
    from apps.accounts.models import User
    from apps.treasury.models import Treasury
    from apps.bank_accounts.models import BankAccount
    t = S.tenant
    users = list(User.objects.filter(tenant=t, is_active=True).order_by('id'))
    owner = next((u for u in users if u.is_tenant_admin), None) or (users[0] if users else None)
    S.owner = owner
    S.branch_user = {}
    for b in S.branches:
        cands = [u for u in users if u.branch_id == b.id]
        cands.sort(key=lambda u: (not u.is_branch_supervisor, u.id))
        S.branch_user[b.id] = cands[0] if cands else owner
    S.users = users or ([owner] if owner else [])

    # الخزائن (إعادة استعمال الموجود، وإنشاء الناقص فقط)
    cur = t.currency or ''
    S.main_tr = {}
    S.tr_extra = {}
    for b in S.branches:
        tr = Treasury.objects.filter(tenant=t, branch=b, is_hard_currency=False, is_head_office=False, is_active=True) \
            .order_by('-is_branch_main', 'id').first()
        if tr is None:
            tr = Treasury.objects.create(tenant=t, branch=b, name=f'خزينة {b.name}', code=f'TR-{b.code}', is_branch_main=True,
                                         is_active=True, currency=cur, created_by=S.owner)
        S.main_tr[b.id] = tr
        S.tr_extra[b.id] = Treasury.objects.create(
            tenant=t, branch=b, name=f'خزينة المناوبة المسائية - {b.name}', code=f'TR-EV-{b.code}', is_active=True,
            currency=cur, notes='تُسلَّم إليها عهدة المناوبة المسائية يومياً', created_by=S.owner)
    S.hq_tr = Treasury.objects.filter(tenant=t, is_head_office=True, is_hard_currency=False, is_active=True).order_by('id').first()
    if S.hq_tr is None:
        S.hq_tr = Treasury.objects.create(tenant=t, name='خزينة الإدارة الرئيسية', code='TR-HQ', is_head_office=True,
                                          is_active=True, currency=cur, created_by=S.owner)
    S.hq_hc_tr = Treasury.objects.filter(tenant=t, is_head_office=True, is_hard_currency=True, is_active=True).order_by('id').first()

    # الحسابات البنكية: حسابان للإدارة وحساب لكل فرع
    banks = [('بنك الخرطوم', 'حساب الإدارة الجاري'), ('بنك أم درمان الوطني', 'حساب الإدارة الاحتياطي')]
    S.hq_banks = []
    for i, (bank, nm) in enumerate(banks):
        S.hq_banks.append(BankAccount.objects.create(
            tenant=t, name=f'{bank} - {nm}', bank_name=bank, account_number=f'{random.Random(11 + i).randint(10**9, 10**10 - 1)}',
            iban='', is_head_office=True, is_default=(i == 0), is_active=True, currency=cur, created_by=S.owner))
    S.hq_bank = S.hq_banks[0]
    S.branch_bank = {}
    bank_names = ['بنك فيصل الإسلامي السوداني', 'بنك النيلين', 'بنك الخرطوم', 'بنك أم درمان الوطني']
    for i, b in enumerate(S.branches):
        bn = bank_names[i % len(bank_names)]
        S.branch_bank[b.id] = BankAccount.objects.create(
            tenant=t, branch=b, name=f'{bn} - {b.name}', bank_name=bn,
            account_number=f'{random.Random(31 + i).randint(10**9, 10**10 - 1)}', is_active=True, currency=cur,
            created_by=S.owner)


def setup_catalog_and_items():
    from apps.items.models import Category, Unit, Item, ItemUnit
    from apps.stocks.models import Stock
    from apps.catalog.models import MasterDrug
    from apps.catalog.matching import normalize_text, normalize_strength
    t = S.tenant
    rng = S.rng
    owner = S.owner

    # ── المخازن: مخزن لكل فرع + مخزن احتياطي للفرع الرئيسي + المركزي (هجين)
    S.stock_of = {}
    for b in S.branches:
        st = Stock.objects.filter(tenant=t, branch=b, is_central=False, is_active=True).order_by('-is_default', 'id').first()
        if st is None:
            st = Stock.objects.create(tenant=t, branch=b, name=f'مخزن {b.name}', is_active=True, is_default=True,
                                      address=b.address or '', created_by=owner)
            Stock.objects.filter(pk=st.pk).update(is_default=True)
        S.stock_of[b.id] = st
    reserve_name = f'مخزن الاحتياطي - {S.main_branch.name}'
    S.reserve_stock = Stock.objects.filter(tenant=t, name=reserve_name).first() or Stock.objects.create(
        tenant=t, branch=S.main_branch, name=reserve_name, is_active=True, is_default=False, created_by=owner)
    S.central_stock = None
    if S.hybrid:
        existing = Stock.objects.filter(tenant=t, is_central=True).first()
        S.central_stock = existing or Stock.objects.create(
            tenant=t, name='المخزن المركزي - الإدارة', is_central=True, is_active=True, is_default=False, created_by=owner)
        Stock.objects.filter(pk=S.central_stock.pk).update(is_default=False)

    # ── التصنيفات: أسماؤها الأشكال الدوائية الإنجليزية كما في الكتالوج المركزي
    S.item_rows = build_item_rows()
    S.cat = {}
    S.cat_by_form = {}
    for f in sorted({r[4] for r in S.item_rows}):
        S.cat_by_form[f] = Category.objects.create(tenant=t, name=f, created_by=owner)
    log(f'التصنيفات (من الكتالوج): {sorted(S.cat_by_form)}')

    # ── الوحدات
    S.units = {}
    for nm, ab in [('علبة', 'علبة'), ('زجاجة', 'زجاجة'), ('أنبوب', 'أنبوب'), ('عبوة', 'عبوة'), ('قطعة', 'قطعة'),
                   ('قلم', 'قلم'), ('كيس', 'كيس'), ('كرتونة', 'كرتونة')]:
        S.units[nm] = Unit.objects.create(tenant=t, name=nm, abbreviation=ab, created_by=owner)

    def unit_for(pack, form):
        for en, ar in (('Bottle', 'زجاجة'), ('Tube', 'أنبوب'), ('Pack', 'عبوة'), ('Pen', 'قلم'), ('Sachet', 'كيس')):
            if pack.startswith(en):
                return S.units[ar]
        return S.units['علبة']

    # ── الموردون المركزيون (قبل المنتجات ليرتبطوا بها)
    from apps.suppliers.models import Supplier
    S.central_suppliers = []
    if S.hybrid:
        for nm, city, cats in CENTRAL_SUPPLIERS:
            s = Supplier.objects.create(
                tenant=t, branch=None, name=nm, phone=phone(rng), city=city, address=f'سوق {city} — المنطقة الصناعية',
                credit_limit=D('25000000'), email='', created_by=owner,
                notes='مورد مركزي تديره الإدارة')
            s._cats = cats.split()
            S.central_suppliers.append(s)

    def central_supplier_for(cat_key, idx):
        cands = [s for s in S.central_suppliers if cat_key in s._cats]
        return cands[idx % len(cands)] if cands else None

    # ── المنتجات
    hc_on = bool(t.hard_currency_mode and t.exchange_rate and D(str(t.exchange_rate)) > 1)
    rate = D(str(t.exchange_rate)) if hc_on else None
    S.items = []
    S.item_by_key = {}
    barcodes = set()
    master_hits = 0
    for idx, row in enumerate(S.item_rows):
        (key, name, name_en, generic, form, strength, pack, catk, manu, origin, cost, ratio, pop, src, flags, step) = row[:16]
        extra = row[16] if len(row) > 16 else {}
        flags = flags.split()
        cost = D(str(cost))
        price = max(round_up(cost * D(str(ratio)), 50), round_up(cost * D('1.40'), 50))
        assert price >= cost * D('1.40'), key
        while True:
            bc = '62' + ''.join(str(rng.randint(0, 9)) for _ in range(11))
            if bc not in barcodes:
                barcodes.add(bc)
                break
        track = 'nb' not in flags
        unit = unit_for(pack, form)
        central = S.hybrid and src == 'c'
        sup = central_supplier_for(catk, idx) if central else None
        it = Item.objects.create(
            tenant=t, name=name, name_en=name_en, barcode=bc, item_type='product', category=S.cat_by_form[form],
            unit=unit, purchase_unit=unit, supplier=sup,
            cost_price=cost, selling_price=price, min_selling_price=round_up(cost * D('1.20'), 50),
            cost_price_hc=(cost / rate).quantize(D('0.0001')) if hc_on else None,
            selling_price_hc=(price / rate).quantize(D('0.0001')) if hc_on else None,
            min_selling_price_hc=(round_up(cost * D('1.20'), 50) / rate).quantize(D('0.0001')) if hc_on else None,
            tax_rate=D('0'), track_expiry=track, track_batch=track,
            generic_name=generic, manufacturer=manu, country_of_origin=origin, pack_size=pack, dosage_form=form,
            strength=strength, requires_prescription=('rx' in flags), is_insurance_excluded=('noins' in flags),
            sudan_agent=(extra.get('agent') or (sup.name if sup else '')), is_controlled_substance=bool(extra.get('controlled')), is_active=True, is_sellable=True, is_purchasable=True,
            created_by=owner)
        it._row = dict(key=key, cat=catk, pop=pop, src=('c' if central else 'l'), flags=flags, step=step, ratio=ratio)
        # قيم الكتالوج الرئيسي (إن وُجد تطابق) — قراءة فقط
        if extra.get('md') is not None:
            it.master_drug = extra['md']
            it.save(update_fields=['master_drug'])
            master_hits += 1
        elif generic:
            md = MasterDrug.objects.filter(generic_name_normalized=normalize_text(generic),
                                           dosage_form_normalized=normalize_text(form),
                                           strength_normalized=normalize_strength(strength), status='active').first()
            if md:
                it.master_drug = md
                it.save(update_fields=['master_drug'])
                master_hits += 1
        # وحدات المنتج (الوحدة الأصغر + كرتونة لبعض الأصناف السريعة)
        ItemUnit.objects.create(tenant=t, item=it, name=unit.name, factor=D('1'), created_by=owner)
        if pop >= 9 and 'nb' not in flags:
            it.has_multiple_units = True
            it.save(update_fields=['has_multiple_units'])
            ItemUnit.objects.create(tenant=t, item=it, name='كرتونة', factor=D(str(step if step >= 6 else 12)), created_by=owner)
        S.items.append(it)
        S.item_by_key[key] = it
    log(f'المنتجات: {len(S.items)} | مرتبط بالكتالوج الرئيسي: {master_hits}')

    # بدائل: نفس المادة الفعالة والتركيز والشكل
    groups = defaultdict(list)
    for it in S.items:
        if it.generic_name:
            groups[(it.generic_name, it.strength, it.dosage_form)].append(it)
    n_alt = 0
    for g in groups.values():
        if len(g) > 1:
            for a in g:
                a.alternatives.add(*[b for b in g if b.pk != a.pk])
            n_alt += len(g)
    log(f'بدائل مرتبطة: {n_alt} صنفاً')

    # ── موردو الفروع
    S.branch_suppliers = {}
    for bi, b in enumerate(S.branches):
        names = BRANCH_SUPPLIER_NAMES[bi % len(BRANCH_SUPPLIER_NAMES)]
        lst = []
        for nm in names:
            lst.append(Supplier.objects.create(
                tenant=t, branch=b, name=nm if bi < len(BRANCH_SUPPLIER_NAMES) else f'{nm} ({b.name})',
                phone=phone(rng), city=(b.name.split()[-1] if b.name else ''), credit_limit=D('6000000'),
                created_by=S.branch_user[b.id], notes='مورد محلي للفرع'))
        S.branch_suppliers[b.id] = lst


def setup_parties():
    from apps.customers.models import Customer
    from apps.insurance.models import InsuranceCompany, InsuranceMember
    from apps.agents.models import Agent
    from apps.employees.models import Employee
    t = S.tenant
    rng = S.rng
    S.cust = {}          # branch.id -> {'credit': [...], 'cash': [...]}
    S.cust_meta = {}
    S.agents = {}
    used_names = set()

    def person():
        while True:
            nm = f'{rng.choice(FIRST)} {rng.choice(FAMILY)} {rng.choice(FAMILY)}'
            if nm not in used_names:
                used_names.add(nm)
                return nm

    for bi, b in enumerate(S.branches):
        u = S.branch_user[b.id]
        credit, cash = [], []
        orgs = ORG_CUSTOMERS[:]
        rng.shuffle(orgs)
        for k in range(8):
            if k < 5:
                nm = orgs[k]
                if any(c.name == nm for c in sum((v['credit'] for v in S.cust.values()), [])):
                    nm = f'{nm} - {b.name}'
                limit = D(rng.choice([800000, 1200000, 1800000, 2500000]))
            else:
                nm = person()
                limit = D(rng.choice([150000, 250000, 400000]))
            c = Customer.objects.create(tenant=t, branch=b, name=nm, phone=phone(rng), city=b.name.split()[-1],
                                        address=f'{b.name} — حي {rng.choice(["الرياض", "المنشية", "الصافية", "الأزهري", "الجريف", "العمارات"])}',
                                        credit_limit=limit, created_by=u)
            c._pay_cycle = rng.randint(10, 28)
            credit.append(c)
        # بطيئان في السداد (لتظهر متأخرات واقعية دون تجاوز الحد)
        for c in credit[-2:]:
            c._pay_cycle = rng.randint(50, 64)
        for k in range(14):
            nm = person()
            cash.append(Customer.objects.create(tenant=t, branch=b, name=nm, phone=phone(rng), city=b.name.split()[-1],
                                                credit_limit=D('0'), created_by=u))
        S.cust[b.id] = {'credit': credit, 'cash': cash}
        for c in credit:
            S.cust_meta[c.id] = {'bal': D('0'), 'since': None, 'branch': b}
    # بوابة العميل: رموز لبعض العملاء
    for b in S.branches:
        for c in S.cust[b.id]['credit'][:2]:
            c.refresh_portal_token()

    # شركات التأمين والمشتركون
    S.insurers = []
    S.members = {b.id: [] for b in S.branches}
    for nm, pct, days in INSURERS:
        S.insurers.append(InsuranceCompany.objects.create(
            tenant=t, name=nm, contact_person=person(), phone=phone(rng), email='',
            address='الخرطوم — شارع النيل', default_coverage_percent=D(pct), settlement_period_days=days,
            created_by=S.owner))
    cardn = 1
    for b in S.branches:
        for k in range(14):
            comp = S.insurers[(cardn + k) % len(S.insurers)]
            S.members[b.id].append(InsuranceMember.objects.create(
                tenant=t, insurance_company=comp, card_number=f'HI-{2026}-{cardn:05d}', member_id=f'M{rng.randint(100000, 999999)}',
                full_name=person(), coverage_percent=(D(rng.choice([70, 80, 90])) if rng.random() < 0.3 else None),
                created_by=S.branch_user[b.id]))
            cardn += 1

    # المناديب
    specs = [('invoice', 'percentage', '1.5', '0'), ('collection', 'percentage', '2', '0'), ('both', 'percentage', '1', '1')]
    ai = 0
    for b in S.branches:
        lst = []
        for k in range(2 if b.id == S.main_branch.id else 1):
            basis, ctype, rate_, rate_c = specs[ai % 3]
            ai += 1
            lst.append(Agent.objects.create(
                tenant=t, branch=b, name=f'{rng.choice(FIRST)} {rng.choice(FAMILY)} (مندوب مبيعات)', phone=phone(rng),
                city=b.name.split()[-1], commission_type=ctype, commission_basis=basis,
                commission_rate=D(rate_), commission_rate_collection=D(rate_c), created_by=S.branch_user[b.id]))
        S.agents[b.id] = lst
    for b in S.branches:
        for c, ag in zip(S.cust[b.id]['credit'][:4], (S.agents[b.id] * 4)[:4]):
            S.cust_meta[c.id]['agent'] = ag

    # الموظفون (HR)
    S.employees = []
    plan_main = [('صيدلاني مسؤول', 'الصيدلية', 640000), ('مساعد صيدلاني', 'الصيدلية', 420000),
                 ('أمين صندوق', 'المبيعات', 320000), ('عامل مبيعات', 'المبيعات', 270000)]
    plan_other = [('صيدلاني مسؤول', 'الصيدلية', 600000), ('مساعد صيدلاني', 'الصيدلية', 380000), ('أمين صندوق', 'المبيعات', 290000)]
    for b in S.branches:
        for pos, dep, sal in (plan_main if b.id == S.main_branch.id else plan_other):
            S.employees.append(Employee.objects.create(
                tenant=t, branch=b, name=person(), phone=phone(rng), position=pos, department=dep, salary_type='fixed',
                base_salary=D(sal), hire_date=S.start - dt.timedelta(days=rng.randint(200, 1400)), created_by=S.branch_user[b.id]))
    for pos, dep, sal in [('محاسب الإدارة', 'الحسابات', 520000), ('مسؤول المشتريات', 'المشتريات', 450000)]:
        S.employees.append(Employee.objects.create(
            tenant=t, branch=None, name=person(), phone=phone(rng), position=pos, department=dep, salary_type='fixed',
            base_salary=D(sal), hire_date=S.start - dt.timedelta(days=rng.randint(300, 1500)), created_by=S.owner))

    # إعدادات متجر الفرع (تُورَّث من إعدادات النشاط؛ لا يُفعَّل المتجر)
    from apps.store.models import BranchStoreSettings
    for b in S.branches:
        BranchStoreSettings.objects.get_or_create(
            tenant=t, branch=b, defaults=dict(
                whatsapp=phone(rng), delivery_message=f'التوصيل متاح داخل {b.name.split()[-1]} خلال ساعتين من تأكيد الطلب',
                bank_details='بنك الخرطوم — حساب رقم 1234567890 — صيدلية انجاز', created_by=S.owner))

    # تصنيفات المصروفات
    from apps.expenses.models import ExpenseCategory
    S.exp_cat = {}
    for nm in EXPENSE_CATEGORIES:
        c, _ = ExpenseCategory.objects.get_or_create(tenant=t, name=nm, defaults={'created_by': S.owner})
        S.exp_cat[nm] = c


# ──────────────────────────────────────────────────────────────────────────
#  حالة المحاكاة: كميات، طلب متوقع، حاكم المشتريات
# ──────────────────────────────────────────────────────────────────────────
def qty_dist(it):
    p = it.selling_price
    if p > 40000:
        return [1], [1]
    if p < 3500:
        return [1, 2, 3, 4, 5, 6, 10], [35, 25, 12, 10, 6, 8, 4]
    return [1, 2, 3, 4, 6], [62, 22, 9, 4, 3]


def init_demand():
    rng = S.rng
    S.qdist = {it.id: qty_dist(it) for it in S.items}
    S.avgq = {it.id: sum(v * w for v, w in zip(*S.qdist[it.id])) / sum(S.qdist[it.id][1]) for it in S.items}
    S.w = {}
    S.lam = {}
    LINES = 2.31
    for b in S.branches:
        inv = (BASE_INVOICES_MAIN if b.id == S.main_branch.id else BASE_INVOICES_OTHER) * SCALE
        ws = {it.id: it._row['pop'] * rng.uniform(0.7, 1.35) for it in S.items}
        tot = sum(ws.values())
        S.w[b.id] = ws
        for it in S.items:
            S.lam[(b.id, it.id)] = max(inv * LINES * ws[it.id] / tot * S.avgq[it.id], 0.05)
    # شبه-محذوفة من إعادة الطلب في آخر أيام (مخزون منخفض واقعي)
    for it in S.items:
        avg = sum(S.lam[(b.id, it.id)] for b in S.branches) / len(S.branches)
        it.min_quantity = D(max(math.ceil(avg * 6), 2))
        it.save(update_fields=['min_quantity', 'updated_at'])
    S.starve = set()
    pops = sorted([it for it in S.items if it._row['src'] == 'c' and it._row['pop'] >= 6], key=lambda i: -i._row['pop'])
    for it in pops[2:7]:
        S.starve.add((S.main_branch.id, it.id))
    S.qty = {}
    for b in S.branches:
        for it in S.items:
            S.qty[(S.stock_of[b.id].id, it.id)] = D('0')
            S.qty[(S.reserve_stock.id, it.id)] = D('0')
    if S.central_stock:
        for it in S.items:
            S.qty[(S.central_stock.id, it.id)] = D('0')
    S.sales_mtd = defaultdict(D)       # (scope, month) -> مبيعات
    S.purch_mtd = defaultdict(D)       # (scope, month) -> مشتريات
    S.sales_day = defaultdict(D)       # (scope, date)
    S.purch_day = defaultdict(D)
    S.expense_total = D('0')
    S.cbal = {cid: D('0') for cid in S.cust_meta}
    S.sbal = defaultdict(D)
    S.invoices = []        # سجل الفواتير المؤكدة للمرتجعات والتتبع
    S.claims = []
    S.topups = []
    S.skipped = defaultdict(int)
    S.counters = defaultdict(int)
    S.events = defaultdict(list)
    S.cur_branch = None
    S.last_sale_day = {}


def schedule(day, hour, fn, label=''):
    if day <= S.today:
        S.events[day].append((hour, len(S.events[day]), fn, label))


def open_stocks():
    """رصيد افتتاحي بدفعات متعددة بتواريخ صلاحية مستقبلية (بلا فواتير شراء)."""
    from apps.stocks.models import StockQuantity
    from apps.items.models import ItemBatch
    from apps.sales.models import StockMovement
    t = S.tenant
    rng = S.rng
    day = S.open_day
    set_clock(day, 600)
    today = S.today
    n_batches = 0

    def put(stock, it, qty):
        nonlocal n_batches
        qty = D(int(qty))
        if qty <= 0:
            return
        sq = StockQuantity.objects.get(tenant=t, stock=stock, item=it)
        sq.quantity = qty
        sq.opening_quantity = qty
        sq.save(update_fields=['quantity', 'opening_quantity', 'updated_at'])
        StockMovement.objects.create(
            tenant=t, item=it, stock=stock, movement_type='opening_in', direction='in', quantity=qty, unit_cost=it.cost_price,
            movement_date=day, reference_type='opening_balance', reference_id=sq.id, balance_after=qty, notes='رصيد افتتاحي')
        S.qty[(stock.id, it.id)] = qty
        if it.track_batch:
            parts = rng.choice([1, 2, 2, 3])
            remaining = qty
            for k in range(parts):
                q = remaining if k == parts - 1 else D(max(1, int(qty * D(str(rng.uniform(0.25, 0.55))))))
                q = min(q, remaining)
                if q <= 0:
                    continue
                remaining -= q
                months = rng.randint(7, 26)
                exp = add_months(today, months) + dt.timedelta(days=rng.randint(0, 25))
                ItemBatch.objects.create(
                    tenant=t, item=it, stock=stock, batch_number=f'{(exp.year - 2) % 100:02d}{rng.randint(1, 12):02d}{it.id % 90 + 10}{chr(65 + rng.randint(0, 25))}',
                    expiry_date=exp, quantity_received=q, quantity_remaining=q, purchase_date=day, created_by=S.owner)
                n_batches += 1

    for b in S.branches:
        st = S.stock_of[b.id]
        for it in S.items:
            lam = S.lam[(b.id, it.id)]
            cover = 18 if it._row['src'] == 'l' else 16
            q = max(math.ceil(lam * cover), it._row['step'])
            put(st, it, round_up(q, it._row['step']))
    if S.central_stock:
        for it in S.items:
            if it._row['src'] != 'c':
                continue
            lam_tot = sum(S.lam[(b.id, it.id)] for b in S.branches)
            q = max(math.ceil(lam_tot * 36), it._row['step'] * 2)
            put(S.central_stock, it, round_up(q, it._row['step']))
    log(f'رصيد افتتاحي: {n_batches} دفعة')


def open_balances():
    """أرصدة افتتاحية للخزائن والحسابات البنكية."""
    from apps.treasury.services import set_opening_balance as tr_open
    from apps.bank_accounts.services import set_opening_balance as bk_open
    t = S.tenant
    set_clock(S.open_day, 540)
    tr_open(t, S.hq_tr, D('12000000'), S.open_day, user=S.owner)
    bk_open(t, S.hq_bank, D('15000000'), S.open_day, user=S.owner)
    bk_open(t, S.hq_banks[1], D('8000000'), S.open_day, user=S.owner)
    for b in S.branches:
        tr_open(t, S.main_tr[b.id], D('2500000'), S.open_day, user=S.branch_user[b.id])
        tr_open(t, S.tr_extra[b.id], D('300000'), S.open_day, user=S.branch_user[b.id])
        bk_open(t, S.branch_bank[b.id], D('3000000'), S.open_day, user=S.branch_user[b.id])


# ── تثبيت الخزينة الافتراضية للفرع أثناء عمليات الخدمات
def install_treasury_patch():
    import apps.treasury.services as ts
    S._orig_default_treasury = ts.get_or_create_default_treasury

    def patched(tenant, user=None):
        if S.cur_branch is not None:
            return S.main_tr[S.cur_branch.id]
        return S._orig_default_treasury(tenant, user=user)
    ts.get_or_create_default_treasury = patched
    S._ts = ts


def restore_treasury_patch():
    if getattr(S, '_ts', None):
        S._ts.get_or_create_default_treasury = S._orig_default_treasury


class in_branch:
    def __init__(self, b):
        self.b = b

    def __enter__(self):
        S.cur_branch = self.b

    def __exit__(self, *a):
        S.cur_branch = None


def tr_balance(tr):
    from apps.treasury.models import Treasury
    return Treasury.objects.get(pk=tr.pk).current_balance


def bank_balance(ba):
    from apps.bank_accounts.models import BankAccount
    return BankAccount.objects.get(pk=ba.pk).current_balance


def ensure_cash(tr, amount, who='خزينة'):
    """يضمن رصيداً كافياً قبل صرف؛ عند العجز (نادر) يُسجَّل إيداع إضافي من المالك."""
    from apps.treasury.services import post_treasury_receipt
    bal = tr_balance(tr)
    if bal < amount:
        top = round_up(amount - bal + D('200000'), 50000)
        post_treasury_receipt(tenant=S.tenant, amount=top, date=djtz.localdate(), reference_type='capital_injection',
                              description='إيداع إضافي من مالك النشاط لتغطية السيولة', user=S.owner, treasury=tr)
        S.topups.append((tr.name, top))


def ensure_bank(ba, amount):
    from apps.bank_accounts.services import post_bank_account_receipt
    bal = bank_balance(ba)
    if bal < amount:
        top = round_up(amount - bal + D('500000'), 100000)
        post_bank_account_receipt(tenant=S.tenant, amount=top, date=djtz.localdate(), reference_type='capital_injection',
                                  description='إيداع إضافي من مالك النشاط لتغطية السيولة', user=S.owner, bank_account=ba)
        S.topups.append((ba.name, top))


# ──────────────────────────────────────────────────────────────────────────
#  المبيعات
# ──────────────────────────────────────────────────────────────────────────
def weekday_factor(day):
    # الأسبوع بتقويم بايثون: الاثنين=0 … الجمعة=4، السبت=5، الأحد=6
    return {0: 1.00, 1: 0.98, 2: 1.00, 3: 1.14, 4: 0.62, 5: 0.86, 6: 1.04}[day.weekday()]


def trend_factor(day):
    k = (day - S.start).days / max(HISTORY_DAYS - 1, 1)
    f = 0.86 + 0.34 * k                 # صعود هادئ ~ +10% شهرياً
    if day.day <= 7:
        f *= 1.06                        # أول الشهر (صرف الرواتب)
    return f


def gen_lines(br, kind):
    rng = S.rng
    st = S.stock_of[br.id]
    n = rng.choices([1, 2, 3, 4, 5], [28, 34, 22, 11, 5])[0]
    pool = []
    weights = []
    for it in S.items:
        have = S.qty[(st.id, it.id)]
        if have <= 0:
            continue
        w = S.w[br.id][it.id]
        r = it._row
        if kind == 'insurance':
            if 'noins' in r['flags']:
                continue
            w *= 3 if ('rx' in r['flags'] or r['cat'] in ('chronic', 'antibiotic')) else 1
        if kind == 'credit' and r['cat'] in ('device', 'chronic', 'baby'):
            w *= 2
        pool.append(it)
        weights.append(w)
    if not pool:
        return []
    picks = []
    seen = set()
    for it in rng.choices(pool, weights, k=n * 3):
        if it.id in seen:
            continue
        seen.add(it.id)
        picks.append(it)
        if len(picks) >= n:
            break
    lines = []
    for it in picks:
        vals, wts = S.qdist[it.id]
        q = rng.choices(vals, wts)[0]
        if kind == 'credit':
            q = q * rng.choice([1, 1, 2, 2, 3])
        have = S.qty[(st.id, it.id)]
        q = min(D(q), have)
        if q <= 0:
            continue
        lines.append((it, D(int(q))))
    return lines


def make_sale(br, day, minute, kind):
    """ينشئ فاتورة بيع ويؤكدها عبر الخدمات. يعيد الفاتورة أو None عند التعذر."""
    from apps.sales.services import build_invoice_from_post, confirm_sale_invoice
    from django.db import transaction
    rng = S.rng
    st = S.stock_of[br.id]
    user = S.branch_user[br.id]
    lines = gen_lines(br, kind)
    if not lines:
        S.skipped['no_stock'] += 1
        return None
    subtotal = sum((q * it.selling_price for it, q in lines), D('0'))
    disc = D('0')
    if kind != 'insurance' and rng.random() < 0.06 and subtotal >= 10000:
        disc = round_down(subtotal * D(rng.choice(['0.02', '0.03'])), 50)
    total = subtotal - disc

    customer = None
    pm = 'cash'
    extra = {}
    meta = None
    if kind == 'cash_named':
        customer = rng.choice(S.cust[br.id]['cash'])
    elif kind == 'bank':
        pm = 'bank'
        extra.update(bank_account_id=S.branch_bank[br.id].id, bank_reference=f'BK{rng.randint(10**7, 10**8 - 1)}')
        if rng.random() < 0.35:
            customer = rng.choice(S.cust[br.id]['cash'])
    elif kind == 'credit':
        cands = S.cust[br.id]['credit']
        customer = rng.choice(cands)
        meta = S.cust_meta[customer.id]
        if S.cbal[customer.id] + total > customer.credit_limit * D('0.85'):
            S.skipped['credit_limit'] += 1
            return make_sale(br, day, minute, 'cash_walkin') if rng.random() < 0.7 else None
        pm = 'credit'
        extra['due_date'] = day + dt.timedelta(days=45)
        if meta.get('agent'):
            extra['agent_id'] = meta['agent'].id
    elif kind == 'mixed':
        pm = 'mixed'
        customer = rng.choice(S.cust[br.id]['cash'])
        extra.update(bank_account_id=S.branch_bank[br.id].id, bank_reference=f'BK{rng.randint(10**7, 10**8 - 1)}')
    elif kind == 'insurance':
        pm = 'mixed'
        member = rng.choice(S.members[br.id])
        extra.update(insurance_member_id=member.id, insurance_card_number=member.card_number)

    data = dict(invoice_date=day, payment_method=pm, delivery_type='immediate',
                invoice_discount_type='amount', invoice_discount_value=disc,
                customer_id=(customer.id if customer else None), **extra)
    lines_data = [dict(item_id=it.id, quantity=q, unit_price=it.selling_price, unit_id=None,
                       cost_price_snapshot=it.cost_price) for it, q in lines]
    set_clock(day, minute)
    try:
        with transaction.atomic(), in_branch(br):
            inv = build_invoice_from_post(S.tenant, st, data, lines_data, user)
            if kind == 'mixed':
                cash = round_down(inv.grand_total * D('0.55'), 50)
                inv.cash_amount = cash
                inv.bank_amount = inv.grand_total - cash
                inv.save(update_fields=['cash_amount', 'bank_amount', 'updated_at'])
            elif kind == 'insurance':
                inv.cash_amount = inv.grand_total - inv.insurance_amount
                inv.save(update_fields=['cash_amount', 'updated_at'])
            confirm_sale_invoice(inv, user)
            claim = None
            if kind == 'insurance':
                from apps.insurance.services import create_claim_from_sale
                claim = create_claim_from_sale(inv, S.tenant, None, user)
    except ValueError as e:
        S.skipped['sale_error'] += 1
        S.skipped_msgs = getattr(S, 'skipped_msgs', [])
        if len(S.skipped_msgs) < 5:
            S.skipped_msgs.append(f'{kind}: {e}')
        return None

    for it, q in lines:
        S.qty[(st.id, it.id)] -= q
    total = inv.grand_total
    for scope in ('all', br.id):
        S.sales_mtd[(scope, month_key(day))] += total
        S.sales_day[(scope, day)] += total
    if customer is not None and meta is not None:
        S.cbal[customer.id] += total
        if meta['since'] is None:
            meta['since'] = day
    S.counters['sales_' + kind] += 1
    S.invoices.append((inv.id, br.id, day, kind, customer.id if customer else None))
    if claim is not None:
        S.claims.append(claim)
        plan_claim(claim, inv, br, day)
    return inv


def plan_claim(claim, inv, br, day):
    """يجدول دورة حياة مطالبة التأمين حسب عمر الفاتورة."""
    rng = S.rng
    sub = day + dt.timedelta(days=rng.randint(1, 3))
    resp = sub + dt.timedelta(days=rng.randint(6, 20))
    pay1 = resp + dt.timedelta(days=rng.randint(7, 25))
    # حالات خاصة: رفض/اعتماد جزئي بنسب صغيرة
    r = rng.random()
    mode = 'full'
    if r < 0.05:
        mode = 'rejected' if claim.covered_amount < D('30000') else 'partial'
    elif r < 0.17:
        mode = 'partial'
    state = {'mode': mode}

    def do_submit():
        from apps.insurance.services import submit_claim
        from apps.insurance.models import InsuranceClaim
        c = InsuranceClaim.objects.get(pk=claim.pk)
        if c.status == 'draft':
            submit_claim(c, S.branch_user[br.id])

    def do_respond():
        from apps.insurance.services import record_claim_response
        from apps.insurance.models import InsuranceClaim
        c = InsuranceClaim.objects.get(pk=claim.pk)
        if c.status != 'submitted':
            return
        if state['mode'] == 'rejected':
            record_claim_response(c, D('0'), 'rejected', 'مستند ناقص — لا يغطيه عقد الشركة', S.owner)
        elif state['mode'] == 'partial':
            appr = round_down(c.covered_amount * D('0.93'), 50)
            record_claim_response(c, appr, 'partially_approved', 'استبعاد صنف خارج قائمة الشركة', S.owner)
        else:
            record_claim_response(c, c.covered_amount, 'approved', '', S.owner)

    def do_pay(frac):
        def _f():
            from apps.insurance.services import settle_claim_payment
            from apps.insurance.models import InsuranceClaim
            c = InsuranceClaim.objects.get(pk=claim.pk)
            if c.status not in ('approved', 'partially_approved', 'partially_paid'):
                return
            amt = c.remaining_amount if frac >= 1 else round_down(c.remaining_amount * D(str(frac)), 50)
            if amt <= 0:
                return
            method = rng.choice(['bank', 'bank', 'cash'])
            with in_branch(br):
                settle_claim_payment(c, amt, djtz.localdate(), f'INS-{rng.randint(10**5, 10**6 - 1)}', S.owner,
                                     treasury=S.main_tr[br.id], received_method=method)
        return _f

    schedule(sub, 11, do_submit, 'claim_submit')
    schedule(resp, 11, do_respond, 'claim_respond')
    if state['mode'] != 'rejected':
        if rng.random() < 0.3:
            schedule(pay1, 12, do_pay(0.6), 'claim_pay_partial')
            schedule(pay1 + dt.timedelta(days=rng.randint(8, 18)), 12, do_pay(1), 'claim_pay_rest')
        else:
            schedule(pay1, 12, do_pay(1), 'claim_pay')


def day_target(br, day):
    """هدف إيراد اليوم للفرع (يُولَّد من فواتير حتى بلوغه) — يضمن اتجاهاً شهرياً هادئاً بلا تذبذب تذاكر ضخمة."""
    base = TARGET_MAIN if br.id == S.main_branch.id else TARGET_OTHER
    tgt = base * SCALE * weekday_factor(day) * trend_factor(day) * S.rng.uniform(0.94, 1.06)
    if day == S.today:
        now_min = _REAL_NOW().astimezone(S.tz)
        frac = max(0.0, min(1.0, (now_min.hour * 60 + now_min.minute - 540) / 840))
        tgt *= frac
    return D(str(round(tgt)))


KIND_WEIGHTS = [('cash_walkin', 60), ('cash_named', 6), ('bank', 11), ('credit', 11), ('mixed', 3), ('insurance', 7)]


def run_sales_day(day):
    rng = S.rng
    kinds = [k for k, _ in KIND_WEIGHTS]
    weights = [w for _, w in KIND_WEIGHTS]
    for br in S.branches:
        target = day_target(br, day)
        if target < D('20000'):
            continue
        hi = 1380
        if day == S.today:
            nowl = _REAL_NOW().astimezone(S.tz)
            hi = max(nowl.hour * 60 + nowl.minute - 1, 560)
        est = int(target / D('56000')) + 4
        minutes = sorted(rng.randint(545, hi) for _ in range(est))
        cum = D('0')
        i = 0
        fails = 0
        while cum < target and fails < 25 and i < est * 3:
            mnt = minutes[i] if i < len(minutes) else min(hi, minutes[-1] + (i - len(minutes) + 1))
            inv = make_sale(br, day, mnt, rng.choices(kinds, weights)[0])
            if inv is None:
                fails += 1
            else:
                cum += inv.grand_total
            i += 1


def run_returns_day(day):
    from apps.sales.models import SaleReturn, SaleReturnLine, SaleInvoice, SaleInvoiceLine
    from apps.sales.services import confirm_sale_return
    rng = S.rng
    if day == S.today or not S.invoices:
        return
    for br in S.branches:
        base = BASE_INVOICES_MAIN if br.id == S.main_branch.id else BASE_INVOICES_OTHER
        if rng.random() > min(0.9, 0.016 * base * SCALE):
            continue
        cands = [x for x in S.invoices[-120:] if x[1] == br.id and x[3] in ('cash_walkin', 'cash_named', 'bank', 'credit')
                 and 1 <= (day - x[2]).days <= 12]
        if not cands:
            continue
        inv_id, _, _, kind, cust_id = rng.choice(cands)
        inv = SaleInvoice.objects.get(pk=inv_id)
        if inv.status not in ('confirmed', 'partially_returned'):
            continue
        line = inv.lines.filter(returned_quantity=0).order_by('?').first()
        if line is None:
            continue
        qty = D(1) if line.quantity > 1 else line.quantity
        refund = {'bank': 'bank', 'credit': 'balance'}.get(kind, 'cash')
        user = S.branch_user[br.id]
        set_clock(day, rng.randint(600, 1200))
        try:
            with transaction.atomic(), in_branch(br):
                sr = SaleReturn.objects.create(
                    tenant=S.tenant, original_invoice=inv, return_date=day, status='draft', refund_method=refund,
                    reason=rng.choice(['خطأ في الصرف', 'عبوة تالفة', 'تغيير الوصفة الطبية', 'عدم تحمّل الدواء', 'الكمية زائدة']),
                    created_by=user)
                SaleReturnLine.objects.create(tenant=S.tenant, sale_return=sr, invoice_line=line, item=line.item,
                                              returned_quantity=qty, unit_price=line.unit_price, created_by=user)
                confirm_sale_return(sr, user)
        except ValueError:
            S.skipped['return_error'] += 1
            continue
        st = S.stock_of[br.id]
        S.qty[(st.id, line.item_id)] += qty
        amt = qty * line.unit_price
        if refund == 'balance' and cust_id:
            S.cbal[cust_id] -= amt
        S.counters['sale_returns'] += 1


def run_collections_day(day):
    from apps.sales.services import record_customer_payment_allocated
    rng = S.rng
    for br in S.branches:
        user = S.branch_user[br.id]
        for c in S.cust[br.id]['credit']:
            meta = S.cust_meta[c.id]
            bal = S.cbal[c.id]
            if bal <= 0 or meta['since'] is None:
                continue
            age = (day - meta['since']).days
            due = age >= c._pay_cycle or bal > c.credit_limit * D('0.8')
            if not due or day == S.today:
                continue
            if rng.random() > 0.6:
                continue
            amount = bal if rng.random() < 0.65 else round_down(bal * D(str(rng.uniform(0.5, 0.9))), 50)
            if amount <= 0:
                continue
            method = 'cash' if rng.random() < 0.6 else 'bank'
            set_clock(day, rng.randint(650, 1150))
            try:
                with transaction.atomic(), in_branch(br):
                    record_customer_payment_allocated(
                        S.tenant, c, amount, method, day, reference=(f'TRF{rng.randint(10**6, 10**7 - 1)}' if method == 'bank' else ''),
                        notes='تحصيل دوري', user=user, treasury=S.main_tr[br.id] if method == 'cash' else None,
                        bank_account=S.branch_bank[br.id] if method == 'bank' else None)
            except ValueError:
                S.skipped['collection_error'] += 1
                continue
            S.cbal[c.id] -= amount
            meta['since'] = None if S.cbal[c.id] <= 50 else day - dt.timedelta(days=3)
            S.counters['customer_payments'] += 1


# ──────────────────────────────────────────────────────────────────────────
#  المشتريات والشحنات
# ──────────────────────────────────────────────────────────────────────────
def batch_no(it, day):
    return f'{day.year % 100:02d}{day.month:02d}{it.id % 90 + 10}{chr(65 + S.rng.randint(0, 25))}'


def month_gov(scope_list, amount_needed_fn, day):
    """سقف الإنفاق المسموح به الآن = CAP × مبيعات الشهر حتى الآن − مشتريات الشهر، لكل نطاق (الأضيق يفوز)."""
    m = month_key(day)
    allowed = None
    for scope in scope_list:
        a = D(str(CAP_PURCHASES)) * S.sales_mtd[(scope, m)] - S.purch_mtd[(scope, m)]
        allowed = a if allowed is None else min(allowed, a)
    return max(allowed or D('0'), D('0'))


def choose_pay(rng, supplier, total, central):
    r = rng.random()
    if central:
        return 'credit' if r < 0.55 else ('bank' if r < 0.90 else ('cash' if r < 0.95 else 'mixed'))
    return 'credit' if r < 0.45 else ('cash' if r < 0.80 else ('bank' if r < 0.93 else 'mixed'))


def make_purchase(stock, branch, supplier, day, minute, lines, user):
    """lines: [(item, qty)] — يبني أمر شراء ويؤكده عبر الخدمات. يعيد الفاتورة أو None."""
    from apps.purchases.services import build_purchase_from_post, confirm_purchase_invoice
    rng = S.rng
    central = branch is None
    total = sum((q * it.cost_price for it, q in lines), D('0'))
    pm = choose_pay(rng, supplier, total, central)
    # الحد الائتماني
    if pm in ('credit', 'mixed') and S.sbal[supplier.id] + total > supplier.credit_limit * D('0.85'):
        pm = 'bank' if central else 'cash'
    data = dict(supplier_id=supplier.id, invoice_date=day, payment_method=pm, notes='')
    if pm in ('bank', 'mixed'):
        ba = S.hq_bank if central else S.branch_bank[branch.id]
        data.update(bank_account_id=ba.id, bank_reference=f'TT{rng.randint(10**6, 10**7 - 1)}')
    lines_data = []
    for it, q in lines:
        ld = dict(item_id=it.id, quantity=q, unit_cost=it.cost_price, unit_id=None, tax_rate=0)
        if it.track_batch:
            ld['batch_number'] = batch_no(it, day)
            ld['expiry_date'] = add_months(day, rng.randint(11, 26)) + dt.timedelta(days=rng.randint(0, 20))
        lines_data.append(ld)
    set_clock(day, minute)
    try:
        with transaction.atomic(), in_branch(branch):
            inv = build_purchase_from_post(S.tenant, stock, data, lines_data, user)
            grand = inv.grand_total
            if pm == 'mixed':
                cash = round_down(grand * D('0.3'), 50)
                bank = round_down(grand * D('0.3'), 50)
                inv.cash_amount, inv.bank_amount = cash, bank
                inv.save(update_fields=['cash_amount', 'bank_amount', 'updated_at'])
                if cash:
                    ensure_cash(S.hq_tr if central else S.main_tr[branch.id], cash)
                if bank:
                    ensure_bank(S.hq_bank if central else S.branch_bank[branch.id], bank)
            elif pm == 'cash':
                ensure_cash(S.hq_tr if central else S.main_tr[branch.id], grand)
            elif pm == 'bank':
                ensure_bank(S.hq_bank if central else S.branch_bank[branch.id], grand)
            confirm_purchase_invoice(inv, user)
    except ValueError as e:
        S.skipped['purchase_error'] += 1
        S.skipped_msgs = getattr(S, 'skipped_msgs', [])
        if len(S.skipped_msgs) < 8:
            S.skipped_msgs.append(f'purchase: {e}')
        return None
    for it, q in lines:
        S.qty[(stock.id, it.id)] += q
    grand = inv.grand_total
    ledger_credit = grand if pm == 'credit' else (grand - inv.cash_amount - inv.bank_amount if pm == 'mixed' else D('0'))
    # الرصيد المحسوب في دفتر المورد: الفاتورة كاملة ثم السداد؛ نتتبع الصافي
    S.sbal[supplier.id] += ledger_credit
    m = month_key(day)
    S.purch_mtd[('all', m)] += grand
    S.purch_day[('all', day)] += grand
    if branch is not None:
        S.purch_mtd[(branch.id, m)] += grand
        S.purch_day[(branch.id, day)] += grand
    S.counters['central_purchases' if central else 'branch_purchases'] += 1
    S.purchases.append((inv.id, supplier.id, stock.id, branch.id if branch else None, day))
    return inv


def reorder_lines(stock, demand_fn, items, reorder_cover, target_cover, avail_fn=None):
    """يعيد [(item, qty, cover)] للأصناف تحت حد إعادة الطلب."""
    out = []
    for it in items:
        lam = demand_fn(it)
        have = S.qty[(stock.id, it.id)]
        cover = float(have) / lam if lam > 0 else 99
        if cover < reorder_cover or have <= it.min_quantity:
            need = lam * target_cover - float(have)
            step = it._row['step']
            q = round_up(max(need, step), step)
            if avail_fn is not None:
                q = min(q, avail_fn(it))
                q = round_down(q, 1)
            if q > 0:
                out.append((it, D(q), cover))
    out.sort(key=lambda x: x[2])
    return out


def trim_to_budget(lines, budget):
    """يقلّص الأسطر (الأحوج أولاً) لتبقى التكلفة ≤ budget."""
    out = []
    spent = D('0')
    for it, q, cov in lines:
        cost = q * it.cost_price
        if spent + cost <= budget:
            out.append((it, q))
            spent += cost
        else:
            left = budget - spent
            step = it._row['step']
            qq = round_down(left / it.cost_price, step) if left > 0 else D('0')
            if qq >= step:
                out.append((it, qq))
                spent += qq * it.cost_price
    return out


def run_central_purchases(day):
    if not S.hybrid or day.weekday() != 1:           # الثلاثاء
        return
    if (S.today - day).days < NO_PURCHASE_LAST_DAYS:
        return
    items = [it for it in S.items if it._row['src'] == 'c']
    lam_c = lambda it: sum(S.lam[(b.id, it.id)] for b in S.branches) * trend_factor(day)
    for sup in S.central_suppliers:
        sit = [it for it in items if it.supplier_id == sup.id]
        lines = reorder_lines(S.central_stock, lam_c, sit, reorder_cover=34, target_cover=44)
        if not lines:
            continue
        budget = month_gov(['all'], None, day)
        lines = trim_to_budget(lines, budget)
        if not lines:
            S.skipped['central_budget'] += 1
            continue
        make_purchase(S.central_stock, None, sup, day, 505 + sup.id % 30, lines, S.owner)


def run_branch_purchases(day):
    if day.weekday() not in (0, 3):                  # الاثنين والخميس
        return
    if (S.today - day).days < NO_PURCHASE_LAST_DAYS:
        return
    for br in S.branches:
        st = S.stock_of[br.id]
        user = S.branch_user[br.id]
        items = [it for it in S.items if it._row['src'] == 'l']
        lam_b = lambda it: S.lam[(br.id, it.id)] * trend_factor(day)
        lines = reorder_lines(st, lam_b, items, reorder_cover=13, target_cover=30)
        if not lines:
            continue
        sups = S.branch_suppliers[br.id]
        by_sup = defaultdict(list)
        for ln in lines:
            by_sup[sups[ln[0].id % len(sups)]].append(ln)
        for sup, ls in by_sup.items():
            budget = month_gov([br.id, 'all'], None, day)
            ls2 = trim_to_budget(ls, budget)
            if not ls2:
                S.skipped['branch_budget'] += 1
                continue
            make_purchase(st, br, sup, day, 515 + (sup.id % 25), ls2, user)


def run_shipments(day):
    from apps.stocks.shipment_services import create_shipment, send_shipment, receive_shipment
    rng = S.rng
    if not S.hybrid or day.weekday() not in (6, 2):  # الأحد والأربعاء
        return
    items = [it for it in S.items if it._row['src'] == 'c']
    for br in S.branches:
        st = S.stock_of[br.id]
        lam_b = lambda it: S.lam[(br.id, it.id)] * trend_factor(day)
        starving = (S.today - day).days <= 9
        its = [it for it in items if not (starving and (br.id, it.id) in S.starve)]
        lines = reorder_lines(st, lam_b, its, reorder_cover=11, target_cover=22,
                              avail_fn=lambda it: S.qty[(S.central_stock.id, it.id)])
        lines = [(it, q) for it, q, c in lines if q > 0]
        if not lines:
            continue
        set_clock(day, 600)
        try:
            with transaction.atomic():
                ship = create_shipment(S.tenant, S.central_stock, st, day, 'توزيع دوري من المخزن المركزي',
                                       [{'item': it, 'quantity': q} for it, q in lines], user=S.owner)
                send_shipment(ship, user=S.owner)
        except ValueError as e:
            S.skipped['shipment_error'] += 1
            continue
        for it, q in lines:
            S.qty[(S.central_stock.id, it.id)] -= q
        last_day = (S.today - day).days <= 0
        # آخر شحنة قبل اليوم تبقى «في الطريق»
        if last_day or (S.pending_ship is None and (S.today - day).days <= 4 and br.id == S.branches[-1].id):
            S.pending_ship = ship.id
            S.counters['shipments_in_transit'] += 1
            continue
        recv_day = day if rng.random() < 0.7 else day + dt.timedelta(days=1)
        if recv_day > S.today:
            recv_day = day
        short_line = None
        diff = {}
        if rng.random() < 0.10 and len(lines) > 1:
            it, q = rng.choice([l for l in lines if l[1] >= 6] or lines)
            if q >= 2:
                short_line = (it, D(1))
        set_clock(recv_day, 900)
        try:
            with transaction.atomic():
                if short_line:
                    sl = ship.lines.get(item=short_line[0])
                    diff[sl.id] = sl.quantity_sent - short_line[1]
                receive_shipment(ship, diff, user=S.branch_user[br.id])
        except ValueError:
            S.skipped['shipment_recv_error'] += 1
            continue
        for it, q in lines:
            got = q - (short_line[1] if short_line and short_line[0].id == it.id else D('0'))
            S.qty[(st.id, it.id)] += got
            if got < q:
                S.qty[(S.central_stock.id, it.id)] += (q - got)
        S.counters['shipments_received'] += 1
        if short_line:
            S.counters['shipments_with_difference'] += 1


# ──────────────────────────────────────────────────────────────────────────
#  السداد، الخزائن، المصروفات، الرواتب
# ──────────────────────────────────────────────────────────────────────────
def pay_supplier(supplier, amount, day, minute, method, treasury=None, bank=None, user=None, note=''):
    """سداد مورد (يطابق منطق supplier_payment_create_api مسار العملة المحلية)."""
    from apps.purchases.services import _apply_supplier_ledger
    from apps.treasury.services import post_treasury_disbursement
    from apps.bank_accounts.services import post_bank_account_disbursement
    t = S.tenant
    set_clock(day, minute)
    amount = D(str(amount))
    hc_mode = bool(t.hard_currency_mode)
    hc_amt = hc_cur = hc_rate = None
    if hc_mode and t.exchange_rate and D(str(t.exchange_rate)) > 0:
        hc_rate = D(str(t.exchange_rate))
        hc_amt = -(amount / hc_rate).quantize(D('0.01'))
        hc_cur = t.hard_currency or ''
    ref_type = 'supplier_payment_bank' if method == 'bank' else 'supplier_payment_cash'
    with transaction.atomic():
        entry = _apply_supplier_ledger(tenant=t, supplier=supplier, amount=-amount, entry_type='payment',
                                       reference_type=ref_type, reference_id=None, date=day,
                                       notes=note or 'سداد مورد', hc_amount=hc_amt, hc_currency=hc_cur or '',
                                       hc_exchange_rate=hc_rate)
        if method == 'cash':
            ensure_cash(treasury, amount)
            post_treasury_disbursement(tenant=t, amount=amount, date=day, reference_type=ref_type, reference_id=entry.id,
                                       description=f'دفعة مورد {supplier.name}', user=user, treasury=treasury)
        else:
            ensure_bank(bank, amount)
            post_bank_account_disbursement(tenant=t, amount=amount, date=day, reference_type=ref_type, reference_id=entry.id,
                                           description=f'دفعة مورد {supplier.name}', user=user, bank_account=bank)
    S.sbal[supplier.id] -= amount
    S.counters['supplier_payments'] += 1


def run_supplier_payments(day):
    rng = S.rng
    if day.weekday() != 2 or day == S.today:     # الأربعاء
        return
    pools = []
    for sup in S.central_suppliers:
        pools.append((sup, None))
    for br in S.branches:
        for sup in S.branch_suppliers[br.id]:
            pools.append((sup, br))
    for sup, br in pools:
        bal = S.sbal[sup.id]
        if bal <= 0:
            continue
        # يُسدَّد ما يزيد عن آخر أسبوع شراء تقريباً
        recent = sum((p_total for p_total in [S.purch_day.get(('all' if br is None else br.id, day - dt.timedelta(days=k)), D('0')) for k in range(0, 7)]), D('0'))
        keep = D('0') if br is not None else D('0')
        if rng.random() > 0.55:
            continue
        amount = round_down(bal * D(str(rng.uniform(0.6, 1.0))), 50)
        if amount < 20000:
            continue
        if br is None:
            method = 'bank' if rng.random() < 0.85 else 'cash'
            pay_supplier(sup, amount, day, 840, method, treasury=S.hq_tr, bank=S.hq_bank, user=S.owner,
                         note='سداد دفعة من الحساب')
        else:
            method = 'cash' if rng.random() < 0.6 else 'bank'
            pay_supplier(sup, amount, day, 840, method, treasury=S.main_tr[br.id], bank=S.branch_bank[br.id],
                         user=S.branch_user[br.id], note='سداد دفعة من الحساب')


def run_treasury_ops(day):
    """إيداعات نقدية بالبنك، تسليم الفروع للإدارة، تحويلات بين الحسابات."""
    from apps.bank_accounts.services import post_treasury_to_bank_transfer, post_bank_account_transfer
    from apps.branch_transfers.services import create_transfer, receive_transfer
    from apps.treasury.services import post_treasury_transfer
    from apps.branch_transfers.models import BranchTransfer
    t = S.tenant
    rng = S.rng
    wd = day.weekday()
    # الخميس: الفروع تسلّم فائض النقد للإدارة (يُستلم في اليوم التالي) وإيداع بنكي
    if wd == 3 and day != S.today:
        for br in S.branches:
            user = S.branch_user[br.id]
            tr = S.main_tr[br.id]
            bal = tr_balance(tr)
            amt = round_down(bal - D('2200000'), 100000)
            if amt >= 500000:
                set_clock(day, 1260)
                with in_branch(br):
                    xfer = create_transfer(t, user, direction='to_head_office', branch=br, source_kind='cash', source=tr,
                                           dest_kind='cash', dest=S.hq_tr, amount=amt, transfer_date=day,
                                           notes='تسليم فائض النقد الأسبوعي للإدارة')
                recv_day = day + dt.timedelta(days=1)
                if recv_day <= S.today and (S.today - day).days > 1:
                    set_clock(recv_day, 600)
                    receive_transfer(xfer, amt, S.owner)
                else:
                    S.pending_branch_transfer = xfer.id
                S.counters['branch_cash_remits'] += 1
            # مبالغ البطاقات/التحويلات بالحساب البنكي للفرع → حساب الإدارة (كل أسبوعين)
            bb = S.branch_bank[br.id]
            bbal = bank_balance(bb)
            if day.isocalendar()[1] % 2 == 0 and bbal - D('1500000') >= 500000:
                amt2 = round_down(bbal - D('1500000'), 100000)
                set_clock(day, 1270)
                x2 = create_transfer(t, user, direction='to_head_office', branch=br, source_kind='bank', source=bb,
                                     dest_kind='bank', dest=S.hq_bank, amount=amt2, transfer_date=day,
                                     notes='تحويل رصيد التحصيل البنكي إلى حساب الإدارة')
                recv_day = day + dt.timedelta(days=1)
                if recv_day <= S.today and (S.today - day).days > 1:
                    set_clock(recv_day, 610)
                    receive_transfer(x2, amt2, S.owner)
                S.counters['branch_bank_remits'] += 1
    # السبت: إيداع نقدي من خزينة الإدارة في البنك + تمويل الفروع عند الحاجة
    if wd == 5 and day != S.today:
        bal = tr_balance(S.hq_tr)
        amt = round_down(bal - D('3500000'), 100000)
        if amt >= 500000:
            set_clock(day, 780)
            post_treasury_to_bank_transfer(t, S.hq_tr, S.hq_bank, amt, day, 'إيداع نقدي بالبنك', S.owner)
            S.counters['treasury_to_bank'] += 1
        for br in S.branches:
            tr = S.main_tr[br.id]
            bal2 = tr_balance(tr)
            if bal2 > D('1800000'):
                dep = round_down(bal2 - D('1500000'), 50000)
                if dep >= 300000:
                    set_clock(day, 790)
                    with in_branch(br):
                        post_treasury_to_bank_transfer(t, tr, S.branch_bank[br.id], dep, day, 'إيداع نقدي بالبنك', S.branch_user[br.id])
                    S.counters['treasury_to_bank'] += 1
    # أول الشهر: عهدة المناوبة المسائية بين خزينتي الفرع + تحويل بين حسابي الإدارة
    if day.day == 2:
        for br in S.branches:
            set_clock(day, 700)
            ensure_cash(S.main_tr[br.id], D('150000'))
            post_treasury_transfer(t, S.main_tr[br.id], S.tr_extra[br.id], D('150000'), D('150000'), D('1'), day,
                                   'عهدة المناوبة المسائية', S.branch_user[br.id])
            S.counters['treasury_transfers'] += 1
        set_clock(day, 720)
        ensure_bank(S.hq_bank, D('1000000'))
        post_bank_account_transfer(t, S.hq_bank, S.hq_banks[1], D('1000000'), D('1000000'), D('1'), day,
                                   'تحويل بين حسابي الإدارة', S.owner)
        S.counters['bank_transfers'] += 1
    # تحويل عملة (إن وُجدت خزينة عملة صعبة للإدارة): مرتان في الفترة
    if S.hq_hc_tr is not None and t.exchange_rate and D(str(t.exchange_rate)) > 1 and day in (S.today - dt.timedelta(days=25), S.today - dt.timedelta(days=10)):
        rate = D(str(t.exchange_rate))
        sdg = D('2600000')
        usd = (sdg / rate).quantize(D('0.01'))
        set_clock(day, 800)
        ensure_cash(S.hq_tr, sdg)
        post_treasury_transfer(t, S.hq_tr, S.hq_hc_tr, sdg, usd, (usd / sdg).quantize(D('0.0001')), day,
                               'تحويل عملة لسداد مستحقات الاستيراد', S.owner)
        S.counters['fx_treasury_transfers'] += 1
    # تمويل الإدارة للفرع الأصغر في أول فترة (إدارة → فرع) مرة واحدة
    if day == S.start + dt.timedelta(days=20) and len(S.branches) > 1:
        br = [b for b in S.branches if b.id != S.main_branch.id][0]
        set_clock(day, 700)
        ensure_cash(S.hq_tr, D('800000'))
        x = create_transfer(t, S.owner, direction='to_branch', branch=br, source_kind='cash', source=S.hq_tr,
                            dest_kind='cash', dest=S.main_tr[br.id], amount=D('800000'), transfer_date=day,
                            notes='تمويل رأس مال تشغيل للفرع')
        set_clock(day + dt.timedelta(days=1), 640)
        receive_transfer(x, D('800000'), S.branch_user[br.id])
        S.counters['branch_fundings'] += 1


def new_expense(branch, cat, desc, amount, day, minute, method='cash', draft=False, cancel=False):
    from apps.expenses.models import Expense
    from apps.expenses.services import confirm_expense, cancel_expense
    amount = D(str(int(round(float(amount), -2))))
    user = S.branch_user[branch.id] if branch else S.owner
    if branch is not None:
        tr, ba = S.main_tr[branch.id], S.branch_bank[branch.id]
    else:
        tr, ba = S.hq_tr, S.hq_bank
    set_clock(day, minute)
    exp = Expense(tenant=S.tenant, category=S.exp_cat[cat], description=desc, amount=amount, expense_date=day,
                  payment_method=method, treasury=(tr if method == 'cash' else None),
                  bank_account=(ba if method == 'bank' else None), branch=branch, notes='', created_by=user, updated_by=user)
    exp.save()
    if draft:
        S.counters['expenses_draft'] += 1
        return exp
    if method == 'cash':
        ensure_cash(tr, amount)
    else:
        ensure_bank(ba, amount)
    with in_branch(branch):
        confirm_expense(exp, user)
    S.expense_total += amount
    S.counters['expenses'] += 1
    if cancel:
        cancel_expense(exp, user)
        S.expense_total -= amount
        S.counters['expenses_cancelled'] += 1
    return exp


def run_expenses(day):
    rng = S.rng
    if day == S.today:
        return
    for br in S.branches:
        f = 1.0 if br.id == S.main_branch.id else 0.7
        d = day.day
        if d == 1:
            new_expense(br, 'إيجار', f'إيجار {br.name} — {day.month}/{day.year}', 450000 * f, day, 600, 'bank')
        if d == 6:
            new_expense(br, 'كهرباء ومياه', 'فاتورة الكهرباء', round(150000 * f * rng.uniform(0.9, 1.15), -2), day, 700)
            new_expense(br, 'كهرباء ومياه', 'فاتورة المياه', round(22000 * f * rng.uniform(0.9, 1.1), -2), day, 705)
        if d == 10:
            new_expense(br, 'اتصالات وإنترنت', 'اشتراك الإنترنت والاتصالات', 60000 * f, day, 710, 'bank')
        if d == 12:
            new_expense(br, 'نظافة وصيانة', 'نظافة وصيانة دورية', round(72000 * f * rng.uniform(0.9, 1.2), -2), day, 720)
        if d == 18:
            new_expense(br, 'قرطاسية ومطبوعات', 'أوراق فواتير ورولات طباعة', round(36000 * f * rng.uniform(0.8, 1.2), -2), day, 730)
        if d == 22 and rng.random() < 0.7:
            new_expense(br, 'دعاية وإعلان', 'إعلان ممول على مواقع التواصل', round(70000 * f, -2), day, 740, 'bank')
        if d == 25 and rng.random() < 0.6:
            new_expense(br, 'ضيافة', 'ضيافة وموارد للعاملين', round(45000 * f, -2), day, 750)
        if day.weekday() == 3:
            new_expense(br, 'مواصلات وتوصيل', 'توصيل طلبات المنازل ومواصلات', round(50000 * f * rng.uniform(0.85, 1.2), -2), day, 760)
    # الإدارة
    if day.day == 1:
        new_expense(None, 'إيجار', f'إيجار مكتب الإدارة — {day.month}/{day.year}', 270000, day, 610, 'bank')
    if day.day == 8:
        new_expense(None, 'اشتراكات برامج', 'اشتراك نظام إدارة الصيدليات والدعم', 160000, day, 620, 'bank')
    if day.day == 10:
        new_expense(None, 'اتصالات وإنترنت', 'إنترنت ومكالمات الإدارة', 45000, day, 625, 'bank')
    if day.day == 16:
        new_expense(None, 'مواصلات وتوصيل', 'مواصلات الإدارة وزيارات الفروع', round(54000 * rng.uniform(0.9, 1.2), -2), day, 630)
    if day.day == 20 and day.month % 3 == 0:
        new_expense(None, 'رسوم وتراخيص', 'رسوم تجديد ترخيص مزاولة المهنة', 200000, day, 640, 'bank')


def run_hr(day):
    """سلف، حوافز، رواتب شهرية."""
    from apps.employees.services import create_advance, create_incentive, create_salary_payment
    from apps.employees.models import EmployeeAdvance, EmployeeIncentive
    rng = S.rng
    if day == S.today:
        return
    emps = S.employees
    # سلف
    if day.day in (9, 14) and rng.random() < 0.5:
        emp = rng.choice(emps)
        br = next((b for b in S.branches if b.id == emp.branch_id), None)
        amt = D(rng.choice([50000, 80000, 100000, 120000]))
        tr = S.main_tr[br.id] if br else S.hq_tr
        set_clock(day, 800)
        ensure_cash(tr, amt)
        create_advance(S.tenant, emp, amt, day, 'cash', treasury=tr, notes='سلفة على الراتب', user=S.branch_user[br.id] if br else S.owner)
        S.counters['advances'] += 1
    # حوافز وخصومات (يوم 20 من كل شهر: حافز فوري في الأشهر الفردية وخصم مع الراتب في الزوجية)
    if day.day == 20:
        emp = rng.choice(emps)
        br = next((b for b in S.branches if b.id == emp.branch_id), None)
        tr = S.main_tr[br.id] if br else S.hq_tr
        set_clock(day, 810)
        if day.month % 2 == 1:
            amt = D(rng.choice([30000, 40000, 50000]))
            ensure_cash(tr, amt)
            create_incentive(S.tenant, emp, amt, 'حافز تحقيق هدف المبيعات', 'bonus', 'immediate', 'cash', treasury=tr, date=day,
                             user=S.owner)
        else:
            create_incentive(S.tenant, emp, D(rng.choice([10000, 15000])), 'خصم تأخير عن الدوام', 'deduction', 'with_salary',
                             'cash', treasury=tr, date=day, user=S.owner)
        S.counters['incentives'] += 1
    # الراتب: يوم 27 (أو آخر الشهر إن كان أقصر)
    last = month_last_day(day.year, day.month)
    if day.day == min(27, last):
        ps = day.replace(day=1)
        pe = day.replace(day=last)
        if ps < S.start - dt.timedelta(days=31):
            return      # راتب شهر سابق لبداية النظام يُصرف خارجه
        for emp in emps:
            br = next((b for b in S.branches if b.id == emp.branch_id), None)
            tr = S.main_tr[br.id] if br else S.hq_tr
            user = S.branch_user[br.id] if br else S.owner
            adv_ids = list(EmployeeAdvance.objects.filter(tenant=S.tenant, employee=emp, status='pending').values_list('id', flat=True))
            inc_ids = list(EmployeeIncentive.objects.filter(tenant=S.tenant, employee=emp, status='pending', payout='with_salary').values_list('id', flat=True))
            set_clock(day, 870)
            method = 'cash'
            bank = None
            if rng.random() < 0.35:
                method = 'bank'
                bank = S.branch_bank[br.id] if br else S.hq_bank
            net_est = emp.base_salary
            if method == 'cash':
                ensure_cash(tr, net_est)
            else:
                ensure_bank(bank, net_est)
            with in_branch(br):
                sp = create_salary_payment(
                    S.tenant, emp, ps, pe, emp.base_salary, method, treasury=(tr if method == 'cash' else None),
                    bank_account=bank, bank_reference=(f'SAL{rng.randint(10**5, 10**6 - 1)}' if bank else ''),
                    notes=f'راتب {ps.month}/{ps.year}', deductions_notes='', advance_ids=adv_ids, incentive_ids=inc_ids, user=user)
                sp.pay()
            S.counters['salaries'] += 1
            S.salary_total += sp.total_due


def run_agents(day):
    from apps.agents.services import _apply_agent_ledger
    from apps.agents.models import AgentLedger
    from apps.treasury.services import post_treasury_disbursement
    rng = S.rng
    if day.day != 26 or day == S.today:
        return
    for br in S.branches:
        for ag in S.agents[br.id]:
            bal = AgentLedger.objects.filter(tenant=S.tenant, agent=ag).aggregate(s=Sum('amount'))['s'] or D('0')
            if bal < 20000:
                continue
            amt = round_down(bal * D('0.9'), 50)
            tr = S.main_tr[br.id]
            set_clock(day, 880)
            ensure_cash(tr, amt)
            with transaction.atomic():
                entry = _apply_agent_ledger(S.tenant, ag, -amt, 'payment', 'agent_payment_cash', None, day, 'دفعة عمولات شهرية')
                post_treasury_disbursement(tenant=S.tenant, amount=amt, date=day, reference_type='agent_payment_cash',
                                           reference_id=entry.id, description=f'دفعة مندوب {ag.name}', user=S.branch_user[br.id],
                                           treasury=tr)
            S.counters['agent_payments'] += 1


# ──────────────────────────────────────────────────────────────────────────
#  أحداث متفرقة داخل الفترة (جرد، إتلاف، تحويلات مخزون، عروض، طلبات…)
# ──────────────────────────────────────────────────────────────────────────
def ev_stocktake(br, days_ago, confirm=True):
    from apps.stocks.models import Stocktake, StocktakeLine
    from apps.stocks.services import confirm_stocktake
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)
    st = S.stock_of[br.id]
    user = S.branch_user[br.id]

    def _f():
        set_clock(day, 1000)
        with transaction.atomic():
            sk = Stocktake.objects.create(tenant=S.tenant, stock=st, stocktake_date=day, status='draft',
                                          notes='جرد دوري شامل للأصناف سريعة الحركة', created_by=user)
            its = rng.sample(S.items, 35)
            diffs = {}
            for it in its:
                sysq = S.qty[(st.id, it.id)]
                cnt = sysq
                if confirm and rng.random() < 0.18 and sysq >= 3:
                    dq = D(rng.choice([-2, -1, -1, 1]))
                    cnt = sysq + dq
                    diffs[it.id] = dq
                StocktakeLine.objects.create(tenant=S.tenant, stocktake=sk, item=it, system_quantity=sysq, counted_quantity=cnt,
                                             notes=('فرق جرد' if it.id in diffs else ''), created_by=user)
            if confirm:
                confirm_stocktake(sk)
                for iid, dq in diffs.items():
                    S.qty[(st.id, iid)] += dq
        S.counters['stocktakes_confirmed' if confirm else 'stocktakes_draft'] += 1
    schedule(day, 16, _f, 'stocktake')


def ev_destruction(br, days_ago, reason, confirm=True):
    from apps.stocks.models import StockDestruction, StockDestructionLine
    from apps.stocks.services import confirm_stock_destruction
    from apps.items.models import ItemBatch
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)
    st = S.stock_of[br.id]
    user = S.branch_user[br.id]

    def _f():
        set_clock(day, 1010)
        batches = list(ItemBatch.objects.filter(tenant=S.tenant, stock=st, quantity_remaining__gte=6,
                                                item__cost_price__lt=5000).order_by('?')[:3])
        if not batches:
            return
        with transaction.atomic():
            de = StockDestruction.objects.create(
                tenant=S.tenant, stock=st, destruction_date=day, status='draft', reason=reason,
                witness_name='لجنة الجرد بالفرع', reference_number=f'محضر إتلاف {day:%Y%m%d}',
                notes=('عبوات تالفة أثناء النقل والتخزين' if reason == 'damaged' else 'سحب دفعة بقرار من الجهة المصنِّعة'),
                created_by=user)
            for b in batches:
                q = D(rng.choice([2, 3, 4]))
                StockDestructionLine.objects.create(
                    tenant=S.tenant, destruction=de, item=b.item, batch=b, batch_number_snapshot=b.batch_number,
                    expiry_date_snapshot=b.expiry_date, quantity=q, unit_cost_snapshot=b.item.cost_price, created_by=user)
            if confirm:
                confirm_stock_destruction(de, user)
                for ln in de.lines.all():
                    S.qty[(st.id, ln.item_id)] -= ln.quantity
        S.counters['destructions_confirmed' if confirm else 'destructions_draft'] += 1
    schedule(day, 16, _f, 'destruction')


def ev_internal_transfer(days_ago):
    """تحويل داخل الفرع الرئيسي من المخزن الرئيسي إلى مخزن الاحتياطي."""
    from apps.stocks.models import StockTransfer, StockTransferLine
    from apps.stocks.services import confirm_stock_transfer
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)
    br = S.main_branch
    src = S.stock_of[br.id]

    def _f():
        set_clock(day, 1030)
        its = [it for it in S.items if S.qty[(src.id, it.id)] >= 30 and it._row['src'] == 'c'][:8]
        if not its:
            return
        with transaction.atomic():
            tr = StockTransfer.objects.create(tenant=S.tenant, transfer_date=day, from_stock=src, to_stock=S.reserve_stock,
                                              status='draft', notes='نقل مخزون احتياطي إلى مخزن الاحتياطي', created_by=S.branch_user[br.id])
            for it in its:
                StockTransferLine.objects.create(tenant=S.tenant, transfer=tr, item=it, quantity=D(int(S.qty[(src.id, it.id)] // 6) or 1),
                                                 created_by=S.branch_user[br.id])
            confirm_stock_transfer(tr)
            for ln in tr.lines.all():
                S.qty[(src.id, ln.item_id)] -= ln.quantity
                S.qty[(S.reserve_stock.id, ln.item_id)] += ln.quantity
        S.counters['stock_transfers_internal'] += 1
    schedule(day, 17, _f, 'internal_transfer')


def ev_inter_branch_transfer(days_ago, from_b, to_b, mode):
    """mode: received | short_restock | in_transit | draft"""
    from apps.stocks.models import StockTransfer, StockTransferLine
    from apps.stocks.branch_transfer_services import send_branch_transfer, receive_branch_transfer, settle_branch_transfer_shortage
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)
    src, dst = S.stock_of[from_b.id], S.stock_of[to_b.id]

    def _f():
        set_clock(day, 1040)
        its = [it for it in S.items if S.qty[(src.id, it.id)] >= 25][:6]
        if len(its) < 2:
            return
        fu, tu = S.branch_user[from_b.id], S.branch_user[to_b.id]
        with transaction.atomic():
            tr = StockTransfer.objects.create(tenant=S.tenant, transfer_date=day, from_stock=src, to_stock=dst, status='draft',
                                              is_inter_branch=True, notes='تحويل بين الفروع لتغطية نقص', created_by=fu)
            for it in its:
                StockTransferLine.objects.create(tenant=S.tenant, transfer=tr, item=it, quantity=D(int(S.qty[(src.id, it.id)] // 5) or 1),
                                                 created_by=fu)
            if mode == 'draft':
                S.counters['stock_transfers_draft'] += 1
                return
            send_branch_transfer(tr, fu)
            qtys = {ln.item_id: ln.quantity for ln in tr.lines.all()}
            for iid, q in qtys.items():
                S.qty[(src.id, iid)] -= q
            if mode == 'in_transit':
                S.counters['stock_transfers_in_transit'] += 1
                return
            set_clock(day + dt.timedelta(days=1) if day < S.today else day, 800)
            recv = {}
            short = {}
            if mode == 'short_restock':
                ln0 = tr.lines.first()
                recv[ln0.id] = ln0.quantity - 1
                short[ln0.item_id] = D(1)
            receive_branch_transfer(tr, recv, tu)
            for iid, q in qtys.items():
                S.qty[(dst.id, iid)] += q - short.get(iid, D('0'))
            if mode == 'short_restock':
                set_clock(day + dt.timedelta(days=2) if day + dt.timedelta(days=2) <= S.today else day, 800)
                settle_branch_transfer_shortage(tr, 'restock', fu)
                for iid, q in short.items():
                    S.qty[(src.id, iid)] += q
        S.counters['stock_transfers_' + mode] += 1
    schedule(day, 17, _f, 'inter_branch_transfer')


def ev_quote(days_ago, br, flow):
    from apps.sales.services import (build_quote_from_post, mark_quote_sent, mark_quote_accepted, mark_quote_rejected,
                                     cancel_sale_quote, convert_quote_to_invoice, confirm_sale_invoice)
    from apps.sales.models import SaleQuote
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)
    user = S.branch_user[br.id]

    def _f():
        set_clock(day, 780)
        cust = rng.choice(S.cust[br.id]['credit'])
        st_ = S.stock_of[br.id]
        pool = [it for it in S.items if S.qty[(st_.id, it.id)] >= 12] or S.items
        its = rng.sample(pool, 4)
        lines = [dict(item_id=it.id,
                      quantity=D(min(rng.choice([2, 3, 5, 10]), max(int(S.qty[(st_.id, it.id)] // 3), 1))) if it.selling_price < 20000 else D(1),
                      unit_price=it.selling_price, discount_percent=D('0')) for it in its]
        with transaction.atomic():
            q = build_quote_from_post(S.tenant, user, dict(stock_id=S.stock_of[br.id].id, customer_id=cust.id, quote_date=day,
                                                          expiry_date=day + dt.timedelta(days=14), terms='الأسعار سارية لمدة 14 يوماً',
                                                          notes='عرض سعر لتوريد دوري', quote_discount_type='amount',
                                                          quote_discount_value=D('0')), lines)
            if flow in ('sent', 'accepted', 'rejected', 'converted', 'converted_confirmed', 'expired'):
                mark_quote_sent(q, user)
            if flow in ('accepted', 'converted', 'converted_confirmed'):
                mark_quote_accepted(q, user)
            if flow == 'rejected':
                mark_quote_rejected(q, user)
            if flow == 'cancelled':
                cancel_sale_quote(q, user)
            if flow == 'expired':
                SaleQuote.objects.filter(pk=q.pk).update(status='expired')
            if flow in ('converted', 'converted_confirmed'):
                inv = convert_quote_to_invoice(q, user, payment_method='cash')
                inv.refresh_from_db()
                inv.tenant = S.tenant
                if flow == 'converted_confirmed':
                    inv.tenant = S.tenant
                    with in_branch(br):
                        confirm_sale_invoice(inv, user)
                    for ln in inv.lines.all():
                        S.qty[(S.stock_of[br.id].id, ln.item_id)] -= ln.quantity
                    for scope in ('all', br.id):
                        S.sales_mtd[(scope, month_key(day))] += inv.grand_total
        S.counters['quotes_' + flow] += 1
    schedule(day, 13, _f, 'quote')


def ev_rfq(days_ago, flow):
    from apps.purchases.models import PurchaseRFQ, PurchaseRFQLine, PurchaseInvoice
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)

    def _f():
        set_clock(day, 790)
        sup = rng.choice(S.central_suppliers or sum(S.branch_suppliers.values(), []))
        stock = S.central_stock or S.stock_of[S.main_branch.id]
        its = [it for it in S.items if it.supplier_id == sup.id][:5] or S.items[:5]
        with transaction.atomic():
            rfq = PurchaseRFQ.objects.create(
                tenant=S.tenant, rfq_date=day, expiry_date=day + dt.timedelta(days=10), supplier=sup, stock=stock,
                status='draft', notes='طلب عرض سعر لتوريد شهري', terms='الدفع آجل 30 يوماً — التسليم خلال أسبوع',
                created_by=S.owner)
            for it in its:
                PurchaseRFQLine.objects.create(tenant=S.tenant, rfq=rfq, item=it, requested_quantity=D(rng.choice([24, 48, 96])),
                                               quoted_price=(it.cost_price if flow != 'draft' and flow != 'sent' else D('0')) ,
                                               created_by=S.owner)
            rfq.recalculate_total()
            status = {'draft': 'draft', 'sent': 'sent', 'received': 'received', 'accepted': 'accepted',
                      'rejected': 'rejected', 'converted': 'converted'}[flow]
            upd = {'status': status}
            if flow == 'converted':
                inv = PurchaseInvoice.objects.filter(tenant=S.tenant, stock=stock, source_rfq__isnull=True, status='confirmed') \
                    .order_by('-invoice_date').first()
                if inv:
                    upd['converted_invoice'] = inv
                else:
                    upd['status'] = 'accepted'
            for k, v in upd.items():
                setattr(rfq, k, v)
            rfq.save(update_fields=list(upd.keys()) + ['updated_at'])
        S.counters['rfqs_' + flow] += 1
    schedule(day, 13, _f, 'rfq')


def ev_purchase_return(days_ago):
    from apps.purchases.models import PurchaseInvoice, PurchaseReturn, PurchaseReturnLine
    from apps.purchases.services import confirm_purchase_return
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)

    def _f():
        set_clock(day, 820)
        cands = [p for p in S.purchases if (day - p[4]).days >= 3]
        if not cands:
            return
        pid, sup_id, stock_id, br_id, _ = rng.choice(cands)
        inv = PurchaseInvoice.objects.get(pk=pid)
        line = inv.lines.filter(item__cost_price__lt=30000).order_by('?').first()
        if not line or S.qty[(inv.stock_id, line.item_id)] < 4:
            return
        qty = D(2)
        refund = 'balance' if inv.payment_method == 'credit' else ('bank' if inv.payment_method == 'bank' else 'cash')
        if refund == 'bank' and not inv.bank_account_id:
            refund = 'balance'
        user = S.branch_user[br_id] if br_id else S.owner
        branch = next((b for b in S.branches if b.id == br_id), None)
        with transaction.atomic(), in_branch(branch):
            pr = PurchaseReturn.objects.create(tenant=S.tenant, return_date=day, original_invoice=inv, status='draft',
                                               refund_method=refund, reason='عبوات تالفة عند الاستلام / قرب انتهاء الصلاحية', created_by=user)
            PurchaseReturnLine.objects.create(tenant=S.tenant, purchase_return=pr, invoice_line=line, item=line.item,
                                              returned_quantity=qty, unit_cost=line.unit_cost, created_by=user)
            confirm_purchase_return(pr, user)
        S.qty[(inv.stock_id, line.item_id)] -= qty
        if inv.supplier_id:
            S.sbal[inv.supplier_id] -= qty * line.unit_cost
        S.counters['purchase_returns'] += 1
    schedule(day, 14, _f, 'purchase_return')


def ev_online_order(days_ago, br, flow):
    from apps.store.models import StoreSettings
    from apps.store.services import place_order, approve_order, reject_order
    from apps.sales.services import confirm_sale_invoice
    rng = S.rng
    day = S.today - dt.timedelta(days=days_ago)

    def _f():
        set_clock(day, 1100)
        store = StoreSettings.objects.filter(tenant=S.tenant).first()
        if store is None:
            store = StoreSettings.objects.create(tenant=S.tenant, display_name=S.tenant.name, is_enabled=False)
        st = S.stock_of[br.id]
        its = [it for it in S.items if S.qty[(st.id, it.id)] >= 10 and 'rx' not in it._row['flags']][:60]
        picks = rng.sample(its, 3)
        cart = []
        for it in picks:
            q = D(rng.choice([1, 2]))
            cart.append({'item': it, 'qty': q, 'unit_price': it.selling_price, 'line_total': (it.selling_price * q)})
        pm = 'bank' if flow in ('approved_confirmed', 'pending') else 'credit'
        with transaction.atomic():
            order = place_order(store, cart, dict(name=f'{rng.choice(FIRST)} {rng.choice(FAMILY)}', phone=phone(rng),
                                                  address=f'{br.name} — حي {rng.choice(["الرياض", "المنشية", "الصافية"])}',
                                                  notes='رجاء التوصيل مساءً', payment_method=pm), branch=br)
            if flow == 'rejected':
                reject_order(order)
            elif flow in ('approved', 'approved_confirmed'):
                set_clock(day, 1160)
                inv = approve_order(order, S.branch_user[br.id])
                if flow == 'approved_confirmed':
                    inv.bank_account = S.branch_bank[br.id]
                    inv.bank_reference = f'BK{rng.randint(10**7, 10**8 - 1)}'
                    inv.save(update_fields=['bank_account', 'bank_reference', 'updated_at'])
                    inv.tenant = S.tenant
                    with in_branch(br):
                        confirm_sale_invoice(inv, S.branch_user[br.id])
                    for ln in inv.lines.all():
                        S.qty[(st.id, ln.item_id)] -= ln.quantity
                    for scope in ('all', br.id):
                        S.sales_mtd[(scope, month_key(day))] += inv.grand_total
        S.counters['online_orders_' + flow] += 1
    schedule(day, 18, _f, 'online_order')


def ev_agent_requests():
    from apps.agents.models import AgentInvoiceRequest, AgentInvoiceRequestLine
    from apps.notifications.models import Notification
    rng = S.rng
    for i, (flow, days_ago) in enumerate([('pending', 1), ('pending', 3), ('approved', 12), ('rejected', 20)]):
        br = S.branches[i % len(S.branches)]
        ag = S.agents[br.id][0]
        day = S.today - dt.timedelta(days=days_ago)

        def _f(br=br, ag=ag, flow=flow, day=day):
            set_clock(day, 700)
            cust = rng.choice(S.cust[br.id]['credit'])
            its = rng.sample(S.items, 3)
            with transaction.atomic():
                rq = AgentInvoiceRequest.objects.create(
                    tenant=S.tenant, agent=ag, customer=cust, customer_name=cust.name, customer_phone=cust.phone,
                    notes='طلب توريد أسبوعي', status='pending', created_by=S.owner)
                tot = D('0')
                for it in its:
                    q = D(rng.choice([2, 4, 6]))
                    AgentInvoiceRequestLine.objects.create(tenant=S.tenant, request=rq, item=it, quantity=q, unit_price=it.selling_price,
                                                           created_by=S.owner)
                    tot += q * it.selling_price
                rq.subtotal = rq.total_amount = tot
                if flow == 'approved':
                    from apps.sales.models import SaleInvoice
                    inv = SaleInvoice.objects.filter(tenant=S.tenant, agent=ag, status__in=('confirmed', 'partially_returned'),
                                                     agent_request__isnull=True).order_by('-invoice_date').first()
                    if inv:
                        rq.status = 'approved'
                        rq.sale_invoice = inv
                        rq.admin_comment = 'تم اعتماد الطلب وإصدار الفاتورة'
                if flow == 'rejected':
                    rq.status = 'rejected'
                    rq.admin_comment = 'الكمية المطلوبة غير متوفرة حالياً'
                rq.save()
                if rq.status == 'pending':
                    Notification.objects.create(
                        tenant=S.tenant, branch=br, notification_type='agent_request', priority='medium',
                        title=f'طلب مندوب جديد: {rq.request_number}',
                        message=f'المندوب {ag.name} أرسل طلب فاتورة للعميل {cust.name} بقيمة {tot:,.2f}.',
                        link=f'/agents/requests/{rq.pk}/')
            S.counters['agent_requests_' + flow] += 1
        schedule(day, 9, _f, 'agent_request')


def ev_return_shipment(days_ago):
    """مرتجع من الفرع إلى المخزن المركزي (يؤكد استلامه الإدارة)."""
    from apps.stocks.shipment_services import create_shipment, send_shipment, receive_shipment
    day = S.today - dt.timedelta(days=days_ago)
    br = S.branches[0]
    st = S.stock_of[br.id]

    def _f():
        set_clock(day, 1050)
        its = [it for it in S.items if it._row['src'] == 'c' and S.qty[(st.id, it.id)] >= 14 and it.track_batch][:3]
        if not its:
            return
        with transaction.atomic():
            ship = create_shipment(S.tenant, st, S.central_stock, day, 'مرتجع دفعة قريبة الانتهاء إلى المخزن المركزي',
                                   [{'item': it, 'quantity': D(4)} for it in its], user=S.branch_user[br.id], direction='to_central')
            send_shipment(ship, user=S.branch_user[br.id])
            set_clock(day + dt.timedelta(days=1), 640)
            receive_shipment(ship, {}, user=S.owner)
        for it in its:
            S.qty[(st.id, it.id)] -= D(4)
            S.qty[(S.central_stock.id, it.id)] += D(4)
        S.counters['shipments_returned_to_central'] += 1
    schedule(day, 17, _f, 'return_shipment')


def plan_period_events():
    if S.hybrid and S.central_stock:
        ev_return_shipment(14)
    """جدولة الأحداث المتفرقة على الأيام."""
    # جرد: الفرع الأول مكتمل قبل 24 يوماً، والثاني قبل 14، ويوجد جرد جارٍ (مسودة) اليوم
    for i, br in enumerate(S.branches):
        ev_stocktake(br, 24 - 10 * i, confirm=True)
    ev_stocktake(S.main_branch, 2, confirm=False)
    ev_destruction(S.main_branch, 18, 'damaged', True)
    if len(S.branches) > 1:
        ev_destruction([b for b in S.branches if b.id != S.main_branch.id][0], 11, 'recalled', True)
    ev_destruction(S.main_branch, 1, 'damaged', False)
    ev_internal_transfer(21)
    if len(S.branches) > 1:
        other = [b for b in S.branches if b.id != S.main_branch.id][0]
        ev_inter_branch_transfer(27, other, S.main_branch, 'received')
        ev_inter_branch_transfer(15, S.main_branch, other, 'short_restock')
        ev_inter_branch_transfer(2, other, S.main_branch, 'in_transit')
        ev_inter_branch_transfer(0, S.main_branch, other, 'draft')
    for days_ago, flow in [(28, 'converted_confirmed'), (24, 'accepted'), (20, 'rejected'), (16, 'cancelled'), (14, 'converted'),
                           (12, 'expired'), (6, 'sent'), (1, 'draft')]:
        ev_quote(days_ago, S.branches[days_ago % len(S.branches)], flow)
    for days_ago, flow in [(26, 'converted'), (21, 'accepted'), (17, 'rejected'), (14, 'received'), (6, 'sent'), (2, 'draft')]:
        ev_rfq(days_ago, flow)
    for days_ago in (24, 11):
        ev_purchase_return(days_ago)
    for days_ago, flow in [(16, 'approved_confirmed'), (9, 'approved'), (8, 'rejected'), (2, 'pending'), (1, 'pending'), (0, 'pending')]:
        ev_online_order(days_ago, S.branches[days_ago % len(S.branches)], flow)
    ev_agent_requests()
    # شراء مسودة (لم يُؤكَّد) في آخر يوم
    # تُنشأ في finalize_pending()


# ──────────────────────────────────────────────────────────────────────────
#  حلقة الأيام
# ──────────────────────────────────────────────────────────────────────────
def run_days():
    day = S.start
    n = 0
    t0 = _REAL_NOW()
    while day <= S.today:
        # أحداث مجدولة لليوم (مرتّبة بالساعة)
        evs = sorted(S.events.get(day, []), key=lambda e: (e[0], e[1]))
        early = [e for e in evs if e[0] < 9]
        late = [e for e in evs if e[0] >= 9]
        for e in early:
            e[2]()
        run_central_purchases(day)
        run_branch_purchases(day)
        run_shipments(day)
        for e in late:
            e[2]()
        run_sales_day(day)
        run_returns_day(day)
        run_collections_day(day)
        run_expenses(day)
        run_hr(day)
        run_agents(day)
        run_supplier_payments(day)
        run_treasury_ops(day)
        n += 1
        if n % 10 == 0:
            el = (_REAL_NOW() - t0).total_seconds()
            log(f'  … {day} ({n}/{HISTORY_DAYS}) فواتير بيع: {sum(v for k, v in S.counters.items() if k.startswith("sales_"))} | {el:.0f} ث')
        day += dt.timedelta(days=1)


# ──────────────────────────────────────────────────────────────────────────
#  حالات معلّقة وبيانات نهاية الفترة
# ──────────────────────────────────────────────────────────────────────────
def finalize_pending():
    from apps.sales.services import build_invoice_from_post, confirm_sale_invoice
    from apps.purchases.services import build_purchase_from_post
    from apps.stocks.shipment_services import create_shipment
    from apps.expenses.models import Expense
    from apps.sales.models import SaleReturn, SaleReturnLine, SaleInvoice
    rng = S.rng
    day = S.today
    # مسودات فواتير بيع (لم تؤكد)
    for br in S.branches:
        user = S.branch_user[br.id]
        st = S.stock_of[br.id]
        for k in range(2):
            lines = gen_lines(br, 'cash_walkin')
            if not lines:
                continue
            set_clock(day, 700 + k * 40)
            with in_branch(br):
                build_invoice_from_post(S.tenant, st, dict(invoice_date=day, payment_method='cash'),
                                        [dict(item_id=it.id, quantity=q, unit_price=it.selling_price, unit_id=None,
                                              cost_price_snapshot=it.cost_price) for it, q in lines], user)
            S.counters['sales_draft'] += 1
    # فاتورة بتسليم لاحق (قيد التسليم) — تحجز المخزون
    br = S.main_branch
    lines = gen_lines(br, 'cash_walkin')[:2]
    if lines:
        set_clock(day, 720)
        user = S.branch_user[br.id]
        with transaction.atomic(), in_branch(br):
            inv = build_invoice_from_post(S.tenant, S.stock_of[br.id],
                                          dict(invoice_date=day, payment_method='cash', delivery_type='deferred',
                                               customer_id=S.cust[br.id]['cash'][0].id, notes='طلب يُسلَّم غداً'),
                                          [dict(item_id=it.id, quantity=q, unit_price=it.selling_price, unit_id=None,
                                                cost_price_snapshot=it.cost_price) for it, q in lines], user)
            confirm_sale_invoice(inv, user)
        for it, q in lines:
            S.qty_reserved[(S.stock_of[br.id].id, it.id)] += q
        for scope in ('all', br.id):
            S.sales_mtd[(scope, month_key(day))] += inv.grand_total
        S.counters['sales_pending_delivery'] += 1
    # مسودات شراء
    set_clock(day, 740)
    for br_ in [None, S.main_branch]:
        stock = S.central_stock if (br_ is None and S.central_stock) else S.stock_of[S.main_branch.id]
        sup = S.central_suppliers[0] if (br_ is None and S.central_suppliers) else S.branch_suppliers[S.main_branch.id][0]
        its = [it for it in S.items if (it._row['src'] == ('c' if br_ is None and S.central_stock else 'l'))][:4]
        if not its:
            continue
        build_purchase_from_post(S.tenant, stock, dict(supplier_id=sup.id, invoice_date=day, payment_method='credit'),
                                 [dict(item_id=it.id, quantity=D(it._row['step'] * 2), unit_cost=it.cost_price, unit_id=None,
                                       tax_rate=0) for it in its], S.owner if br_ is None else S.branch_user[S.main_branch.id])
        S.counters['purchases_draft'] += 1
    # مصروفات: مسودتان + مصروف ملغى
    new_expense(S.main_branch, 'صيانة' if 'صيانة' in S.exp_cat else 'نظافة وصيانة', 'صيانة مكيف قسم المخزن', 45000, day, 745, draft=True)
    new_expense(None, 'رسوم وتراخيص', 'رسوم تسجيل وكالة جديدة (قيد الموافقة)', 80000, day, 750, draft=True)
    new_expense(S.main_branch, 'ضيافة', 'ضيافة زيارة مفتشي الوزارة (أُلغي — مدفوع من جهة أخرى)', 18000, day - dt.timedelta(days=9), 800, cancel=True)
    # شحنة مسودة من المركزي
    if S.hybrid and S.central_stock:
        br = S.branches[0]
        its = [it for it in S.items if it._row['src'] == 'c' and S.qty[(S.central_stock.id, it.id)] >= 20][:5]
        if its:
            set_clock(day, 760)
            create_shipment(S.tenant, S.central_stock, S.stock_of[br.id], day, 'شحنة قيد التجهيز',
                            [{'item': it, 'quantity': D(6)} for it in its], user=S.owner)
            S.counters['shipments_draft'] += 1
    # شحنة في الطريق وتحويل مالي في الطريق (إن لم تتولد طبيعياً)
    if S.hybrid and S.central_stock and S.pending_ship is None:
        from apps.stocks.shipment_services import send_shipment
        br = S.branches[-1]
        its = [it for it in S.items if it._row['src'] == 'c' and S.qty[(S.central_stock.id, it.id)] >= 40][:6]
        if its:
            set_clock(day, 780)
            ship = create_shipment(S.tenant, S.central_stock, S.stock_of[br.id], day, 'شحنة أسبوعية — في الطريق',
                                   [{'item': it, 'quantity': D(12)} for it in its], user=S.owner)
            send_shipment(ship, user=S.owner)
            for it in its:
                S.qty[(S.central_stock.id, it.id)] -= D(12)
            S.pending_ship = ship.id
            S.counters['shipments_in_transit'] += 1
    if S.pending_branch_transfer is None and len(S.branches) > 0:
        from apps.branch_transfers.services import create_transfer
        br = S.branches[0]
        set_clock(day, 790)
        ensure_cash(S.main_tr[br.id], D('300000'))
        create_transfer(S.tenant, S.branch_user[br.id], direction='to_head_office', branch=br, source_kind='cash',
                        source=S.main_tr[br.id], dest_kind='cash', dest=S.hq_tr, amount=D('300000'), transfer_date=day,
                        notes='فائض نقدي — في الطريق للإدارة')
        S.counters['branch_cash_remits_in_transit'] += 1
    # كشوف رواتب الشهر الحالي (مسودة) لم تُصرف بعد
    from apps.employees.services import create_salary_payment
    last_d = month_last_day(day.year, day.month)
    if day.day < min(27, last_d):
        for emp in S.employees:
            br_ = next((b for b in S.branches if b.id == emp.branch_id), None)
            set_clock(day, 790)
            create_salary_payment(S.tenant, emp, day.replace(day=1), day.replace(day=last_d), emp.base_salary, 'cash',
                                  treasury=(S.main_tr[br_.id] if br_ else S.hq_tr), notes=f'راتب {day.month}/{day.year} — مسودة',
                                  user=(S.branch_user[br_.id] if br_ else S.owner))
        S.counters['salaries_draft'] += len(S.employees)
    # مرتجع بيع مسودة
    cands = [x for x in S.invoices if x[3] in ('cash_walkin', 'cash_named') and (S.today - x[2]).days <= 2]
    if cands:
        inv = SaleInvoice.objects.get(pk=cands[-1][0])
        line = inv.lines.first()
        if inv.status == 'confirmed' and line:
            set_clock(day, 770)
            sr = SaleReturn.objects.create(tenant=S.tenant, original_invoice=inv, return_date=day, status='draft',
                                           refund_method='cash', reason='العميل غيّر رأيه — بانتظار اعتماد المدير',
                                           created_by=S.branch_user[inv.branch_id] if inv.branch_id in S.branch_user else S.owner)
            SaleReturnLine.objects.create(tenant=S.tenant, sale_return=sr, invoice_line=line, item=line.item,
                                          returned_quantity=D(1), unit_price=line.unit_price, created_by=sr.created_by)
            S.counters['sale_returns_draft'] += 1


def adjust_near_expiry():
    """يجعل بعض الدفعات قريبة الانتهاء (للتنبيهات)، من الأصناف بطيئة الحركة فقط."""
    from apps.items.models import ItemBatch
    rng = S.rng
    qs = ItemBatch.objects.filter(tenant=S.tenant, quantity_remaining__gte=4, stock__is_central=False,
                                  item__selling_price__lt=60000).order_by('?')
    cnt = 0
    seen = set()
    for b in qs:
        if (b.stock_id, b.item_id) in seen:
            continue
        # أقدم دفعة للصنف فقط حتى لا يختلّ ترتيب FEFO
        oldest = ItemBatch.objects.filter(tenant=S.tenant, stock=b.stock, item=b.item, quantity_remaining__gt=0).order_by('expiry_date').first()
        if oldest is None or oldest.pk != b.pk:
            continue
        seen.add((b.stock_id, b.item_id))
        b.expiry_date = S.today + dt.timedelta(days=rng.randint(14, 58))
        b.notes = 'دفعة قريبة الانتهاء — تُصرف أولاً'
        b.save(update_fields=['expiry_date', 'notes'])
        cnt += 1
        if cnt >= 14:
            break
    return cnt


def post_activity_and_notifications():
    from apps.accounts.models import UserActivity
    from apps.notifications.models import Notification
    from apps.notifications import services as ns
    from apps.core.models import SupportTicket, SupportMessage
    rng = S.rng
    # نشاط المستخدمين (سجل الأنشطة): موزّع على آخر 30 يوماً
    clock_restore()
    titles = [('create', 'إنشاء فاتورة مبيعات'), ('confirm', 'تأكيد فاتورة مبيعات'), ('create', 'تسجيل مصروف جديد'),
              ('confirm', 'تأكيد أمر شراء'), ('update', 'تعديل بيانات صنف'), ('create', 'تسجيل دفعة عميل'),
              ('create', 'إضافة عميل جديد'), ('other', 'تصدير تقرير المبيعات'), ('confirm', 'استلام شحنة من المخزن المركزي'),
              ('login', 'تسجيل دخول'), ('create', 'إنشاء مرتجع مبيعات'), ('create', 'تسجيل سداد مورد')]
    users = S.users
    objs = []
    for k in range(120):
        d = S.today - dt.timedelta(days=rng.randint(0, 29))
        when = dt.datetime(d.year, d.month, d.day, rng.randint(9, 22), rng.randint(0, 59), tzinfo=S.tz)
        if when > _REAL_NOW():
            when = _REAL_NOW() - dt.timedelta(minutes=rng.randint(5, 600))
        u = rng.choice(users)
        a, ttl = rng.choice(titles)
        objs.append((UserActivity(tenant=S.tenant, user=u, branch=u.branch, title=ttl, action_type=a,
                                  details='', ip_address=f'41.{rng.randint(20, 250)}.{rng.randint(1, 250)}.{rng.randint(1, 250)}'), when))
    created = []
    for obj, when in objs:
        obj.save()
        UserActivity.objects.filter(pk=obj.pk).update(created_at=when)
        created.append(obj)
    S.counters['user_activities'] = len(created)

    # إشعارات النظام (الدوال الأصلية)
    c1 = ns.generate_low_stock_notifications(S.tenant)
    c2 = ns.generate_expiry_notifications(S.tenant, warn_days=60)
    c3 = ns.generate_overdue_invoice_notifications(S.tenant)
    c4 = ns.generate_rfq_expiry_notifications(S.tenant)
    # الفواتير المسدَّدة كلياً لا تُنبَّه كمتأخرة (مولّد الإشعارات الأصلي لا يفحص السداد)
    from apps.sales.models import SaleInvoice
    for n in Notification.objects.filter(tenant=S.tenant, notification_type='overdue_invoice'):
        try:
            inv = SaleInvoice.objects.get(pk=int(n.link.strip('/').split('/')[-1]))
        except Exception:
            continue
        if inv.paid_amount >= inv.grand_total:
            n.delete()
    # مخزن الاحتياطي الثانوي لا يُنبَّه على نقصه (مخزن تحويلات فقط)
    Notification.objects.filter(tenant=S.tenant, notification_type='low_stock', link__contains=f'&stock={S.reserve_stock.id}').delete()
    log(f'إشعارات مولّدة: مخزون={c1} صلاحية={c2} متأخرات={c3} عروض={c4}')
    # إشعارات عامة
    gen = [
        ('general', 'high', 'شحنة في الطريق إلى الفرع', 'شحنة جديدة من المخزن المركزي بانتظار تأكيد الاستلام.', '/stocks/shipments/'),
        ('general', 'medium', 'تحويل مالي في الطريق', 'تحويل مالي من الفرع إلى الإدارة بانتظار التأكيد.', '/branch-transfers/'),
        ('general', 'low', 'تذكير: جرد دوري', 'موعد الجرد الدوري للأصناف سريعة الحركة هذا الأسبوع.', ''),
        ('general', 'medium', 'مطالبات تأمين بانتظار الرد', 'توجد مطالبات تأمين مُقدَّمة لم تردّ عليها الشركات بعد.', '/insurance/claims/'),
    ]
    for nt, pr, ti, ms, ln in gen:
        Notification.objects.create(tenant=S.tenant, notification_type=nt, priority=pr, title=ti, message=ms, link=ln)
    # اجعل القديم مقروءاً
    old = Notification.objects.filter(tenant=S.tenant).order_by('id')
    ids = list(old.values_list('id', flat=True))
    for nid in ids[: int(len(ids) * 0.55)]:
        Notification.objects.filter(pk=nid).update(is_read=True)

    # تذاكر الدعم (دون إشعار لإدارة المنصة)
    from apps.core.signals import _on_ticket_created
    post_save.disconnect(_on_ticket_created, sender=SupportTicket, dispatch_uid='admin_notif_new_ticket')
    from apps.accounts.models import User as _U
    admin_user = _U.objects.filter(is_superuser=True, tenant__isnull=True).order_by('id').first()
    try:
        specs = [
            ('technical', 'medium', 'closed', 'طباعة الفاتورة على طابعة الإيصالات الحرارية', 'مقاس الإيصال لا يطابق الورق، نرجو المساعدة في ضبط الطباعة.',
             'تم ضبط مقاس الإيصال إلى 80 مم من إعدادات الطباعة. هل تم حل المشكلة؟', 'نعم، شكراً لكم.', 28),
            ('feature', 'low', 'resolved', 'اقتراح: إشعار عند اقتراب انتهاء صلاحية الأدوية', 'نرجو إضافة تنبيه أسبوعي للدفعات التي تنتهي خلال 60 يوماً.',
             'الميزة متوفرة في الإشعارات — فعّلنا لكم خيار 60 يوماً. سنضيف تقريراً أسبوعياً في تحديث قادم.', 'ممتاز، بانتظار التحديث.', 12),
            ('account', 'medium', 'in_progress', 'إضافة مستخدم جديد لفرع عطبرة', 'نحتاج إضافة صيدلاني جديد بصلاحيات المبيعات فقط لفرع عطبرة.',
             'تم استلام طلبكم وجارٍ مراجعة حد المستخدمين في الباقة.', '', 3),
        ]
        for cat, pr, status, subj, desc, admin_reply, tenant_reply, ago in specs:
            when = _REAL_NOW() - dt.timedelta(days=ago)
            tk = SupportTicket.objects.create(tenant=S.tenant, created_by=S.owner, subject=subj, description=desc, category=cat,
                                              priority=pr, status=status,
                                              last_reply_at=when + dt.timedelta(hours=5),
                                              closed_at=(when + dt.timedelta(days=1)) if status in ('closed', 'resolved') else None)
            SupportTicket.objects.filter(pk=tk.pk).update(created_at=when)
            m1 = SupportMessage.objects.create(ticket=tk, sender=S.owner, sender_type='tenant', body=desc)
            SupportMessage.objects.filter(pk=m1.pk).update(created_at=when)
            if admin_user is not None:
                m2 = SupportMessage.objects.create(ticket=tk, sender=admin_user, sender_type='admin', body=admin_reply)
                SupportMessage.objects.filter(pk=m2.pk).update(created_at=when + dt.timedelta(hours=5))
                SupportTicket.objects.filter(pk=tk.pk).update(assigned_to=admin_user)
            if tenant_reply:
                m3 = SupportMessage.objects.create(ticket=tk, sender=S.owner, sender_type='tenant', body=tenant_reply)
                SupportMessage.objects.filter(pk=m3.pk).update(created_at=when + dt.timedelta(hours=8))
            S.counters['support_tickets'] += 1
    finally:
        post_save.connect(_on_ticket_created, sender=SupportTicket, weak=False, dispatch_uid='admin_notif_new_ticket')


# ──────────────────────────────────────────────────────────────────────────
#  الفحوص النهائية وحساب الأرباح
# ──────────────────────────────────────────────────────────────────────────
def profit_numbers(start, end, branch):
    """يطابق منطق analytics (مبيعات − مشتريات − مصروفات) وقائمة الدخل (COGS)."""
    from apps.sales.models import SaleInvoice, SaleInvoiceLine, SaleReturn
    from apps.purchases.models import PurchaseInvoice
    from apps.expenses.models import Expense
    from apps.employees.models import EmployeeSalaryPayment
    from apps.core.utils import filter_by_branch_strict
    t = S.tenant
    inv = filter_by_branch_strict(SaleInvoice.objects.filter(
        tenant=t, status__in=SaleInvoice.REVENUE_STATUSES, invoice_date__gte=start, invoice_date__lte=end), branch)
    sales = inv.aggregate(s=Sum('grand_total'))['s'] or D('0')
    pur = filter_by_branch_strict(PurchaseInvoice.objects.filter(
        tenant=t, status__in=PurchaseInvoice.EFFECTIVE_STATUSES, invoice_date__gte=start, invoice_date__lte=end), branch) \
        .aggregate(s=Sum('grand_total'))['s'] or D('0')
    exp = Expense.objects.for_tenant(t).for_branch(branch, strict=True).filter(
        status='confirmed', expense_date__gte=start, expense_date__lte=end).aggregate(s=Sum('amount'))['s'] or D('0')
    rets = filter_by_branch_strict(SaleReturn.objects.filter(
        tenant=t, status='confirmed', return_date__gte=start, return_date__lte=end), branch,
        field='original_invoice__stock__branch').aggregate(s=Sum('total_returned'))['s'] or D('0')
    cogs = filter_by_branch_strict(SaleInvoiceLine.objects.filter(
        tenant=t, invoice__status__in=SaleInvoice.REVENUE_STATUSES, invoice__invoice_date__gte=start,
        invoice__invoice_date__lte=end), branch, field='invoice__stock__branch').aggregate(
        s=Sum(ExpressionWrapper((F('quantity') - F('returned_quantity')) * F('cost_price_snapshot'), output_field=DecimalField())))['s'] or D('0')
    sal_qs = EmployeeSalaryPayment.objects.filter(tenant=t, status='paid', period_end__gte=start, period_end__lte=end)
    if branch is not None:
        sal_qs = sal_qs.filter(employee__branch=branch)
    sal = sal_qs.aggregate(s=Sum('base_salary'))['s'] or D('0')
    net_rev = sales - rets
    gp = net_rev - cogs
    return dict(sales=sales, purchases=pur, expenses=exp, salaries=sal, returns=rets, cogs=cogs,
                analytics_profit=sales - pur - exp, gross_profit=gp,
                margin=(gp / net_rev * 100) if net_rev else D('0'), net_profit=gp - exp,
                net_after_salaries=gp - exp - sal)


def verify_and_report():
    from apps.stocks.models import StockQuantity
    from apps.sales.models import SaleInvoice
    from apps.customers.models import Customer
    from apps.suppliers.models import Supplier
    from apps.purchases.models import SupplierLedger
    from apps.sales.models import CustomerLedger
    t = S.tenant
    problems = []
    # 1) مخزون سالب + مطابقة المرآة
    neg = StockQuantity.objects.filter(tenant=t, quantity__lt=0).count()
    if neg:
        problems.append(f'كميات سالبة: {neg}')
    mism = 0
    for sq in StockQuantity.objects.filter(tenant=t):
        exp = S.qty.get((sq.stock_id, sq.item_id))
        if exp is not None and exp != sq.quantity:
            mism += 1
            if mism <= 5:
                log(f'   فرق كمية: stock={sq.stock_id} item={sq.item_id} db={sq.quantity} mirror={exp}')
    if mism:
        problems.append(f'فروق بين المحاكاة والقاعدة: {mism}')
    # 2) أرصدة العملاء ضمن الحدود، الموردون ضمن الحدود
    over = 0
    for c in Customer.unscoped.filter(tenant=t, credit_limit__gt=0):
        bal = CustomerLedger.objects.filter(tenant=t, customer=c).aggregate(s=Sum('amount'))['s'] or D('0')
        if bal > c.credit_limit:
            over += 1
    if over:
        problems.append(f'عملاء تجاوزوا الحد الائتماني: {over}')
    for s in Supplier.unscoped.filter(tenant=t, credit_limit__gt=0):
        bal = SupplierLedger.objects.filter(tenant=t, supplier=s).aggregate(s=Sum('amount'))['s'] or D('0')
        if bal > s.credit_limit:
            problems.append(f'مورد تجاوز الحد: {s.name}')
    # 3) أرباح كل الفترات والنطاقات
    today = S.today
    windows = {
        'اليوم': (today, today),
        'آخر 3 أيام': (today - dt.timedelta(days=2), today),
        'هذا الأسبوع (من السبت)': (today - dt.timedelta(days=(today.weekday() - 5) % 7), today),
        'آخر 7 أيام': (today - dt.timedelta(days=6), today),
        'هذا الشهر': (today.replace(day=1), today),
        'آخر 30 يوماً': (today - dt.timedelta(days=29), today),
        'آخر 3 أشهر (90 يوماً)': (today - dt.timedelta(days=89), today),
        'هذه السنة': (today.replace(month=1, day=1), today),
    }
    pm_end = today.replace(day=1) - dt.timedelta(days=1)
    windows['الشهر السابق'] = (pm_end.replace(day=1), pm_end)
    cur = pm_end.replace(day=1)
    for k in range(2, 4):
        e = cur - dt.timedelta(days=1)
        windows[f'قبل {k} شهر'] = (e.replace(day=1), e)
        cur = e.replace(day=1)
    scopes = [('كل الفروع', None)] + [(b.name, b) for b in S.branches]
    log('\n══════ الأرباح (مبيعات − مشتريات − مصروفات = ربح analytics | هامش مجمل | صافي بعد المصروفات) ══════')
    S.report_rows = []
    for wname, (a, b_) in windows.items():
        for sname, br in scopes:
            r = profit_numbers(a, b_, br)
            S.report_rows.append((wname, sname, r))
            ok = True
            short = (b_ - a).days < 7 or r['sales'] < D('1500000')
            if r['sales'] > 0:
                if r['analytics_profit'] <= 0 or r['net_profit'] <= 0 or r['margin'] < (25 if short else 30) \
                        or (r['net_after_salaries'] <= 0 and (b_ - a).days >= 25):
                    ok = False
            log(f'{"OK " if ok else "BAD"} {wname:<24} {sname:<22} مبيعات={r["sales"]:>13,.0f} مشتريات={r["purchases"]:>12,.0f} '
                f'مصروفات={r["expenses"]:>10,.0f} رواتب={r["salaries"]:>10,.0f} | ربح(analytics)={r["analytics_profit"]:>12,.0f} '
                f'| هامش={r["margin"]:>5.1f}% | صافي={r["net_profit"]:>12,.0f} | بعد الرواتب={r["net_after_salaries"]:>12,.0f}')
            if not ok:
                problems.append(f'ربحية غير سليمة: {wname} / {sname}')
    # 4) اتساق الدفاتر والخزائن
    from apps.treasury.models import Treasury, TreasuryMovement
    from apps.bank_accounts.models import BankAccount, BankAccountMovement
    for tr in Treasury.objects.filter(tenant=t):
        s = TreasuryMovement.objects.filter(tenant=t, treasury=tr)
        net = (s.filter(movement_type__in=('receipt', 'adjustment')).aggregate(x=Sum('amount'))['x'] or D('0')) - \
              (s.filter(movement_type='disbursement').aggregate(x=Sum('amount'))['x'] or D('0'))
        if net != tr.current_balance:
            problems.append(f'خزينة غير متسقة: {tr.name} {net} ≠ {tr.current_balance}')
        if tr.current_balance < 0:
            problems.append(f'رصيد خزينة سالب: {tr.name}')
    for ba in BankAccount.objects.filter(tenant=t):
        s = BankAccountMovement.objects.filter(tenant=t, bank_account=ba)
        net = (s.filter(movement_type__in=('receipt', 'adjustment')).aggregate(x=Sum('amount'))['x'] or D('0')) - \
              (s.filter(movement_type='disbursement').aggregate(x=Sum('amount'))['x'] or D('0'))
        if net != ba.current_balance:
            problems.append(f'حساب بنكي غير متسق: {ba.name}')
        if ba.current_balance < 0:
            problems.append(f'رصيد بنكي سالب: {ba.name}')
    return problems


def print_summary(before, after):
    log('\n══════ ما أُنشئ (عدد السجلات لكل نموذج) ══════')
    rows = []
    for k in sorted(after):
        d = after[k] - before.get(k, 0)
        if d:
            rows.append((k, d))
    for k, d in rows:
        log(f'  {k:<45} {d:>8}')
    log('\n══════ عدّادات العمليات ══════')
    for k in sorted(S.counters):
        log(f'  {k:<40} {S.counters[k]:>8}')
    if S.skipped:
        log(f'\nتخطّيات: {dict(S.skipped)} {getattr(S, "skipped_msgs", [])}')
    if S.topups:
        log(f'إيداعات سيولة إضافية (يجب أن تكون نادرة): {len(S.topups)} — {S.topups[:6]}')


# ──────────────────────────────────────────────────────────────────────────
def run():
    from apps.accounts.models import User  # noqa: F401
    before = None
    S.purchases = []
    S.pending_ship = None
    S.pending_branch_transfer = None
    S.salary_total = D('0')
    S.qty_reserved = defaultdict(D)
    ok = False
    with transaction.atomic():
        bootstrap_tenant()
        preflight()
        try:
            caps = S.tenant.capabilities
        except Exception:
            caps = None
        for flag in ('has_insurance_billing', 'has_expiry_dates', 'has_batch_numbers', 'has_agents_module', 'has_drug_classification'):
            if caps is None or not getattr(caps, flag, False):
                log(f'⚠ تنبيه: قدرة المشترك {flag} معطّلة — البيانات ستُنشأ لكن الوحدة قد تكون مخفية في الواجهة (إعداد إدارة المنصة).')
        if not S.tenant.plan_allows('agents'):
            log('⚠ باقة المشترك لا تتيح المناديب.')
        before = count_snapshot()
        clock_install()
        install_treasury_patch()
        try:
            set_clock(S.open_day, 540)
            setup_users_and_accounts()
            setup_catalog_and_items()
            setup_parties()
            init_demand()
            setup_store()
            seed_exchange_history()
            open_stocks()
            open_balances()
            plan_period_events()
            log('بدء محاكاة الأيام …')
            run_days()
            finalize_pending()
            nexp = adjust_near_expiry()
            log(f'دفعات قريبة الانتهاء: {nexp}')
            add_expired_batches()
            log(f"دفعات منتهية الصلاحية: {S.counters['expired_batches']}")
            # خطوات تُنفَّذ بالوقت الحقيقي
            clock_restore()
            post_activity_and_notifications()
            after = count_snapshot()
            problems = verify_and_report()
            n_sales = sum(v for k, v in S.counters.items() if k.startswith('sales_') and k not in ('sales_draft', 'sales_pending_delivery'))
            if n_sales < 150 * SCALE:
                problems.append(f'عدد فواتير البيع المؤكدة قليل جداً: {n_sales}')
            print_summary(before, after)
            if problems:
                log('\n✗ مشاكل في الفحص النهائي:')
                for p in problems:
                    log('   - ' + p)
                raise SeedAbort('فشل الفحص النهائي — سيتم التراجع عن كل شيء.')
            if DRY_RUN:
                raise SeedAbort('SEED_DRY_RUN=1 — تم التراجع عمداً بعد نجاح كل الفحوص.')
            ok = True
        finally:
            restore_treasury_patch()
            clock_restore()
    if ok:
        log('\n✓ تم الحفظ (commit).')
        if not SKIP_BACKUP:
            try:
                from apps.core.backup_service import create_backup
                for typ in ('auto', 'manual'):
                    rec = create_backup(S.tenant, typ)
                    log(f'نسخة احتياطية {typ}: {rec.filename} [{rec.status}] {rec.file_size_display}')
            except Exception as e:  # noqa: BLE001
                log(f'تعذّر إنشاء النسخة الاحتياطية (غير حرج): {e}')


try:
    run()
except SeedAbort as e:
    clock_restore()
    log(f'\n■ توقف السكربت: {e}')
except Exception:
    clock_restore()
    log('\n■ خطأ غير متوقع — تم التراجع عن كل شيء:')
    traceback.print_exc()
