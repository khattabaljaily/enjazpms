"""
حارس لغوي: واجهة النظام ونصوصه بالعربية الفصحى. يفشل الاختبار إذا ظهر في أي قالب أو
كود أو سكربت واجهة مفردة عامية (أي لهجة محلية) من القائمة أدناه.
"""
import os
import re

from django.conf import settings
from django.test import SimpleTestCase

_B = r'(?<![؀-ۿ])'
_E = r'(?![؀-ۿ])'
_WORDS = [
    'مش', 'دلوقتي', 'دلوقت', 'دا', 'ده', 'دي', 'دول', 'كده', 'كدا', 'ازاي', 'إزاي', 'ليه', 'فين',
    'إيه', 'ايه', 'لسه', 'لسا', 'بتاع', 'بتاعة', 'بتاعت', 'عايز', 'عايزة', 'عايزين', 'عاوز', 'عاوزة',
    'برضو', 'برده', 'أوي', 'اوي', 'حاجه', 'شوية', 'شويه', 'عشان', 'علشان', 'اللي', 'إللي', 'يلا',
    'كمان', 'ايوه', 'أيوه', 'اهو', 'أهو', 'مفيش', 'مافيش', 'معلش', 'هسي', 'هسه', 'شنو', 'منو',
    'داير', 'دايرين', 'ذاتو', 'ياخي', 'كتير', 'كتيرة', 'كتيره', 'دايما', 'دايماً', 'دايمًا',
    'تعرضش', 'يقدرش', 'سددتش', 'استحقتش', 'اتأكد', 'اتاكد', 'اتفضل',
]
_PATTERNS = [
    re.compile(_B + '(?:' + '|'.join(map(re.escape, _WORDS)) + ')' + _E),
    # مضارع/مستقبل عامي: بيتم، بتظهر، هيتم، هتظهر...
    re.compile(_B + r'(?:ب|ه)(?:ي|ت|ن)(?:تم|ظهر|قدر|وصف|حصل|كون|عمل|طلع|ضيف|حول|شتغل|روح|جيب|حفظ|مسح|عرض|'
               r'دخل|خرج|اختار|فتح|قفل|رجع|ستخدم|حسب|سجل|نفذ|حتاج|فضل|اخد|ضاف|طبق|تغير|تكرر|منع|سمح)'
               r'[ء-ي]*' + _E),
    # ماضٍ مبني للمجهول بالعامية: اتعمل، اتحذف، اتسجل...
    re.compile(_B + r'ات(?:عمل|سجل|حذف|لغى|ضاف|حفظ|غير|نفذ|كتب|دفع|اكد|أكد|سدد|حصل|عكس|نادى|نادت|رفض|'
               r'فتح|قفل|مسح|ضغط|حول|رسل|بعت|اخد|أخد|طلب|رجع|ظبط|استخدم)[ء-ي]*' + _E),
]
_ARABIC = re.compile(r'[؀-ۿ]')
_SKIP_DIRS = {'.env', 'node_modules', 'staticfiles', '__pycache__', '.git', 'migrations', 'media', 'logs',
              'vendor', 'plugins', 'lib'}
_VENDOR = re.compile(r'(chart|jquery|bootstrap|datatables|fontawesome|select2|flatpickr|sweetalert|\.min\.|moment|'
                     r'popper|html2canvas|jspdf|xlsx|qrcode|sortable)', re.I)
_THIS = os.path.basename(__file__)


class NoDialectTests(SimpleTestCase):
    def test_no_dialect_words_in_ui_or_code(self):
        base = str(settings.BASE_DIR)
        hits = []
        for sub in ('apps', 'templates', os.path.join('static', 'js')):
            for dp, dn, fn in os.walk(os.path.join(base, sub)):
                dn[:] = [d for d in dn if d not in _SKIP_DIRS and not d.startswith('.')]
                for f in fn:
                    if not f.endswith(('.html', '.py', '.js', '.json')) or _VENDOR.search(f) or f == _THIS:
                        continue
                    path = os.path.join(dp, f)
                    with open(path, encoding='utf-8', errors='ignore') as fh:
                        for i, line in enumerate(fh, 1):
                            if not _ARABIC.search(line):
                                continue
                            found = [m for pat in _PATTERNS for m in pat.findall(line)]
                            if found:
                                hits.append(f'{os.path.relpath(path, base)}:{i}: {sorted(set(found))}')
        self.assertFalse(hits, 'ألفاظ عامية يجب استبدالها بالفصحى:\n' + '\n'.join(hits[:40]))
