"""
محرك استيراد المنتجات — بخطوتين:

1. المعاينة (`analyze_products`): يقرأ أي ملف (قالبنا أو تصدير نظام آخر) عبر smart_reader، يختار
   الورقة وصف العناوين، يطابق الأعمدة (أسماء بديلة ثم الذكاء الاصطناعي)، ويفحص كل صف دون أن يكتب
   شيئاً، ثم يعرض على المستخدم ما فهمه والقرارات التي يحتاجها (تصنيفات غير معروفة، صفوف بلا تصنيف،
   عملة أعمدة الأسعار...) ليعدّلها قبل الاستيراد.
2. الاستيراد (`commit_products`): نفس الفحص بنفس الخيارات ثم الكتابة. الصف المعيب لا يوقف الملف:
   يُتخطّى وحده برسالة تحدد الصف والعمود والقيمة والسبب، والمشكلات البسيطة تُصحَّح وتُذكر كتنبيه.

`import_products(tenant, file, user)` يبقى للاستيراد المباشر بلا معاينة (الخيارات التلقائية).

يحاكي أنماطاً موجودة بدل إعادة اختراعها:
- الرصيد الافتتاحي:   apps/stocks/views.py `opening_balance_save_api`
- إنشاء الدفعات:      apps/purchases/services.py `confirm_purchase_invoice`
- المخزن الافتراضي:   apps/stocks/views.py (`filter(is_default=True).first() or .first()`)
- وحدات المنتج:       apps/items/views.py `_save_item_units` (نفس دالة نموذج «إضافة منتج»)
"""
import io
import json
import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction, IntegrityError
from django.utils import timezone

from apps.items.models import Category, Item, ItemBatch
from apps.items.views import _apply_hc_prices, _save_item_units
from apps.items.dedup import find_existing_item_match
from apps.stocks.models import StockQuantity
from apps.stocks.scope import opening_balance_stocks
from apps.sales.models import StockMovement
from apps.ai.services import smart_map_columns, match_category_names_batch

from . import smart_reader as sr
from .schemas import get_product_schema, LOCAL_PRICE_FIELDS
from .gating import products_import_blocked_reason

logger = logging.getLogger('data_import')

# وضع العملة الصعبة: حقول العملة الصعبة ونظيراتها المحلية. عمود لا يدل عنوانه على «عملة صعبة»
# لا يُقرأ كسعر بالعملة الصعبة (إلا إذا اختار المستخدم ذلك)، وإلا ضُرب سعر محلي في سعر الصرف.
HC_TO_LOCAL = {'cost_price_hc': 'cost_price', 'selling_price_hc': 'selling_price',
               'min_selling_price_hc': 'min_selling_price'}
_HC_MARKERS = ('صعبة', 'صعبه', 'hc', 'usd', 'دولار', 'dollar', '$')
SYNTHETIC_FIELDS = ('name', 'category', 'base_unit_name', 'large_unit_name', 'large_unit_count',
                    'opening_quantity', 'stock')
# تُقرأ كبقية الحقول لكنها تخص دفعة الرصيد الافتتاحي (ItemBatch) لا المنتج نفسه
BATCH_FIELDS = ('batch_number', 'expiry_date')
DEFAULT_BASE_UNIT = 'حبة'
ITEM_TYPE_ALIASES = {'service': ('خدمه', 'خدمات', 'service', 'services', 'srv'),
                     'product': ('منتج', 'منتجات', 'سلعه', 'صنف', 'بضاعه', 'product', 'goods', 'item', 'stock')}
MAX_LISTED_ISSUES = 500
PREVIEW_ROWS = 15


def _apply_local_prices(item, tenant, local_prices):
    """السعر المحلي الوارد في الملف هو المرجع (يبقى كما كُتب) والصعبة تُشتق منه = محلي ÷ سعر الصرف."""
    rate = Decimal(str(tenant.exchange_rate or 0))
    for field, value in local_prices.items():
        if getattr(item, field) == value and getattr(item, field + '_hc'):
            continue   # لم يتغير السعر المحلي: لا نعيد اشتقاق الصعبة منه فلا ينحرف شيء
        setattr(item, field, value)
        setattr(item, field + '_hc', (value / rate).quantize(Decimal('0.0001')) if rate else Decimal('0'))


def _is_hc_header(header) -> bool:
    text = str(header or '').lower()
    return any(marker in text for marker in _HC_MARKERS)


def _label(spec) -> str:
    return spec['header_ar'].replace(' *', '').strip()


# ──────────────────────────────────────────────────────────────
# سياق المشترك
# ──────────────────────────────────────────────────────────────

class ImportContext:
    def __init__(self, tenant, user):
        self.tenant = tenant
        self.user = user
        self.hc_mode = bool(tenant.hard_currency_mode)
        self.rate = Decimal(str(tenant.exchange_rate or 0))
        self.schema = get_product_schema(tenant)
        self.spec = {s['field']: s for s in self.schema}
        self.alias_index = sr.build_alias_index(self.schema)
        # مستخدم الفرع (حين يُسمح للفروع بإدارة المنتجات) يستورد إلى فرعه فقط: منتجاته ومخازنه.
        self.branch = user.branch if getattr(user, 'branch_id', None) else None
        self.categories = list(Category.objects.for_tenant(tenant).filter(is_active=True).order_by('name'))
        self.category_by_key = {c.name.strip().lower(): c for c in self.categories}
        self.category_by_id = {c.id: c for c in self.categories}
        # التصنيفات تُدار من الإدارة فقط (مثل قائمة التصنيفات في القائمة الجانبية)
        self.can_create_categories = bool(user.is_superuser or (user.has_perm_key('add_categories') and not self.branch))
        stocks = list(opening_balance_stocks(tenant, self.branch).order_by('-is_default', 'name'))
        self.stocks = {s.name: s for s in stocks}
        self.has_stock_column = 'stock' in self.spec
        self.default_stock = next((s for s in stocks if s.is_default), stocks[0] if stocks else None)

    def mappable_fields(self):
        """الحقول التي يختار منها المستخدم لكل عمود (حقول العملة الصعبة تُختار عبر مفتاح العملة)."""
        return [s for s in self.schema if s['field'] not in HC_TO_LOCAL]

    def ai_schema(self):
        return [{'field': s['field'], 'description': s['description'], 'required': s.get('required', False)}
                for s in self.mappable_fields()]


# ──────────────────────────────────────────────────────────────
# الخطة: الورقة + صف العناوين + تعيين الأعمدة
# ──────────────────────────────────────────────────────────────

def build_plan(ctx, data, filename, options=None, meta=None):
    """يقرأ الملف ويحدد ما سيُستورد. يرفع sr.ReadError برسالة واضحة إن تعذّرت القراءة."""
    options = options or {}
    meta = meta if meta is not None else {}
    sheets = sr.read_workbook(data, filename)
    overview = sr.sheet_overview(sheets, ctx.alias_index)

    sheet_idx = options.get('sheet')
    if not isinstance(sheet_idx, int) or not 0 <= sheet_idx < len(sheets):
        sheet_idx = sr.pick_sheet(overview)
    rows = sheets[sheet_idx]['rows']
    header_row = options.get('header_row')
    if not isinstance(header_row, int) or not 0 <= header_row < len(rows):
        header_row = overview[sheet_idx]['header_row']

    columns, data_rows = sr.extract_table(sheets[sheet_idx], header_row)
    plan = {
        'filename': filename, 'sheets': overview, 'sheet': sheet_idx, 'header_row': header_row,
        'sheet_rows': rows, 'columns': columns, 'data_rows': data_rows,
    }
    plan['mapping'] = _resolve_mapping(ctx, plan, options, meta)
    plan['field_columns'] = {}
    for col in columns:
        info = plan['mapping'].get(col['index'])
        if info and info.get('field'):
            plan['field_columns'].setdefault(info['field'], []).append(col['index'])
    return plan


def _resolve_mapping(ctx, plan, options, meta):
    columns = plan['columns']
    valid = {s['field'] for s in ctx.schema}
    user_mapping = options.get('mapping')
    mapping = {}
    if isinstance(user_mapping, dict):
        for col in columns:
            field = user_mapping.get(str(col['index']))
            if field in valid:
                mapping[col['index']] = {'field': HC_TO_LOCAL.get(field, field), 'source': 'user'}
    else:
        mapping = sr.match_columns(columns, ctx.alias_index)
        pending = [c for c in columns if mapping.get(c['index'], {}).get('source') != 'exact']
        if pending:
            cache_key = f"{plan['sheet']}:{plan['header_row']}"
            cache = meta.setdefault('ai_mapping', {})
            if cache_key in cache:
                ai = {int(k): v for k, v in cache[cache_key].items()}
            else:
                try:
                    ai = smart_map_columns(
                        [{'index': c['index'], 'header': c['header'], 'samples': c['samples']} for c in pending],
                        ctx.ai_schema(),
                    )
                except Exception:
                    logger.warning('smart_map_columns failed', exc_info=True)
                    ai = {}
                cache[cache_key] = {str(k): v for k, v in ai.items()}
            for idx, field in ai.items():
                if field in valid and mapping.get(idx, {}).get('source') != 'exact':
                    mapping[idx] = {'field': field, 'source': 'ai'}
        # حقل واحد لعمود واحد: عند التعارض يبقى الأوثق (مطابقة حرفية ثم الذكاء الاصطناعي ثم الجزئية)
        rank = {'exact': 0, 'ai': 1, 'partial': 2}
        owner = {}
        for idx in sorted(mapping, key=lambda i: (rank.get(mapping[i]['source'], 3), i)):
            field = mapping[idx]['field']
            base = HC_TO_LOCAL.get(field, field)
            key = (base, _is_hc_header(_column(plan, idx)['header'])) if base in LOCAL_PRICE_FIELDS else field
            if key in owner and mapping[idx]['source'] != 'exact':
                mapping[idx] = {'field': '', 'source': 'conflict'}
            else:
                owner.setdefault(key, idx)

    # عملة أعمدة الأسعار
    currency_options = options.get('currency') if isinstance(options.get('currency'), dict) else {}
    for idx, info in mapping.items():
        field = HC_TO_LOCAL.get(info['field'], info['field'])
        info['field'] = field
        if field not in LOCAL_PRICE_FIELDS:
            continue
        if not ctx.hc_mode:
            info['currency'] = 'local'
            continue
        chosen = currency_options.get(str(idx))
        if chosen in ('local', 'hc'):
            info['currency'], info['currency_source'] = chosen, 'user'
        elif _is_hc_header(_column(plan, idx)['header']):
            info['currency'], info['currency_source'] = 'hc', 'header'
        elif _values_look_hard_currency(ctx, plan, idx):
            info['currency'], info['currency_source'] = 'hc', 'values'
        else:
            info['currency'], info['currency_source'] = 'local', 'default'
        if info['currency'] == 'hc':
            info['field'] = field + '_hc'
    return mapping


def _column(plan, idx):
    return next(c for c in plan['columns'] if c['index'] == idx)


def _values_look_hard_currency(ctx, plan, idx):
    """أسعار أصغر بكثير من سعر الصرف (الوسيط < سعر الصرف ÷ 100) تكاد تكون بالدولار لا بالجنيه."""
    if ctx.rate < 50:
        return False
    numbers = []
    for _, row in plan['data_rows'][:300]:
        value, err = sr.parse_decimal(row[idx] if idx < len(row) else None)
        if value is not None and value > 0:
            numbers.append(value)
    if len(numbers) < 3:
        return False
    numbers.sort()
    return numbers[len(numbers) // 2] < ctx.rate / 100


# ──────────────────────────────────────────────────────────────
# فحص الصفوف (بلا كتابة)
# ──────────────────────────────────────────────────────────────

def _raw(plan, row, field):
    """(القيمة، فهرس العمود) — أول قيمة غير فارغة من الأعمدة المعيّنة لهذا الحقل."""
    for idx in plan['field_columns'].get(field, ()):
        value = row[idx] if idx < len(row) else None
        if not sr.is_empty(value):
            return value, idx
    cols = plan['field_columns'].get(field)
    return None, (cols[0] if cols else None)


def _issue(ctx, plan, excel_row, field, message, value=None, col_idx=None):
    spec = ctx.spec.get(field) or ctx.spec.get(HC_TO_LOCAL.get(field, ''), {})
    column = _column(plan, col_idx) if col_idx is not None else None
    return {
        'row': excel_row, 'field': field, 'field_label': _label(spec) if spec else '',
        'column': column['letter'] if column else '', 'header': column['header'] if column else '',
        'value': sr.display_value(value)[:80] if value is not None else '', 'message': message,
    }


def _category_decisions(ctx, plan, options, meta):
    """قرار لكل اسم تصنيف في الملف غير موجود في النظام: existing / new / none."""
    raws = {}
    for _, row in plan['data_rows']:
        value, _ = _raw(plan, row, 'category')
        text = sr.parse_text(value)
        if text and text.strip().lower() not in ctx.category_by_key:
            raws[text] = raws.get(text, 0) + 1
    if not raws:
        return {}
    user_map = options.get('category_map') if isinstance(options.get('category_map'), dict) else {}
    names = [c.name for c in ctx.categories]
    decisions, pending = {}, []
    for raw, count in raws.items():
        choice = user_map.get(raw)
        if choice is not None:
            choice = str(choice)
            if choice == 'new' and ctx.can_create_categories:
                decisions[raw] = {'action': 'new', 'source': 'user'}
            elif choice.isdigit() and int(choice) in ctx.category_by_id:
                decisions[raw] = {'action': 'existing', 'category_id': int(choice), 'source': 'user'}
            else:
                decisions[raw] = {'action': 'none', 'source': 'user'}
        else:
            similar = sr.closest_name(raw, names)
            if similar:
                decisions[raw] = {'action': 'existing', 'category_id': ctx.category_by_key[similar.strip().lower()].id,
                                  'source': 'similar'}
            else:
                pending.append(raw)
    if pending and names:
        cache = meta.setdefault('ai_categories', {})
        missing = [r for r in pending if r not in cache]
        if missing:
            try:
                found = match_category_names_batch(missing, names)
            except Exception:
                logger.warning('match_category_names_batch failed', exc_info=True)
                found = {}
            for raw in missing:
                cache[raw] = found.get(raw) or ''
        for raw in pending:
            match = cache.get(raw)
            if match and match.strip().lower() in ctx.category_by_key:
                decisions[raw] = {'action': 'existing', 'category_id': ctx.category_by_key[match.strip().lower()].id,
                                  'source': 'ai'}
    for raw in raws:
        if raw not in decisions:
            decisions[raw] = {'action': 'new' if ctx.can_create_categories else 'none', 'source': 'auto'}
    for raw, decision in decisions.items():
        decision['count'] = raws[raw]
    return decisions


def _parse_choice(spec, value):
    key = sr.normalize_text(value)
    for choice_value, label in spec['choices']:
        if key in (sr.normalize_text(choice_value), sr.normalize_text(label)):
            return choice_value
    if spec['field'] == 'item_type':
        for choice_value, aliases in ITEM_TYPE_ALIASES.items():
            if key in aliases and choice_value in dict(spec['choices']):
                return choice_value
    return None


def prepare_rows(ctx, plan, options=None, meta=None):
    """
    يفحص كل صف ويحوّله لقيم جاهزة. لا يكتب في قاعدة البيانات.
    Returns (prepared, decisions) — prepared: [{'row', 'values', 'category', 'stock', 'units',
    'errors', 'warnings', 'skipped'}]
    """
    options = options or {}
    meta = meta if meta is not None else {}
    decisions = _category_decisions(ctx, plan, options, meta)
    default_category = None
    default_id = str(options.get('default_category') or '')
    if default_id.isdigit():
        default_category = ctx.category_by_id.get(int(default_id))
    row_categories = options.get('row_categories') if isinstance(options.get('row_categories'), dict) else {}
    new_names = {raw.strip().lower(): raw for raw, d in decisions.items() if d['action'] == 'new'}

    seen = {'barcode': {}, 'sku': {}, 'name': {}}
    prepared = []
    for excel_row, row in plan['data_rows']:
        entry = {'row': excel_row, 'values': {}, 'category': None, 'stock': ctx.default_stock,
                 'units': [], 'errors': [], 'warnings': [], 'skipped': None}
        prepared.append(entry)
        warn = lambda field, msg, value=None, col=None: entry['warnings'].append(
            _issue(ctx, plan, excel_row, field, msg, value, col))
        fail = lambda field, msg, value=None, col=None: entry['errors'].append(
            _issue(ctx, plan, excel_row, field, msg, value, col))

        name_raw, name_col = _raw(plan, row, 'name')
        name = sr.parse_text(name_raw)
        if not name:
            others = [v for v in row if not sr.is_empty(v)]
            if len(others) <= 1:
                entry['skipped'] = 'empty'
                continue
            fail('name', 'اسم المنتج فارغ — لا يمكن استيراد صف بلا اسم', None, name_col)
            continue
        if sr.is_summary_row(name):
            entry['skipped'] = 'summary'
            warn('name', 'صف إجمالي في نهاية التقرير وليس منتجاً — تم تجاهله', name_raw, name_col)
            continue
        max_name = Item._meta.get_field('name').max_length
        if len(name) > max_name:
            warn('name', f'الاسم أطول من {max_name} حرفاً — تم اختصاره', name_raw, name_col)
            name = name[:max_name]
        entry['values']['name'] = name

        # الحقول العادية حسب نوعها
        for field in plan['field_columns']:
            if field in SYNTHETIC_FIELDS:
                continue
            spec = ctx.spec.get(field)
            if not spec:
                continue
            value, col = _raw(plan, row, field)
            if sr.is_empty(value):
                continue
            dtype = spec['dtype']
            if dtype == 'decimal':
                number, err = sr.parse_decimal(value)
                if err:
                    warn(field, f'{err} — تُرك هذا الحقل فارغاً', value, col)
                elif number < 0:
                    warn(field, 'قيمة سالبة — تُرك هذا الحقل فارغاً', value, col)
                else:
                    entry['values'][field] = number
            elif dtype == 'bool':
                flag, err = sr.parse_bool(value)
                if err:
                    warn(field, f'{err} — طُبّقت القيمة الافتراضية', value, col)
                else:
                    entry['values'][field] = flag
            elif dtype == 'date':
                parsed, err = sr.parse_date(value)
                if err:
                    warn(field, f'{err} — تُرك هذا الحقل فارغاً', value, col)
                else:
                    entry['values'][field] = parsed
            elif dtype == 'choice_fixed':
                choice = _parse_choice(spec, value)
                if choice is None:
                    choice = spec['choices'][0][0]
                    warn(field, f'قيمة غير معروفة — اعتُبر «{spec["choices"][0][1]}»', value, col)
                entry['values'][field] = choice
            else:
                text = sr.parse_text(value)
                if field in ('barcode', 'sku') and isinstance(value, str) and 'e+' in value.lower():
                    warn(field, 'الرقم مكتوب بصيغة علمية (مثل 6.29E+12) وقد فقد خاناته — نسّق العمود كنص في Excel',
                         value, col)
                try:
                    max_len = Item._meta.get_field(field).max_length
                except Exception:
                    max_len = None
                if max_len and len(text) > max_len:
                    warn(field, f'النص أطول من {max_len} حرفاً — تم اختصاره', value, col)
                    text = text[:max_len]
                entry['values'][field] = text

        # التصنيف
        cat_raw, cat_col = _raw(plan, row, 'category')
        cat_text = sr.parse_text(cat_raw)
        if cat_text:
            existing = ctx.category_by_key.get(cat_text.strip().lower())
            decision = decisions.get(cat_text)
            if existing:
                entry['category'] = ('existing', existing)
            elif decision and decision['action'] == 'existing':
                entry['category'] = ('existing', ctx.category_by_id[decision['category_id']])
            elif decision and decision['action'] == 'new':
                entry['category'] = ('new', cat_text)
            else:
                fallback = default_category
                entry['category'] = ('existing', fallback) if fallback else None
                warn('category', f'التصنيف «{cat_text}» غير موجود — '
                     + (f'وُضع في «{fallback.name}»' if fallback else 'استُورد المنتج بلا تصنيف'), cat_raw, cat_col)
        else:
            suggested = sr.parse_text(row_categories.get(str(excel_row)))
            if suggested and suggested.strip().lower() in ctx.category_by_key:
                entry['category'] = ('existing', ctx.category_by_key[suggested.strip().lower()])
            elif suggested and suggested.strip().lower() in new_names:
                entry['category'] = ('new', new_names[suggested.strip().lower()])
            elif default_category:
                entry['category'] = ('existing', default_category)
            else:
                entry['missing_category'] = True

        # الوحدات
        base_unit = sr.parse_text(_raw(plan, row, 'base_unit_name')[0])
        large_unit, large_col = _raw(plan, row, 'large_unit_name')
        large_unit = sr.parse_text(large_unit)
        count_raw, count_col = _raw(plan, row, 'large_unit_count')
        if large_unit and not base_unit and 'large_unit_count' not in plan['field_columns']:
            # ملف بعمود وحدة واحد بلا عدد (مثل «العبوة»: Box / قطعة) — هي وحدة المنتج الأساسية
            base_unit, large_unit = large_unit, ''
        if large_unit:
            count, err = sr.parse_decimal(count_raw)
            if err or not count or count <= 0:
                warn('large_unit_count', f'لم يُحدَّد كم وحدة أساسية داخل «{large_unit}» — لم تُضف الوحدة الأكبر',
                     count_raw, count_col)
                large_unit = ''
            elif not base_unit:
                base_unit = DEFAULT_BASE_UNIT
                warn('base_unit_name', f'الوحدة الأساسية غير محددة — اعتُبرت «{DEFAULT_BASE_UNIT}»', None, large_col)
        if base_unit:
            entry['units'].append({'name': base_unit, 'factor': 1})
        if large_unit:
            entry['units'].append({'name': large_unit, 'factor': float(count)})

        # الكمية الافتتاحية والمخزن
        qty_raw, qty_col = _raw(plan, row, 'opening_quantity')
        qty, err = sr.parse_decimal(qty_raw)
        if err:
            warn('opening_quantity', f'{err} — لم تُضف كمية افتتاحية', qty_raw, qty_col)
            qty = None
        elif qty is not None and qty < 0:
            warn('opening_quantity', 'كمية سالبة — لم تُضف كمية افتتاحية', qty_raw, qty_col)
            qty = None
        entry['opening_quantity'] = qty
        if ctx.has_stock_column:
            stock_raw, stock_col = _raw(plan, row, 'stock')
            stock_text = sr.parse_text(stock_raw)
            if stock_text:
                match = sr.closest_name(stock_text, list(ctx.stocks))
                if match:
                    entry['stock'] = ctx.stocks[match]
                else:
                    entry['stock'] = None
                    if qty:
                        warn('stock', f'المخزن «{stock_text}» غير موجود — استُورد المنتج بلا كمية افتتاحية',
                             stock_raw, stock_col)

        # التكرار داخل الملف نفسه
        for key, value in (('barcode', entry['values'].get('barcode')), ('sku', entry['values'].get('sku')),
                           ('name', name)):
            if not value:
                continue
            norm = sr.normalize_text(value) if key == 'name' else str(value).strip().lower()
            if norm in seen[key]:
                label = {'barcode': 'الباركود', 'sku': 'الرمز', 'name': 'الاسم'}[key]
                warn(key, f'مكرر في الملف: نفس {label} في الصف {seen[key][norm]} — سيُحدَّث المنتج نفسه ولن يُنشأ منتج ثانٍ',
                     value, _raw(plan, row, key)[1])
                break
            seen[key][norm] = excel_row
    return prepared, decisions


# ──────────────────────────────────────────────────────────────
# المعاينة
# ──────────────────────────────────────────────────────────────

def analyze_products(ctx, plan, prepared, decisions, options=None):
    """ملخص المعاينة كـ JSON للواجهة."""
    options = options or {}
    errors = [e for p in prepared for e in p['errors']]
    warnings = [w for p in prepared for w in p['warnings']]
    ready = [p for p in prepared if not p['errors'] and not p['skipped']]
    missing_category = [{'row': p['row'], 'name': p['values']['name']} for p in ready if p.get('missing_category')]

    existing = Item.objects.for_tenant(ctx.tenant)
    barcodes = {b for b in existing.exclude(barcode='').values_list('barcode', flat=True)}
    names = {n.strip().lower() for n in existing.values_list('name', flat=True)}
    estimated_updates = sum(1 for p in ready if (p['values'].get('barcode') in barcodes
                                                 or p['values']['name'].strip().lower() in names))

    fields = []
    for spec in ctx.mappable_fields():
        fields.append({'field': spec['field'], 'label': _label(spec), 'required': bool(spec.get('required')),
                       'is_price': spec['field'] in LOCAL_PRICE_FIELDS})

    columns = []
    for col in plan['columns']:
        info = plan['mapping'].get(col['index'], {})
        field = info.get('field') or ''
        columns.append({**col, 'field': HC_TO_LOCAL.get(field, field), 'source': info.get('source', ''),
                        'currency': info.get('currency', ''), 'currency_source': info.get('currency_source', '')})

    header_candidates = []
    for idx, row in enumerate(plan['sheet_rows'][:sr.MAX_SCAN_ROWS]):
        text = ' | '.join(sr.display_value(v) for v in row if not sr.is_empty(v))
        if text:
            header_candidates.append({'index': idx, 'row': idx + 1, 'text': text[:140]})

    preview_fields = [f for f in ('name', 'category', 'barcode', 'sku', 'cost_price', 'selling_price',
                                  'min_selling_price', 'cost_price_hc', 'selling_price_hc', 'min_selling_price_hc',
                                  'base_unit_name', 'opening_quantity') if f in plan['field_columns']]
    preview_rows = []
    for p in prepared:
        if p['skipped'] == 'empty':
            continue
        cells = []
        for f in preview_fields:
            if f == 'category':
                cat = p['category']
                cells.append(cat[1].name if cat and cat[0] == 'existing' else (f'{cat[1]} (جديد)' if cat else ''))
            elif f == 'base_unit_name':
                cells.append(' / '.join(u['name'] if u['factor'] == 1 else f"{u['name']} ×{u['factor']:g}"
                                        for u in p['units']))
            elif f == 'opening_quantity':
                cells.append(sr.display_value(p.get('opening_quantity')))
            else:
                cells.append(sr.display_value(p['values'].get(f, '')))
        preview_rows.append({'row': p['row'], 'cells': cells,
                             'status': 'error' if p['errors'] else ('skip' if p['skipped'] else
                                                                     ('warning' if p['warnings'] else 'ok'))})
        if len(preview_rows) >= PREVIEW_ROWS:
            break
    preview_labels = []
    for f in preview_fields:
        spec = ctx.spec.get(f)
        preview_labels.append(_label(spec) if spec else f)

    from apps.core.utils import currency_symbol
    return {
        'filename': plan['filename'],
        'sheets': [{k: s[k] for k in ('index', 'name', 'data_rows', 'known_headers')} for s in plan['sheets']],
        'sheet': plan['sheet'], 'header_row': plan['header_row'], 'header_candidates': header_candidates,
        'columns': columns, 'fields': fields,
        'missing_required': [_label(s) for s in ctx.schema if s.get('required') and s['field'] not in plan['field_columns']],
        'hc_mode': ctx.hc_mode, 'rate': str(ctx.rate),
        'hc_symbol': currency_symbol(ctx.tenant.hard_currency or 'USD') if ctx.hc_mode else '',
        'local_symbol': currency_symbol(ctx.tenant.currency or 'SDG'),
        'categories': [{'id': c.id, 'name': c.name} for c in ctx.categories],
        'can_create_categories': ctx.can_create_categories,
        'category_decisions': [{'raw': raw, **d} for raw, d in sorted(decisions.items(), key=lambda x: -x[1]['count'])],
        'missing_category_rows': missing_category[:3000],
        'default_category': str(options.get('default_category') or ''),
        'summary': {
            'total_rows': sum(1 for p in prepared if p['skipped'] != 'empty'),
            'ready': len(ready),
            'with_errors': sum(1 for p in prepared if p['errors']),
            'with_warnings': sum(1 for p in ready if p['warnings']),
            'skipped': sum(1 for p in prepared if p['skipped'] == 'summary'),
            'missing_category': len(missing_category),
            'estimated_updates': estimated_updates,
            'estimated_new': len(ready) - estimated_updates,
        },
        'errors': errors[:MAX_LISTED_ISSUES], 'warnings': warnings[:MAX_LISTED_ISSUES],
        'preview': {'labels': preview_labels, 'rows': preview_rows},
    }


# ──────────────────────────────────────────────────────────────
# الاستيراد
# ──────────────────────────────────────────────────────────────

def commit_products(ctx, plan, prepared, decisions):
    """يكتب الصفوف السليمة. Returns {'created', 'updated', 'errors', 'warnings', 'possible_duplicates', 'skipped'}."""
    tenant, user = ctx.tenant, ctx.user
    created = updated = 0
    errors = [e for p in prepared for e in p['errors']]
    warnings = [w for p in prepared for w in p['warnings']]
    possible_duplicates = []
    failed_rows = {e['row'] for e in errors}
    created_rows = {}   # item_id → رقم الصف الذي أنشأه في هذا الاستيراد
    merged = 0

    # التصنيفات الجديدة المستخدمة فعلاً (اسم واحد لكل كتابة متقاربة)
    new_categories = {}
    for p in prepared:
        if p['errors'] or p['skipped'] or not p['category'] or p['category'][0] != 'new':
            continue
        key = sr.normalize_text(p['category'][1])
        if key not in new_categories:
            with transaction.atomic():
                category = Category.objects.for_tenant(tenant).filter(name__iexact=p['category'][1].strip()).first()
                if category is None:
                    category = Category.objects.create(tenant=tenant, name=p['category'][1].strip()[:200],
                                                       is_active=True)
            new_categories[key] = category

    for p in prepared:
        if p['errors'] or p['skipped']:
            continue
        i = p['row']
        values = p['values']
        try:
            with transaction.atomic():
                category = None
                if p['category']:
                    category = p['category'][1] if p['category'][0] == 'existing' else \
                        new_categories[sr.normalize_text(p['category'][1])]

                kwargs = {'tenant': tenant, 'created_by': user, 'updated_by': user, 'branch': ctx.branch,
                          'name': values['name'], 'category': category, 'is_active': True}
                for field, value in values.items():
                    if field in SYNTHETIC_FIELDS or field in BATCH_FIELDS or (ctx.hc_mode and field in LOCAL_PRICE_FIELDS):
                        continue
                    if field not in ctx.spec or value is None:
                        continue
                    kwargs[field] = value

                # الأسعار المحلية هي المرجع والصعبة تُشتق منها، ما لم يحمل الملف سعراً صعباً مختلفاً عنها
                # (ملف صدّره النظام ثم عدّل المستخدم عمود الصعبة عمداً — فتُعتمد الصعبة).
                local_prices = {}
                if ctx.hc_mode:
                    for local_field in LOCAL_PRICE_FIELDS:
                        value = values.get(local_field)
                        if value is None:
                            continue
                        hc_in_file = values.get(local_field + '_hc')
                        if not hc_in_file or (ctx.rate and abs(value / ctx.rate - hc_in_file) <= Decimal('0.0001')):
                            local_prices[local_field] = value

                dup_match = find_existing_item_match(
                    tenant, name=values['name'],
                    generic_name=values.get('generic_name', ''),
                    dosage_form=values.get('dosage_form', ''),
                    strength=values.get('strength', ''),
                    barcode=values.get('barcode', ''),
                )
                # «exact» في dedup يطابق على الاسم العلمي وحده عند وجوده: FUCIDEM CREAM وFUCIDEM OINT
                # (كلاهما fucidic acid) منتجان مختلفان. التحديث بدل الإنشاء يشترط نفس الاسم التجاري.
                if (dup_match and dup_match['match_type'] == 'exact'
                        and sr.normalize_text(dup_match['item'].name) != sr.normalize_text(values['name'])):
                    dup_match = None

                if dup_match and dup_match['match_type'] in ('barcode', 'exact'):
                    # إعادة استيراد قائمة متداخلة تُحدّث المنتج الموجود ولا تنشئ نسخة مكررة.
                    existing = dup_match['item']
                    merged_into = created_rows.get(existing.id)
                    if ctx.branch is not None and existing.branch_id != ctx.branch.id:
                        errors.append(_issue(ctx, plan, i, 'name',
                                             f'المنتج «{existing.name}» موجود في كتالوج الإدارة أو فرع آخر ولا يمكن لفرعك تعديله',
                                             values['name'], _raw(plan, [], 'name')[1]))
                        failed_rows.add(i)
                        continue
                    update_fields = ['updated_by']
                    simple = ('tax_rate',) if ctx.hc_mode else ('cost_price', 'selling_price', 'min_selling_price',
                                                                'tax_rate')
                    for field in simple:
                        if field in values:
                            setattr(existing, field, values[field])
                            update_fields.append(field)
                    existing.updated_by = user
                    if ctx.hc_mode:
                        # تُحدَّث الأسعار التي تغيّرت فعلاً فقط؛ غير المتغيرة تبقى حرفياً، لأن الصعبة مخزّنة
                        # بـ4 خانات فإعادة ضربها في سعر الصرف تنحرف عن المحلي (10,000.00 ← 9,999.75).
                        for hc_field, local_field in HC_TO_LOCAL.items():
                            new_hc = values.get(hc_field)
                            if new_hc and new_hc != getattr(existing, hc_field):
                                setattr(existing, hc_field, new_hc)
                                setattr(existing, local_field, (new_hc * ctx.rate).quantize(Decimal('0.01')))
                        _apply_local_prices(existing, tenant, local_prices)
                        update_fields += ['cost_price', 'selling_price', 'min_selling_price',
                                          'cost_price_hc', 'selling_price_hc', 'min_selling_price_hc']
                    existing.save(update_fields=list(set(update_fields)))
                    if merged_into:
                        # المنتج أُنشئ من صف سابق في نفس الملف: ليس «منتجاً موجوداً مسبقاً»
                        merged += 1
                        warnings.append(_issue(ctx, plan, i, 'name',
                                               f'اعتُبر نفس منتج الصف {merged_into} («{existing.name}») فدُمج معه '
                                               'ولم يُنشأ منتج جديد — راجعه إن كانا منتجين مختلفين',
                                               values['name'], _raw(plan, [], 'name')[1]))
                        continue
                    updated += 1
                    if dup_match['match_type'] in ('barcode', 'sku') and \
                            sr.normalize_text(existing.name) != sr.normalize_text(values['name']):
                        key = 'barcode' if dup_match['match_type'] == 'barcode' else 'sku'
                        label = 'الباركود' if key == 'barcode' else 'الرمز'
                        warnings.append(_issue(ctx, plan, i, key,
                                               f'نفس {label} مسجّل للمنتج «{existing.name}» فحُدّثت أسعاره بدل إنشاء '
                                               f'«{values["name"]}» — راجعه إن كانا منتجين مختلفين',
                                               values.get(key), _raw(plan, [], key)[1]))
                    if p.get('opening_quantity'):
                        warnings.append(_issue(ctx, plan, i, 'opening_quantity',
                                               f'المنتج «{existing.name}» موجود مسبقاً فحُدّثت أسعاره فقط، ولم تُضف الكمية '
                                               'الافتتاحية (عدّل رصيده من شاشة الرصيد الافتتاحي)',
                                               p['opening_quantity'], _raw(plan, [], 'opening_quantity')[1]))
                    continue

                if dup_match and dup_match['match_type'] == 'fuzzy':
                    possible_duplicates.append({
                        'row': i, 'field': 'name',
                        'existing_item_id': dup_match['item'].id,
                        'existing_item_name': dup_match['item'].name,
                        'confidence': dup_match['confidence'],
                    })

                item = Item(**kwargs)
                if ctx.hc_mode:
                    _apply_hc_prices(item, tenant)
                    _apply_local_prices(item, tenant, local_prices)
                item.save()
                created_rows[item.id] = i

                if p['units']:
                    _save_item_units(item, json.dumps(p['units']), tenant)

                opening_qty = p.get('opening_quantity')
                target_stock = p['stock']
                if opening_qty and target_stock and item.item_type != 'service':
                    sq = StockQuantity.objects.select_for_update().get(tenant=tenant, stock=target_stock, item=item)
                    sq.quantity = opening_qty
                    sq.opening_quantity = opening_qty
                    sq.save(update_fields=['quantity', 'opening_quantity'])
                    StockMovement.objects.create(
                        tenant=tenant, item=item, stock=target_stock,
                        movement_type='opening_in', direction='in',
                        quantity=opening_qty, unit_cost=item.cost_price or Decimal('0'),
                        movement_date=timezone.localdate(),
                        reference_type='opening_balance', reference_id=sq.id,
                        balance_after=sq.quantity, notes='رصيد افتتاحي عبر استيراد المنتجات',
                    )
                    batch_number = values.get('batch_number', '')
                    expiry_date = values.get('expiry_date')
                    if batch_number or expiry_date:
                        ItemBatch.objects.create(
                            tenant=tenant, item=item, stock=target_stock,
                            batch_number=batch_number or '', expiry_date=expiry_date,
                            quantity_received=opening_qty, quantity_remaining=opening_qty,
                            purchase_date=timezone.localdate(),
                        )
                created += 1
        except IntegrityError as exc:
            logger.error('product import row %d: %s', i, exc)
            errors.append(_issue(ctx, plan, i, 'sku', 'رمز المنتج (SKU) أو الباركود مستخدم لمنتج آخر',
                                 values.get('sku') or values.get('barcode'), _raw(plan, [], 'sku')[1]))
            failed_rows.add(i)
        except (InvalidOperation, ValueError) as exc:
            logger.error('product import row %d: %s', i, exc)
            errors.append(_issue(ctx, plan, i, '', f'قيمة غير صالحة: {exc}'))
            failed_rows.add(i)
        except Exception as exc:
            logger.error('product import row %d: %s', i, exc, exc_info=True)
            errors.append(_issue(ctx, plan, i, '', f'خطأ غير متوقع: {exc}'))
            failed_rows.add(i)

    errors.sort(key=lambda e: e['row'])
    return {'created': created, 'updated': updated, 'merged': merged, 'errors': errors, 'warnings': warnings,
            'possible_duplicates': possible_duplicates,
            'skipped': sum(1 for p in prepared if p['skipped'] == 'summary'),
            'categories_created': len(new_categories), 'failed_rows': sorted(failed_rows)}


def build_errors_workbook(plan, result):
    """ملف Excel بالصفوف التي لم تُستورد كما كانت في ملف العميل + عمود السبب، ليصححها ويعيد رفعها."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    reasons = {}
    for e in result['errors']:
        where = f"عمود {e['column']} «{e['header']}»: " if e.get('column') else ''
        reasons.setdefault(e['row'], []).append(where + e['message'])
    wb = Workbook()
    ws = wb.active
    ws.title = 'صفوف لم تُستورد'
    ws.sheet_view.rightToLeft = True
    header = plan['sheet_rows'][plan['header_row']] if plan['sheet_rows'] else []
    ws.append(['رقم الصف في ملفك', 'سبب عدم الاستيراد'] + [sr.display_value(h) for h in header])
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill(start_color='B91C1C', end_color='B91C1C', fill_type='solid')
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
    rows = dict(plan['data_rows'])
    for row_no in sorted(reasons):
        ws.append([row_no, ' | '.join(reasons[row_no])] + list(rows.get(row_no, [])))
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 60
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def run_import(tenant, user, data, filename, options=None, meta=None, analyze_only=False):
    """نقطة الدخول المشتركة للمعاينة والاستيراد. Returns (payload, plan) — plan None عند فشل القراءة."""
    ctx = ImportContext(tenant, user)
    try:
        plan = build_plan(ctx, data, filename, options, meta)
    except sr.ReadError as exc:
        return {'fatal': str(exc)}, None
    prepared, decisions = prepare_rows(ctx, plan, options, meta)
    if analyze_only:   # حتى بلا صفوف: تعرض الواجهة الأوراق وصفوف العناوين ليختار المستخدم غيرها
        return analyze_products(ctx, plan, prepared, decisions, options), plan
    if not plan['data_rows']:
        return {'fatal': 'لم نجد صفوف بيانات تحت صف العناوين في هذه الورقة.'}, plan
    if 'name' not in plan['field_columns']:
        return {'fatal': 'لم نتعرّف على عمود «اسم المنتج» في الملف. حدّده يدوياً من خطوة المعاينة.'}, plan
    return commit_products(ctx, plan, prepared, decisions), plan


def import_products(tenant, uploaded_file, user, options=None):
    """استيراد مباشر بلا معاينة. Returns {'created', 'updated', 'errors', 'warnings', 'possible_duplicates', ...}"""
    empty_result = {'created': 0, 'updated': 0, 'possible_duplicates': [], 'warnings': []}
    if products_import_blocked_reason(tenant):
        return {**empty_result, 'errors': [{'row': 0, 'field': '', 'message': 'أضف تصنيفاً واحداً على الأقل أولاً'}]}
    data = uploaded_file.read()
    result, _ = run_import(tenant, user, data, uploaded_file.name, options)
    if 'fatal' in result:
        return {**empty_result, 'errors': [{'row': 0, 'field': '', 'message': result['fatal']}]}
    return result
