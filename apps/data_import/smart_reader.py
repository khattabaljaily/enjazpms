"""
قراءة ذكية لملفات الاستيراد: قالب النظام نفسه أو أي ملف Excel/CSV مصدَّر من نظام آخر.

لا يفترض شيئاً عن شكل الملف:
- يختار ورقة البيانات تلقائياً (ليست بالضرورة الأولى)، ويتجاهل أوراق التعليمات والقوائم المخفية.
- يكتشف صف العناوين حتى لو سبقته أسطر مقدمة أو عنوان تقرير أو شعار.
- يطابق عناوين الأعمدة بحقول النظام: أولاً بأسماء بديلة معروفة (عربي/إنجليزي) بعد توحيد
  الكتابة العربية، ثم بالذكاء الاصطناعي للأعمدة التي لم تُعرف (مع عيّنة من قيمها).
- يقرأ القيم بتسامح: أرقام عربية، فواصل آلاف، رموز عملة داخل الخلية، باركود مخزّن كرقم عشري،
  تواريخ بصيغ متعددة.

عام بالكامل: يعتمد على قائمة الحقول (schema) التي يمررها المستورد، فلا يعرف شيئاً عن المنتجات.
"""
import csv
import difflib
import io
import json
import re
import shutil
import tempfile
import time
import uuid
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from openpyxl.utils import get_column_letter

MAX_SCAN_ROWS = 40          # أقصى عدد صفوف يُبحث فيها عن صف العناوين
SAMPLE_SIZE = 5             # عدد القيم النموذجية لكل عمود (للعرض وللذكاء الاصطناعي)
SKIP_SHEET_NAMES = {'تعليمات', '_lists', 'instructions'}
ALLOWED_EXTENSIONS = ('.xlsx', '.xlsm', '.xls', '.csv')

# صفوف إجماليات في آخر التقارير المصدّرة من الأنظمة الأخرى — ليست منتجات.
SUMMARY_WORDS = {'الاجمالي', 'اجمالي', 'المجموع', 'مجموع', 'الاجمالى', 'المجموع الكلي', 'الاجمالي العام',
                 'total', 'totals', 'grand total', 'sum', 'subtotal'}

# ──────────────────────────────────────────────────────────────
# توحيد النص العربي
# ──────────────────────────────────────────────────────────────

_DIACRITICS = re.compile(r'[ؐ-ًؚ-ٰٟۖ-ۭـ]')
_PUNCT = re.compile(r'[\*\(\)\[\]\{\}:؛;،,\.\-_/\\|#؟?!"\'`~<>=+%$]+')
_ARABIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789')


def normalize_text(value) -> str:
    """توحيد للمقارنة: بلا تشكيل/تطويل، أ/إ/آ→ا، ة→ه، ى→ي، بلا علامات ترقيم، بأحرف صغيرة."""
    if value is None:
        return ''
    s = str(value).strip().lstrip('﻿').lower().translate(_ARABIC_DIGITS)
    s = _DIACRITICS.sub('', s)
    s = s.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا').replace('ٱ', 'ا')
    s = s.replace('ة', 'ه').replace('ى', 'ي').replace('ؤ', 'و').replace('ئ', 'ي')
    s = _PUNCT.sub(' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ('', 'none', 'nan', 'null', 'n/a', '#n/a', '-', '—', '--')
    return False


def display_value(value) -> str:
    """نص القيمة كما يراه المستخدم في رسائل الأخطاء والمعاينة."""
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d') if not (value.hour or value.minute) else value.strftime('%Y-%m-%d %H:%M')
    if isinstance(value, date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _clean_cell(value):
    if isinstance(value, str):
        value = value.replace('\xa0', ' ').replace('﻿', '').strip()
        return value or None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)   # الباركود والأرقام الصحيحة تُخزَّن في Excel كأعداد عشرية (6291041500213.0)
    return value


# ──────────────────────────────────────────────────────────────
# قراءة الملف
# ──────────────────────────────────────────────────────────────

class ReadError(Exception):
    pass


def read_workbook(data: bytes, filename: str) -> list:
    """[{'name': str, 'rows': [[cell, ...], ...]}] لكل ورقة مرئية تحوي بيانات."""
    name = (filename or '').lower()
    if not name.endswith(ALLOWED_EXTENSIONS):
        raise ReadError('نوع الملف غير مدعوم. ارفع ملف Excel ‏(xlsx أو xls) أو CSV.')
    if not data:
        raise ReadError('الملف فارغ.')
    try:
        if name.endswith('.csv'):
            sheets = [{'name': 'CSV', 'rows': _read_csv(data)}]
        elif name.endswith('.xls'):
            sheets = _read_xls(data)
        else:
            sheets = _read_xlsx(data)
    except ReadError:
        raise
    except Exception as exc:
        raise ReadError(f'تعذّرت قراءة الملف — تأكد أنه ملف Excel سليم وغير محمي بكلمة مرور ({exc.__class__.__name__}).')
    sheets = [s for s in sheets if any(any(not is_empty(c) for c in row) for row in s['rows'])]
    if not sheets:
        raise ReadError('الملف لا يحتوي على أي بيانات.')
    return sheets


def _read_xlsx(data):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    sheets = []
    for ws in wb.worksheets:
        if ws.sheet_state != 'visible' or ws.title.strip().lower() in SKIP_SHEET_NAMES:
            continue
        rows = [[_clean_cell(v) for v in row] for row in ws.iter_rows(values_only=True)]
        sheets.append({'name': ws.title, 'rows': _trim(rows)})
    return sheets


def _read_xls(data):
    try:
        import xlrd
    except ImportError:
        raise ReadError('صيغة xls القديمة غير مدعومة على هذا الخادم. افتح الملف في Excel واحفظه بصيغة xlsx ثم ارفعه.')
    book = xlrd.open_workbook(file_contents=data)
    sheets = []
    for sh in book.sheets():
        if getattr(sh, 'visibility', 0) or sh.name.strip().lower() in SKIP_SHEET_NAMES:
            continue
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                value = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    value = xlrd.xldate.xldate_as_datetime(value, book.datemode)
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    value = None
                row.append(_clean_cell(value))
            rows.append(row)
        sheets.append({'name': sh.name, 'rows': _trim(rows)})
    return sheets


def _read_csv(data):
    text = None
    for encoding in ('utf-8-sig', 'cp1256', 'latin-1'):   # cp1256: ملفات Excel العربية القديمة على ويندوز
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    text = text.lstrip('﻿')
    first_line = text.split('\n', 1)[0]
    delimiter = max(',;\t|', key=first_line.count) if any(d in first_line for d in ',;\t|') else ','
    rows = [[_clean_cell(v) for v in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return _trim(rows)


def _trim(rows):
    """يحذف الأعمدة الفارغة تماماً من نهاية الصفوف، ويوحّد طول الصفوف."""
    width = 0
    for row in rows:
        for idx in range(len(row) - 1, -1, -1):
            if not is_empty(row[idx]):
                width = max(width, idx + 1)
                break
    return [list(row[:width]) + [None] * (width - len(row[:width])) for row in rows]


# ──────────────────────────────────────────────────────────────
# اكتشاف الورقة وصف العناوين
# ──────────────────────────────────────────────────────────────

def build_alias_index(schema) -> dict:
    """{field: set(أسماء بديلة موحّدة)} — من عنوان القالب واسم الحقل والأسماء البديلة في schema."""
    index = {}
    for spec in schema:
        names = {spec['field'], spec['field'].replace('_', ' '), spec.get('header_ar', ''),
                 re.sub(r'\(.*?\)|\*', '', spec.get('header_ar', ''))}
        names.update(spec.get('aliases', ()))
        index[spec['field']] = {normalize_text(n) for n in names if normalize_text(n)}
    return index


def _looks_numeric(value) -> bool:
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return True
    return parse_decimal(value)[0] is not None and not re.search(r'[a-zA-Z؀-ۿ]{3,}', str(value))


def _row_score(rows, idx, all_aliases):
    row = rows[idx]
    values = [v for v in row if not is_empty(v)]
    if len(values) < 2:
        return -1, 0
    texts = [v for v in values if isinstance(v, str) and not _looks_numeric(v)]
    known = sum(1 for v in texts if normalize_text(v) in all_aliases)
    distinct = len({normalize_text(v) for v in texts})
    below = rows[idx + 1: idx + 6]
    data_like = sum(1 for r in below if sum(1 for v in r if not is_empty(v)) >= max(2, len(values) // 2))
    score = known * 4 + distinct + data_like - (len(values) - len(texts)) * 2
    return score, known


def detect_header_row(rows, alias_index) -> tuple:
    """(رقم صف العناوين 0-based، عدد العناوين المعروفة فيه)."""
    all_aliases = set().union(*alias_index.values()) if alias_index else set()
    best = (None, -1, 0)
    for idx in range(min(len(rows), MAX_SCAN_ROWS)):
        score, known = _row_score(rows, idx, all_aliases)
        if score > best[1]:
            best = (idx, score, known)
    if best[0] is None:
        first = next((i for i, r in enumerate(rows) if any(not is_empty(v) for v in r)), 0)
        return first, 0
    return best[0], best[2]


def sheet_overview(sheets, alias_index) -> list:
    """ملخص لكل ورقة: صف العناوين المقترح، عدد العناوين المعروفة، عدد صفوف البيانات."""
    overview = []
    for idx, sheet in enumerate(sheets):
        header_idx, known = detect_header_row(sheet['rows'], alias_index)
        data_rows = sum(1 for r in sheet['rows'][header_idx + 1:] if any(not is_empty(v) for v in r))
        overview.append({'index': idx, 'name': sheet['name'], 'header_row': header_idx,
                         'known_headers': known, 'data_rows': data_rows,
                         'score': known * 5 + min(data_rows, 30)})
    return overview


def pick_sheet(overview) -> int:
    return max(overview, key=lambda s: (s['score'], -s['index']))['index']


def extract_table(sheet, header_idx) -> tuple:
    """(columns, data_rows). columns: [{index, letter, header, samples}]، data_rows: [(رقم صف Excel، [قيم])]."""
    rows = sheet['rows']
    header = rows[header_idx] if header_idx < len(rows) else []
    body = rows[header_idx + 1:]
    header_norm = [normalize_text(v) for v in header]
    columns = []
    for col, title in enumerate(header):
        values = [r[col] for r in body if col < len(r) and not is_empty(r[col])]
        if is_empty(title) and not values:
            continue
        letter = get_column_letter(col + 1)
        samples = []
        for v in values:
            text = display_value(v)
            if text and text not in samples:
                samples.append(text[:60])
            if len(samples) >= SAMPLE_SIZE:
                break
        columns.append({'index': col, 'letter': letter,
                        'header': display_value(title) if not is_empty(title) else f'عمود {letter} (بلا عنوان)',
                        'samples': samples, 'filled': len(values)})
    data_rows = []
    for offset, row in enumerate(body):
        if not any(not is_empty(v) for v in row):
            continue
        # بعض التقارير تكرر صف العناوين في رأس كل صفحة
        if [normalize_text(v) for v in row] == header_norm:
            continue
        data_rows.append((header_idx + offset + 2, row))
    return columns, data_rows


# ──────────────────────────────────────────────────────────────
# مطابقة الأعمدة
# ──────────────────────────────────────────────────────────────

def match_columns(columns, alias_index) -> dict:
    """
    مطابقة حتمية بلا ذكاء اصطناعي: {col_index: {'field', 'confidence', 'source'}}.
    exact = العنوان أحد الأسماء البديلة حرفياً بعد التوحيد؛ partial = يحتوي اسماً بديلاً (يراجعه الذكاء الاصطناعي).
    """
    result = {}
    for col in columns:
        header = normalize_text(col['header'])
        if not header:
            continue
        exact = [f for f, aliases in alias_index.items() if header in aliases]
        if exact:
            result[col['index']] = {'field': exact[0], 'confidence': 1.0, 'source': 'exact'}
            continue
        padded = f' {header} '
        best, best_len = None, 0
        for field, aliases in alias_index.items():
            for alias in aliases:
                if len(alias) >= 3 and f' {alias} ' in padded and len(alias) > best_len:
                    best, best_len = field, len(alias)
        if best:
            result[col['index']] = {'field': best, 'confidence': 0.6, 'source': 'partial'}
    return result


_SOUND_ALIKE = str.maketrans({'ظ': 'ض', 'ذ': 'ز', 'ث': 'س'})


def _loose(text):
    """أخطاء الإملاء الشائعة بين الحروف المتقاربة نطقاً (منضفات/منظفات، ذرة/زرة) وأداة التعريف."""
    text = normalize_text(text).translate(_SOUND_ALIKE)
    return ' '.join(w[2:] if w.startswith('ال') and len(w) > 4 else w for w in text.split())


def closest_name(raw, names, cutoff=0.86):
    """أقرب اسم من القائمة بعد التوحيد (أخطاء إملائية بسيطة/همزات/ة-ه/ال التعريف). None إن لم يوجد."""
    key = normalize_text(raw)
    if not key:
        return None
    by_norm = {normalize_text(n): n for n in names}
    if key in by_norm:
        return by_norm[key]
    by_loose = {_loose(n): n for n in names}
    if _loose(raw) in by_loose:
        return by_loose[_loose(raw)]
    match = difflib.get_close_matches(key, list(by_norm), n=1, cutoff=cutoff)
    return by_norm[match[0]] if match else None


# ──────────────────────────────────────────────────────────────
# قراءة القيم
# ──────────────────────────────────────────────────────────────

_NUMBER_CHARS = re.compile(r'[^0-9.,\-]')


def parse_decimal(value):
    """(Decimal أو None، نص الخطأ أو None). يقبل: ١٬٥٠٠ / 1,500.50 / 1.500,50 / 1500 ج.س / $12."""
    if is_empty(value):
        return None, None
    if isinstance(value, bool):
        return None, 'قيمة غير رقمية'
    if isinstance(value, (int, Decimal)):
        return Decimal(value), None
    if isinstance(value, float):
        return Decimal(str(value)), None
    text = str(value).translate(_ARABIC_DIGITS).replace('٫', '.').replace('٬', ',').replace('،', ',')
    text = text.replace(' ', '')
    negative = text.startswith('(') and text.endswith(')')
    text = _NUMBER_CHARS.sub('', text)
    if not re.search(r'\d', text):
        return None, 'قيمة غير رقمية'
    if ',' in text and '.' in text:
        if text.rfind(',') > text.rfind('.'):
            text = text.replace('.', '').replace(',', '.')
        else:
            text = text.replace(',', '')
    elif ',' in text:
        if re.fullmatch(r'-?\d{1,3}(,\d{3})+', text):
            text = text.replace(',', '')
        elif text.count(',') == 1 and re.fullmatch(r'-?\d+,\d{1,2}', text):
            text = text.replace(',', '.')
        else:
            text = text.replace(',', '')
    if text.count('.') > 1:
        return None, 'رقم غير مفهوم'
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None, 'رقم غير مفهوم'
    return (-abs(number) if negative else number), None


_DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d.%m.%Y', '%d/%m/%y', '%d-%m-%y',
                 '%Y-%m-%d %H:%M:%S', '%d %b %Y', '%d %B %Y', '%b %Y', '%B %Y')
_MONTH_FORMATS = ('%m/%Y', '%m-%Y', '%Y-%m', '%Y/%m', '%m.%Y', '%m/%y', '%m-%y')


def parse_date(value):
    """(date أو None، نص الخطأ أو None). الشهر/السنة وحدهما (مثل 05/2027) = آخر يوم في الشهر."""
    if is_empty(value):
        return None, None
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    if isinstance(value, (int, float)) and 20000 < value < 80000:   # رقم تسلسلي لتاريخ Excel
        return (datetime(1899, 12, 30) + timedelta(days=int(value))).date(), None
    text = str(value).translate(_ARABIC_DIGITS).strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
            if fmt in ('%b %Y', '%B %Y'):
                parsed = parsed.replace(day=monthrange(parsed.year, parsed.month)[1])
            return parsed, None
        except ValueError:
            continue
    for fmt in _MONTH_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
            return parsed.replace(day=monthrange(parsed.year, parsed.month)[1]), None
        except ValueError:
            continue
    return None, 'تاريخ غير مفهوم (اكتبه مثل 2027-05-31)'


_TRUE = {'نعم', 'yes', 'y', '1', 'true', 'صح', 'صحيح', '✓', '✔', 'x', 'active', 'نشط', 'اي', 'on'}
_FALSE = {'لا', 'no', 'n', '0', 'false', 'خطا', '✗', '✘', 'غير', 'inactive', 'off', 'كلا'}


def parse_bool(value):
    """(True/False أو None، نص الخطأ أو None)."""
    if is_empty(value):
        return None, None
    if isinstance(value, bool):
        return value, None
    key = normalize_text(value)
    if key in _TRUE:
        return True, None
    if key in _FALSE:
        return False, None
    return None, 'اكتب نعم أو لا'


def parse_text(value) -> str:
    if is_empty(value):
        return ''
    return re.sub(r'\s+', ' ', display_value(value)).strip()


def is_summary_row(name) -> bool:
    key = normalize_text(name)
    return key in SUMMARY_WORDS or any(key.startswith(w + ' ') for w in SUMMARY_WORDS if ' ' not in w)


# ──────────────────────────────────────────────────────────────
# حفظ مؤقت للملف بين خطوة المعاينة وخطوة الاستيراد
# ──────────────────────────────────────────────────────────────

STASH_TTL_SECONDS = 12 * 3600
_TOKEN_RE = re.compile(r'^[0-9a-f]{32}$')


def _stash_root() -> Path:
    root = getattr(settings, 'DATA_IMPORT_TMP_DIR', None) or Path(tempfile.gettempdir()) / 'enjaz_imports'
    return Path(root)


def _stash_dir(tenant_id, user_id, token) -> Path | None:
    if not token or not _TOKEN_RE.match(str(token)):
        return None
    return _stash_root() / f'{int(tenant_id)}_{int(user_id)}' / token


def _cleanup_old():
    root = _stash_root()
    if not root.exists():
        return
    cutoff = time.time() - STASH_TTL_SECONDS
    for owner in root.iterdir():
        for entry in owner.iterdir() if owner.is_dir() else []:
            try:
                if entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass


def stash_save(tenant_id, user_id, data: bytes, filename: str) -> str:
    _cleanup_old()
    token = uuid.uuid4().hex
    folder = _stash_dir(tenant_id, user_id, token)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'source').write_bytes(data)
    (folder / 'meta.json').write_text(json.dumps({'filename': filename}, ensure_ascii=False), encoding='utf-8')
    return token


def stash_load(tenant_id, user_id, token):
    """(data, meta) أو None إن انتهت صلاحية الملف أو لا يخص هذا المستخدم."""
    folder = _stash_dir(tenant_id, user_id, token)
    if not folder or not (folder / 'source').exists():
        return None
    try:
        meta = json.loads((folder / 'meta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        meta = {}
    return (folder / 'source').read_bytes(), meta


def stash_update_meta(tenant_id, user_id, token, meta: dict):
    folder = _stash_dir(tenant_id, user_id, token)
    if folder and folder.exists():
        (folder / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, default=str), encoding='utf-8')


def stash_write_file(tenant_id, user_id, token, name, data: bytes):
    folder = _stash_dir(tenant_id, user_id, token)
    if folder and folder.exists():
        (folder / name).write_bytes(data)


def stash_read_file(tenant_id, user_id, token, name):
    folder = _stash_dir(tenant_id, user_id, token)
    path = folder / name if folder else None
    return path.read_bytes() if path and path.exists() else None
