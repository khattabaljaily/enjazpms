"""
الاستيراد الذكي لـ العملاء والموردين والمناديب والتصنيفات — نفس فلسفة استيراد المنتجات:
يرفع المستخدم أي ملف (قالبنا أو تصدير من نظام آخر) فيكتشف النظام الورقة وصف العناوين ويطابق الأعمدة
(أسماء بديلة ثم الذكاء الاصطناعي)، ثم يعرض معاينة (جديد / موجود مسبقاً / مكرر في الملف / أخطاء) قبل أي حفظ.

مستورد واحد عام؛ كل نوع بيانات يضيف فقط: schema (حقوله وأسماؤها البديلة)، وكيف يُكتشف الموجود مسبقاً،
وكيف يُنشأ السجل. المنطق المشترك لقراءة الملف في smart_reader (نفسه المستعمل للمنتجات).
"""
import logging
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.ai.services import smart_map_columns
from apps.core.io_utils import clean_email, clean_phone

from . import smart_reader as sr
from .schemas import get_agent_schema, get_category_schema, get_customer_schema, get_supplier_schema

logger = logging.getLogger('data_import')

MAX_LISTED_ISSUES = 500
PREVIEW_ROWS = 15
NAME_MAX = 200


# ──────────────────────────────────────────────────────────────
# تعريف الأنواع
# ──────────────────────────────────────────────────────────────

def _customer_model():
    from apps.customers.models import Customer
    return Customer


def _supplier_model():
    from apps.suppliers.models import Supplier
    return Supplier


def _agent_model():
    from apps.agents.models import Agent
    return Agent


ENTITIES = {
    'customers': {'slug': 'customer', 'label': 'عميل', 'plural': 'العملاء', 'schema': get_customer_schema, 'model': _customer_model,
                  'branch_scoped': True, 'audit': True, 'upsert': False, 'phone_field': True},
    'suppliers': {'slug': 'supplier', 'label': 'مورد', 'plural': 'الموردين', 'schema': get_supplier_schema, 'model': _supplier_model,
                  'branch_scoped': True, 'audit': True, 'upsert': False, 'phone_field': True},
    'agents': {'slug': 'agent', 'label': 'مندوب', 'plural': 'المناديب', 'schema': get_agent_schema, 'model': _agent_model,
               'branch_scoped': True, 'audit': False, 'upsert': False, 'phone_field': True},
    'categories': {'slug': 'category', 'label': 'تصنيف', 'plural': 'التصنيفات', 'schema': get_category_schema, 'model': None,
                   'branch_scoped': False, 'audit': False, 'upsert': True, 'phone_field': False},
}


class Context:
    def __init__(self, entity_key, tenant, user):
        self.key = entity_key
        self.cfg = ENTITIES[entity_key]
        self.tenant = tenant
        self.user = user
        self.branch = user.branch if getattr(user, 'branch_id', None) else None
        self.schema = self.cfg['schema'](tenant)
        self.spec = {s['field']: s for s in self.schema}
        self.alias_index = sr.build_alias_index(self.schema)
        self.label = self.cfg['label']
        self.is_category = entity_key == 'categories'

    def model(self):
        if self.is_category:
            from apps.items.models import Category
            return Category
        return self.cfg['model']()

    def existing_queryset(self):
        model = self.model()
        if self.is_category:
            return model.objects.for_tenant(self.tenant)
        qs = model.unscoped.filter(tenant=self.tenant)
        if self.branch:
            qs = qs.filter(branch_id=self.branch.id)
        return qs

    def ai_schema(self):
        return [{'field': s['field'], 'description': s['description'], 'required': s.get('required', False)}
                for s in self.schema]


# ──────────────────────────────────────────────────────────────
# الخطة: الورقة + صف العناوين + تعيين الأعمدة
# ──────────────────────────────────────────────────────────────

def build_plan(ctx, data, filename, options=None, meta=None):
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
    plan = {'filename': filename, 'sheets': overview, 'sheet': sheet_idx, 'header_row': header_row,
            'sheet_rows': rows, 'columns': columns, 'data_rows': data_rows}
    plan['mapping'] = _resolve_mapping(ctx, plan, options, meta)
    plan['field_columns'] = {}
    for col in columns:
        info = plan['mapping'].get(col['index'])
        if info and info.get('field'):
            plan['field_columns'].setdefault(info['field'], []).append(col['index'])
    return plan


def _resolve_mapping(ctx, plan, options, meta):
    columns = plan['columns']
    valid = set(ctx.spec)
    user_mapping = options.get('mapping')
    if isinstance(user_mapping, dict):
        mapping = {}
        for col in columns:
            field = user_mapping.get(str(col['index']))
            if field in valid:
                mapping[col['index']] = {'field': field, 'source': 'user'}
        return mapping

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
    # حقل واحد لعمود واحد: عند التعارض يبقى الأوثق
    rank = {'exact': 0, 'ai': 1, 'partial': 2}
    owner = {}
    for idx in sorted(mapping, key=lambda i: (rank.get(mapping[i]['source'], 3), i)):
        field = mapping[idx]['field']
        if field in owner:
            mapping[idx] = {'field': '', 'source': 'conflict'}
        else:
            owner[field] = idx
    return mapping


# ──────────────────────────────────────────────────────────────
# فحص الصفوف (بلا كتابة)
# ──────────────────────────────────────────────────────────────

def _column(plan, idx):
    return next(c for c in plan['columns'] if c['index'] == idx)


def _raw(plan, row, field):
    for idx in plan['field_columns'].get(field, ()):
        value = row[idx] if idx < len(row) else None
        if not sr.is_empty(value):
            return value, idx
    cols = plan['field_columns'].get(field)
    return None, (cols[0] if cols else None)


def _issue(ctx, plan, excel_row, field, message, value=None, col_idx=None):
    spec = ctx.spec.get(field, {})
    column = _column(plan, col_idx) if col_idx is not None else None
    return {'row': excel_row, 'field': field, 'field_label': _label(spec) if spec else '',
            'column': column['letter'] if column else '', 'header': column['header'] if column else '',
            'value': sr.display_value(value)[:80] if value is not None else '', 'message': message}


def _label(spec):
    import re
    return re.sub(r'\s*\*\s*$', '', spec.get('header_ar', spec.get('field', ''))).strip()


def _choice_value(spec, raw):
    key = sr.normalize_text(raw)
    for value, label in spec.get('choices', ()):
        if key in (sr.normalize_text(value), sr.normalize_text(label)):
            return value
    return None


def _parse_value(ctx, plan, excel_row, spec, raw, col_idx, errors, warnings):
    field, dtype = spec['field'], spec['dtype']
    if sr.is_empty(raw):
        return None
    if dtype in ('decimal', 'number'):
        number, err = sr.parse_decimal(raw)
        if err:
            errors.append(_issue(ctx, plan, excel_row, field, err, raw, col_idx))
            return None
        return int(number) if dtype == 'number' else number
    if dtype == 'bool':
        flag, err = sr.parse_bool(raw)
        if err:
            warnings.append(_issue(ctx, plan, excel_row, field, err, raw, col_idx))
        return flag
    if dtype == 'choice_fixed':
        value = _choice_value(spec, raw)
        if value is None:
            warnings.append(_issue(ctx, plan, excel_row, field, 'قيمة غير معروفة — تُركت فارغة', raw, col_idx))
        return value or ''
    if field == 'phone':
        phone = clean_phone(sr.parse_text(raw)) or ''
        if len(phone) > 20:
            warnings.append(_issue(ctx, plan, excel_row, field, 'رقم هاتف أطول من المسموح — تُرك فارغاً', raw, col_idx))
            return ''
        return phone
    if field == 'email':
        email = clean_email(sr.parse_text(raw)) or ''
        if not email:
            warnings.append(_issue(ctx, plan, excel_row, field, 'بريد إلكتروني غير صالح — تُرك فارغاً', raw, col_idx))
        return email
    return sr.parse_text(raw)


def _phones_conflict(a, b):
    """نفس الاسم لكن رقمان مختلفان = شخصان مختلفان؛ رقم فارغ في أحدهما = نفس الشخص على الأرجح."""
    return bool(a) and bool(b) and a != b


def prepare_rows(ctx, plan):
    qs = ctx.existing_queryset()
    existing = {}
    if ctx.cfg['phone_field']:
        for name, phone in qs.values_list('name', 'phone'):
            existing.setdefault(sr.normalize_text(name), []).append(phone or '')
    else:
        for name in qs.values_list('name', flat=True):
            existing.setdefault(sr.normalize_text(name), []).append('')

    seen = {}
    prepared = []
    for excel_row, row in plan['data_rows']:
        errors, warnings, values = [], [], {}
        for spec in ctx.schema:
            field = spec['field']
            if field not in plan['field_columns']:
                continue
            raw, col_idx = _raw(plan, row, field)
            value = _parse_value(ctx, plan, excel_row, spec, raw, col_idx, errors, warnings)
            if value is not None:
                values[field] = value

        name = values.get('name', '')
        entry = {'row': excel_row, 'values': values, 'errors': errors, 'warnings': warnings, 'status': 'new'}
        prepared.append(entry)
        if not name:
            if not errors and all(sr.is_empty(v) for v in row):
                entry['status'] = 'empty'
            else:
                entry['status'] = 'error'
                errors.append(_issue(ctx, plan, excel_row, 'name', f'اسم {ctx.label} مطلوب', None,
                                     (plan['field_columns'].get('name') or [None])[0]))
            continue
        if sr.is_summary_row(name):
            entry['status'] = 'summary'
            continue
        if len(name) > NAME_MAX:
            entry['status'] = 'error'
            errors.append(_issue(ctx, plan, excel_row, 'name', f'الاسم أطول من {NAME_MAX} حرفاً', name,
                                 (plan['field_columns'].get('name') or [None])[0]))
            continue
        if errors:
            entry['status'] = 'error'
            continue

        key, phone = sr.normalize_text(name), values.get('phone', '')
        if ctx.cfg['upsert']:                      # التصنيف: الموجود يُحدَّث، والمكرر في الملف يُدمج
            if key in seen:
                entry['status'] = 'duplicate'
            elif key in existing:
                entry['status'] = 'update'
            seen[key] = True
            continue
        if any(not _phones_conflict(phone, p) for p in existing.get(key, ())):
            entry['status'] = 'exists'
        elif any(not _phones_conflict(phone, p) for p in seen.get(key, ())):
            entry['status'] = 'duplicate'
        seen.setdefault(key, []).append(phone)
    return prepared


# ──────────────────────────────────────────────────────────────
# المعاينة
# ──────────────────────────────────────────────────────────────

STATUS_LABELS = {'new': 'جديد', 'update': 'تحديث', 'exists': 'موجود مسبقاً', 'duplicate': 'مكرر في الملف',
                 'error': 'خطأ', 'summary': 'إجمالي'}


def analyze(ctx, plan, prepared):
    counts = {}
    for p in prepared:
        counts[p['status']] = counts.get(p['status'], 0) + 1
    errors = [e for p in prepared for e in p['errors']]
    warnings = [w for p in prepared for w in p['warnings']]

    fields = [{'field': s['field'], 'label': _label(s), 'required': bool(s.get('required'))} for s in ctx.schema]
    columns = []
    for col in plan['columns']:
        info = plan['mapping'].get(col['index'], {})
        columns.append({**col, 'field': info.get('field', ''), 'source': info.get('source', '')})

    header_candidates = []
    for idx, row in enumerate(plan['sheet_rows'][:sr.MAX_SCAN_ROWS]):
        text = ' | '.join(sr.display_value(v) for v in row if not sr.is_empty(v))
        if text:
            header_candidates.append({'index': idx, 'row': idx + 1, 'text': text[:140]})

    preview_fields = [s['field'] for s in ctx.schema if s['field'] in plan['field_columns']][:6]
    preview_rows = []
    for p in prepared:
        if p['status'] == 'empty':
            continue
        preview_rows.append({'row': p['row'], 'status': p['status'], 'status_label': STATUS_LABELS.get(p['status'], ''),
                             'cells': [sr.display_value(p['values'].get(f, '')) for f in preview_fields]})
        if len(preview_rows) >= PREVIEW_ROWS:
            break

    importable = counts.get('new', 0) + counts.get('update', 0)
    return {
        'filename': plan['filename'], 'entity': ctx.key, 'label': ctx.label,
        'sheets': [{k: s[k] for k in ('index', 'name', 'data_rows', 'known_headers')} for s in plan['sheets']],
        'sheet': plan['sheet'], 'header_row': plan['header_row'], 'header_candidates': header_candidates,
        'columns': columns, 'fields': fields,
        'missing_required': [_label(s) for s in ctx.schema if s.get('required') and s['field'] not in plan['field_columns']],
        'summary': {
            'total_rows': sum(counts.get(k, 0) for k in ('new', 'update', 'exists', 'duplicate', 'error')),
            'new': counts.get('new', 0), 'update': counts.get('update', 0), 'exists': counts.get('exists', 0),
            'duplicate': counts.get('duplicate', 0), 'with_errors': counts.get('error', 0),
            'importable': importable, 'with_warnings': sum(1 for p in prepared if p['warnings']),
        },
        'errors': errors[:MAX_LISTED_ISSUES], 'warnings': warnings[:MAX_LISTED_ISSUES],
        'preview': {'labels': [_label(ctx.spec[f]) for f in preview_fields], 'rows': preview_rows},
    }


# ──────────────────────────────────────────────────────────────
# الاستيراد
# ──────────────────────────────────────────────────────────────

def commit(ctx, plan, prepared):
    created = updated = 0
    errors = list(e for p in prepared for e in p['errors'])
    skipped = sum(1 for p in prepared if p['status'] in ('exists', 'duplicate', 'summary'))
    warnings = [w for p in prepared for w in p['warnings']]
    category_cache = {}
    if ctx.is_category:
        category_cache = {sr.normalize_text(c.name): c for c in ctx.existing_queryset()}

    for p in prepared:
        if p['status'] not in ('new', 'update'):
            continue
        try:
            with transaction.atomic():
                if ctx.is_category:
                    was_created = _upsert_category(ctx, p['values'], category_cache, warnings, p['row'])
                else:
                    _create_simple(ctx, p['values'])
                    was_created = True
            created += was_created
            updated += (not was_created)
        except IntegrityError as exc:
            logger.error('%s import row %d: %s', ctx.key, p['row'], exc)
            errors.append({'row': p['row'], 'field': '', 'message': 'بيانات مكررة أو غير صالحة'})
        except Exception as exc:
            logger.error('%s import row %d: %s', ctx.key, p['row'], exc, exc_info=True)
            errors.append({'row': p['row'], 'field': '', 'message': str(exc)})
    return {'created': created, 'updated': updated, 'skipped': skipped, 'errors': errors,
            'warnings': warnings[:MAX_LISTED_ISSUES]}


def _create_simple(ctx, values):
    model = ctx.model()
    kwargs = {'tenant': ctx.tenant, 'is_active': True}
    if ctx.cfg['audit']:
        kwargs.update(created_by=ctx.user, updated_by=ctx.user)
    if ctx.branch:
        kwargs['branch_id'] = ctx.branch.id          # مستخدم الفرع يستورد إلى فرعه
    for spec in ctx.schema:
        field = spec['field']
        value = values.get(field)
        if spec['dtype'] == 'decimal':
            kwargs[field] = value if value is not None else Decimal('0')
        elif value is not None:
            kwargs[field] = value
    model.objects.create(**kwargs)


def _upsert_category(ctx, values, cache, warnings, row):
    """True إن أُنشئ تصنيف جديد، False إن حُدّث موجود. التصنيف الرئيسي غير الموجود يُنشأ تلقائياً."""
    Category = ctx.model()
    name = values['name']
    key = sr.normalize_text(name)

    parent = None
    parent_name = values.get('parent', '')
    if parent_name and sr.normalize_text(parent_name) != key:
        pkey = sr.normalize_text(parent_name)
        parent = cache.get(pkey)
        if parent is None:
            parent = Category.objects.create(tenant=ctx.tenant, name=parent_name, is_active=True)
            cache[pkey] = parent
            warnings.append({'row': row, 'field': 'parent', 'field_label': 'التصنيف الرئيسي', 'column': '',
                             'header': '', 'value': parent_name[:80],
                             'message': 'التصنيف الرئيسي غير موجود فأُنشئ تلقائياً'})

    category = cache.get(key)
    if category is None:
        category = Category.objects.create(
            tenant=ctx.tenant, name=name, parent=parent, description=values.get('description', ''),
            display_order=values.get('display_order') or 0, is_active=values.get('is_active', True) is not False)
        cache[key] = category
        return True
    if parent is not None:
        category.parent = parent
    if 'is_active' in values and values['is_active'] is not None:
        category.is_active = values['is_active']
    if values.get('display_order') is not None:
        category.display_order = values['display_order']
    if values.get('description'):
        category.description = values['description']
    category.save()
    return False


def run_import(entity_key, tenant, user, data, filename, options=None, meta=None, analyze_only=False):
    """نقطة الدخول المشتركة للمعاينة والاستيراد. Returns (payload, plan)."""
    ctx = Context(entity_key, tenant, user)
    try:
        plan = build_plan(ctx, data, filename, options, meta)
    except sr.ReadError as exc:
        return {'fatal': str(exc)}, None
    prepared = prepare_rows(ctx, plan)
    if analyze_only:
        return analyze(ctx, plan, prepared), plan
    if not plan['data_rows']:
        return {'fatal': 'لم نجد صفوف بيانات تحت صف العناوين في هذه الورقة.'}, plan
    if 'name' not in plan['field_columns']:
        return {'fatal': f'لم نتعرّف على عمود «اسم {ctx.label}» في الملف. حدّده يدوياً من خطوة المعاينة.'}, plan
    return commit(ctx, plan, prepared), plan


def import_entities(entity_key, tenant, uploaded_file, user):
    """استيراد مباشر بلا معاينة (يُبقي واجهات الرفع المباشر القديمة تعمل)."""
    result, _ = run_import(entity_key, tenant, user, uploaded_file.read(), uploaded_file.name)
    if 'fatal' in result:
        return {'created': 0, 'updated': 0, 'skipped': 0,
                'errors': [{'row': 0, 'field': '', 'message': result['fatal']}], 'warnings': []}
    return result
