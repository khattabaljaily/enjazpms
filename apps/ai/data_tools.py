"""
أدوات استعلام المساعد الذكي (function calling).

المساعد لا يعتمد على ملخص ثابت: يستعلم مباشرة عن بيانات النشاط عبر ثلاث أدوات
(list_entities / describe_entity / query_data) فيجيب عن أي سؤال عن أي سجل أو
إجمالي أو ترتيب أو مقارنة.

النطاق إجباري ولا يتحكم فيه المساعد:
  - كل استعلام يُقيَّد دائماً بالمشترك (tenant) الحالي.
  - مستخدم الفرع يُقيَّد بفرعه فقط (BRANCH_PATHS)، أياً كانت الفلاتر المرسلة.
  - مدير النشاط/المستخدم المركزي يرى كل الفروع ويصفّي بينها بحقل الفرع.
  - الجداول غير المسجلة في ENTITIES غير متاحة، والحقول الحساسة (كلمات المرور،
    الرموز، المفاتيح...) محجوبة، والعبور بين الجداول للأمام (FK) فقط.
"""
import datetime
import json
from decimal import Decimal

from django.apps import apps
from django.db import models
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncYear

MAX_ROWS = 200
DEFAULT_ROWS = 50
MAX_OUTPUT_CHARS = 14000
MAX_DEPTH = 3

# key -> (app_label.Model, [مسارات الفرع] أو None للمشترك بين الفروع)
# عدة مسارات = OR (مثل تحويل بين مخزنين: يظهر للفرعين).
ENTITIES = {
    # أطراف
    'customers': ('customers.Customer', ['branch']),
    'suppliers': ('suppliers.Supplier', ['branch']),
    'agents': ('agents.Agent', ['branch']),
    'agent_ledger': ('agents.AgentLedger', ['agent__branch']),
    'agent_invoice_requests': ('agents.AgentInvoiceRequest', ['agent__branch']),
    'agent_invoice_request_lines': ('agents.AgentInvoiceRequestLine', ['request__agent__branch']),
    'employees': ('employees.Employee', ['branch']),
    'employee_advances': ('employees.EmployeeAdvance', ['employee__branch']),
    'employee_salary_payments': ('employees.EmployeeSalaryPayment', ['employee__branch']),
    'employee_incentives': ('employees.EmployeeIncentive', ['employee__branch']),
    'users': ('accounts.User', ['branch']),
    'branches': ('core.Branch', ['pk']),
    # كتالوج مركزي مشترك
    'items': ('items.Item', None),
    'categories': ('items.Category', None),
    'units': ('items.Unit', None),
    'item_units': ('items.ItemUnit', None),
    'bom_recipes': ('items.BOMRecipe', None),
    'bom_lines': ('items.BOMLine', None),
    'expense_categories': ('expenses.ExpenseCategory', None),
    'insurance_companies': ('insurance.InsuranceCompany', None),
    # مخازن ومخزون
    'stocks': ('stocks.Stock', ['branch']),
    'stock_quantities': ('stocks.StockQuantity', ['stock__branch']),
    'item_batches': ('items.ItemBatch', ['stock__branch']),
    'stock_movements': ('sales.StockMovement', ['stock__branch']),
    'stock_transfers': ('stocks.StockTransfer', ['from_stock__branch', 'to_stock__branch']),
    'stock_transfer_lines': ('stocks.StockTransferLine',
                             ['transfer__from_stock__branch', 'transfer__to_stock__branch']),
    'shipments': ('stocks.Shipment', ['branch']),
    'shipment_lines': ('stocks.ShipmentLine', ['shipment__branch']),
    'stocktakes': ('stocks.Stocktake', ['stock__branch']),
    'stocktake_lines': ('stocks.StocktakeLine', ['stocktake__stock__branch']),
    'stock_destructions': ('stocks.StockDestruction', ['stock__branch']),
    'stock_destruction_lines': ('stocks.StockDestructionLine', ['destruction__stock__branch']),
    'manufacturing_orders': ('stocks.ManufacturingOrder', ['stock__branch']),
    # مبيعات
    'sale_invoices': ('sales.SaleInvoice', ['stock__branch']),
    'sale_invoice_lines': ('sales.SaleInvoiceLine', ['invoice__stock__branch']),
    'sale_payments': ('sales.SalePayment', ['invoice__stock__branch']),
    'sale_returns': ('sales.SaleReturn', ['branch']),
    'sale_return_lines': ('sales.SaleReturnLine', ['sale_return__branch']),
    'sale_quotes': ('sales.SaleQuote', ['stock__branch']),
    'sale_quote_lines': ('sales.SaleQuoteLine', ['quote__stock__branch']),
    'customer_ledger': ('sales.CustomerLedger', ['customer__branch']),
    # مشتريات
    'purchase_invoices': ('purchases.PurchaseInvoice', ['stock__branch']),
    'purchase_invoice_lines': ('purchases.PurchaseInvoiceLine', ['invoice__stock__branch']),
    'purchase_payments': ('purchases.PurchasePayment', ['invoice__stock__branch']),
    'purchase_returns': ('purchases.PurchaseReturn', ['branch']),
    'purchase_return_lines': ('purchases.PurchaseReturnLine', ['purchase_return__branch']),
    'purchase_rfqs': ('purchases.PurchaseRFQ', ['stock__branch']),
    'purchase_rfq_lines': ('purchases.PurchaseRFQLine', ['rfq__stock__branch']),
    'supplier_ledger': ('purchases.SupplierLedger', ['supplier__branch']),
    # مالية
    'expenses': ('expenses.Expense', ['branch']),
    'treasuries': ('treasury.Treasury', ['branch']),
    'treasury_movements': ('treasury.TreasuryMovement', ['treasury__branch']),
    'treasury_transfers': ('treasury.TreasuryTransfer',
                           ['from_treasury__branch', 'to_treasury__branch']),
    'bank_accounts': ('bank_accounts.BankAccount', ['branch']),
    'bank_account_movements': ('bank_accounts.BankAccountMovement', ['bank_account__branch']),
    'bank_account_transfers': ('bank_accounts.BankAccountTransfer',
                               ['from_bank_account__branch', 'to_bank_account__branch']),
    'treasury_bank_transfers': ('bank_accounts.TreasuryBankTransfer', ['treasury__branch']),
    # تأمين
    'insurance_members': ('insurance.InsuranceMember', ['customer__branch']),
    'insurance_claims': ('insurance.InsuranceClaim', ['customer__branch']),
    'insurance_claim_lines': ('insurance.InsuranceClaimLine', ['claim__customer__branch']),
    'insurance_claim_settlements': ('insurance.InsuranceClaimSettlement', ['claim__customer__branch']),
    # متجر وإشعارات
    'online_orders': ('store.OnlineOrder', ['branch']),
    'online_order_lines': ('store.OnlineOrderLine', ['order__branch']),
    'notifications': ('notifications.Notification', ['branch']),
}

_SENSITIVE = ('password', 'token', 'secret', 'api_key', 'apikey', 'hash', 'otp', 'session', 'signature')
_BLOCKED_TARGETS = {'core.Tenant', 'core.TenantCapabilities', 'accounts.PermissionGroup',
                    'accounts.UserActivity', 'auth.Permission', 'auth.Group'}

_OPS = {
    'eq': lambda f, v: Q(**{f: v}),
    'ne': lambda f, v: ~Q(**{f: v}),
    'gt': lambda f, v: Q(**{f + '__gt': v}),
    'gte': lambda f, v: Q(**{f + '__gte': v}),
    'lt': lambda f, v: Q(**{f + '__lt': v}),
    'lte': lambda f, v: Q(**{f + '__lte': v}),
    'contains': lambda f, v: Q(**{f + '__icontains': v}),
    'startswith': lambda f, v: Q(**{f + '__istartswith': v}),
    'in': lambda f, v: Q(**{f + '__in': v if isinstance(v, list) else [v]}),
    'isnull': lambda f, v: Q(**{f + '__isnull': bool(v)}),
}
_AGGS = {'sum': Sum, 'avg': Avg, 'min': Min, 'max': Max}
_TRUNC = {'day': TruncDay, 'month': TruncMonth, 'year': TruncYear}


class ToolError(Exception):
    pass


def _model(label):
    try:
        return apps.get_model(label)
    except LookupError:
        return None


def available_entities():
    """{key: (model, branch_paths)} للجداول الموجودة في هذه النسخة فقط."""
    out = {}
    for key, (label, paths) in ENTITIES.items():
        model = _model(label)
        if model is not None:
            out[key] = (model, paths)
    return out


def _is_sensitive(name):
    low = name.lower()
    return any(s in low for s in _SENSITIVE)


def _is_file(field):
    return isinstance(field, (models.FileField, models.ImageField, models.BinaryField))


def _forward_fields(model):
    return [f for f in model._meta.get_fields()
            if getattr(f, 'concrete', False) and not _is_file(f) and not _is_sensitive(f.name)]


def _resolve(model, path, allow_relation_leaf=True):
    """يتحقق من مسار حقل (قد يعبر FK للأمام) ويرجع الحقل النهائي."""
    parts = path.split('__')
    if len(parts) > MAX_DEPTH + 1:
        raise ToolError(f'المسار "{path}" أطول من المسموح.')
    current = model
    field = None
    for i, part in enumerate(parts):
        if part == 'pk':
            part = current._meta.pk.name
        if _is_sensitive(part):
            raise ToolError(f'الحقل "{part}" غير متاح.')
        try:
            field = current._meta.get_field(part)
        except Exception:
            names = ', '.join(f.name for f in _forward_fields(current))
            raise ToolError(f'الحقل "{part}" غير موجود في {current._meta.label}. الحقول المتاحة: {names}')
        if not getattr(field, 'concrete', False) or _is_file(field):
            raise ToolError(f'الحقل "{part}" غير متاح.')
        last = i == len(parts) - 1
        if field.is_relation:
            if not field.many_to_one and not field.one_to_one:
                raise ToolError(f'الحقل "{part}" علاقة غير مدعومة.')
            target = field.related_model
            if target._meta.label in _BLOCKED_TARGETS:
                raise ToolError(f'الحقل "{part}" غير متاح.')
            if last:
                if not allow_relation_leaf:
                    raise ToolError(f'"{path}" علاقة؛ حدّد حقلاً منها مثل {path}__name.')
                return field
            current = target
        elif not last:
            raise ToolError(f'"{part}" ليس علاقة فلا يمكن العبور منه.')
    return field


def _scoped_queryset(model, paths, tenant, user):
    qs = model._base_manager.all().filter(tenant=tenant) if _has_field(model, 'tenant') \
        else model._base_manager.none()
    branch = getattr(user, 'branch', None) if user is not None else None
    if branch is not None and paths:
        cond = Q()
        for p in paths:
            cond |= Q(**{p: branch.pk}) if p != 'pk' else Q(pk=branch.pk)
        qs = qs.filter(cond)
    elif branch is not None and paths is None:
        pass  # كتالوج مركزي مشترك بين الفروع
    return qs


def _has_field(model, name):
    try:
        model._meta.get_field(name)
        return True
    except Exception:
        return False


def _json_value(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, dict)):
        return v
    return str(v)


def _filters_q(model, filters):
    q = Q()
    for flt in filters or []:
        if not isinstance(flt, dict) or 'field' not in flt:
            raise ToolError('كل فلتر يجب أن يكون {"field":..., "op":..., "value":...}.')
        op = flt.get('op', 'eq')
        if op not in _OPS:
            raise ToolError(f'عملية الفلتر "{op}" غير مدعومة. المتاح: {", ".join(_OPS)}')
        _resolve(model, flt['field'])
        q &= _OPS[op](flt['field'], flt.get('value'))
    return q


def _group_expr(model, spec):
    """'field' أو 'date_field:month' ← (alias, expression أو اسم الحقل)."""
    if ':' in spec:
        field, unit = spec.split(':', 1)
        if unit not in _TRUNC:
            raise ToolError(f'وحدة التجميع "{unit}" غير مدعومة (day/month/year).')
        _resolve(model, field, allow_relation_leaf=False)
        alias = f'{field.replace("__", "_")}_{unit}'
        return alias, _TRUNC[unit](field)
    _resolve(model, spec)
    return spec, None


def _truncate(payload):
    text = json.dumps(payload, ensure_ascii=False, default=_json_value)
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    rows = payload.get('rows')
    if isinstance(rows, list):
        while rows and len(json.dumps(payload, ensure_ascii=False, default=_json_value)) > MAX_OUTPUT_CHARS:
            rows.pop()
        payload['truncated'] = True
        payload['note'] = 'قُلّصت النتيجة لحجمها؛ استخدم limit/offset أو حقولاً أقل أو فلاتر أدق.'
        return json.dumps(payload, ensure_ascii=False, default=_json_value)
    return text[:MAX_OUTPUT_CHARS]


def tool_list_entities(tenant, user):
    ents = available_entities()
    return {'entities': [
        {'entity': key, 'label': str(model._meta.verbose_name_plural),
         'shared_between_branches': paths is None}
        for key, (model, paths) in ents.items()
    ]}


def tool_describe_entity(tenant, user, entity=None, **_):
    ents = available_entities()
    if entity not in ents:
        raise ToolError(f'الكيان "{entity}" غير معروف. استدعِ list_entities.')
    model = ents[entity][0]
    fields = []
    for f in _forward_fields(model):
        if f.name in ('tenant', 'updated_by'):
            continue
        item = {'name': f.name, 'type': f.get_internal_type(), 'label': str(f.verbose_name)}
        if f.is_relation:
            target = f.related_model
            item['references'] = next((k for k, (m, _p) in ents.items() if m is target), target._meta.label)
            item['hint'] = f'للعبور استخدم مثل {f.name}__name'
        if getattr(f, 'choices', None):
            item['choices'] = {str(k): str(v) for k, v in f.choices}
        fields.append(item)
    return {'entity': entity, 'label': str(model._meta.verbose_name_plural), 'fields': fields}


def tool_query_data(tenant, user, entity=None, filters=None, fields=None, order_by=None,
                    limit=None, offset=0, aggregate=None, group_by=None, **_):
    ents = available_entities()
    if entity not in ents:
        raise ToolError(f'الكيان "{entity}" غير معروف. استدعِ list_entities.')
    model, paths = ents[entity]
    qs = _scoped_queryset(model, paths, tenant, user).filter(_filters_q(model, filters))

    if aggregate or group_by:
        aggs = {}
        for spec in aggregate or [{'func': 'count', 'field': '*', 'as': 'count'}]:
            func = spec.get('func', 'count')
            field = spec.get('field', '*')
            alias = spec.get('as') or f'{func}_{field.replace("__", "_").replace("*", "all")}'
            if func == 'count':
                aggs[alias] = Count('pk') if field == '*' else Count(field)
                if field != '*':
                    _resolve(model, field)
            elif func in _AGGS:
                _resolve(model, field, allow_relation_leaf=False)
                aggs[alias] = _AGGS[func](field)
            else:
                raise ToolError(f'الدالة "{func}" غير مدعومة. المتاح: count, {", ".join(_AGGS)}')
        n = min(int(limit or 100), MAX_ROWS)
        if group_by:
            exprs = [_group_expr(model, g) for g in group_by]
            ann = {alias: expr for alias, expr in exprs if expr is not None}
            if ann:
                qs = qs.annotate(**ann)
            names = [alias for alias, _e in exprs]
            qs = qs.values(*names).annotate(**aggs)
            first = next(iter(aggs))
            order = order_by or [f'-{first}']
            qs = qs.order_by(*[o if o.lstrip('-') in aggs or o.lstrip('-') in names else f'-{first}' for o in order])
            rows = list(qs[:n])
            return {'entity': entity, 'groups': len(rows), 'rows': rows}
        return {'entity': entity, 'result': qs.aggregate(**aggs)}

    for o in order_by or ['-pk']:
        _resolve(model, o.lstrip('-'), allow_relation_leaf=True)
    if fields:
        for f in fields:
            _resolve(model, f)
        cols = list(fields)
    else:
        cols = [f.name for f in _forward_fields(model)
                if f.name not in ('tenant', 'updated_by', 'created_by', 'updated_at')]
    n = max(1, min(int(limit or DEFAULT_ROWS), MAX_ROWS))
    total = qs.count()
    rows = list(qs.order_by(*(order_by or ['-pk'])).values(*cols)[int(offset or 0): int(offset or 0) + n])
    return {'entity': entity, 'total_count': total, 'returned': len(rows), 'offset': int(offset or 0),
            'rows': rows}


TOOL_SCHEMAS = [
    {'type': 'function', 'function': {
        'name': 'list_entities',
        'description': 'قائمة كل أنواع البيانات التي يمكن الاستعلام عنها (عملاء، موردون، فواتير، أرصدة، مخزون، خزائن، مصروفات، موظفون...).',
        'parameters': {'type': 'object', 'properties': {}}}},
    {'type': 'function', 'function': {
        'name': 'describe_entity',
        'description': 'حقول كيان معيّن وأنواعها وعلاقاته وقيم حالاته، لتعرف أسماء الحقول قبل الاستعلام.',
        'parameters': {'type': 'object', 'properties': {'entity': {'type': 'string'}},
                       'required': ['entity']}}},
    {'type': 'function', 'function': {
        'name': 'query_data',
        'description': (
            'استعلام عن بيانات النشاط. صفّي بـ filters [{field, op, value}] (op: eq, ne, gt, gte, lt, lte, '
            'contains, startswith, in, isnull)، واختر fields (يمكن العبور بين العلاقات مثل customer__name '
            'أو stock__branch__name)، وorder_by (مثل "-grand_total")، وlimit/offset. لإجماليات استعمل '
            'aggregate [{func: count|sum|avg|min|max, field, as}] مع group_by اختياري (يدعم "invoice_date:month" '
            'و:day و:year). النتائج مقيّدة تلقائياً بنطاق المستخدم.'),
        'parameters': {'type': 'object', 'properties': {
            'entity': {'type': 'string'},
            'filters': {'type': 'array', 'items': {'type': 'object'}},
            'fields': {'type': 'array', 'items': {'type': 'string'}},
            'order_by': {'type': 'array', 'items': {'type': 'string'}},
            'limit': {'type': 'integer'},
            'offset': {'type': 'integer'},
            'aggregate': {'type': 'array', 'items': {'type': 'object'}},
            'group_by': {'type': 'array', 'items': {'type': 'string'}},
        }, 'required': ['entity']}}},
]

_HANDLERS = {
    'list_entities': tool_list_entities,
    'describe_entity': tool_describe_entity,
    'query_data': tool_query_data,
}


def run_tool(name, arguments, tenant, user):
    """ينفّذ أداة ويرجع نصاً JSON (الأخطاء تُرجَع كنص ليصحّحها المساعد)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({'error': f'أداة غير معروفة: {name}'}, ensure_ascii=False)
    try:
        args = json.loads(arguments) if isinstance(arguments, str) and arguments.strip() else (arguments or {})
        if not isinstance(args, dict):
            raise ToolError('المعاملات يجب أن تكون كائناً JSON.')
        return _truncate(handler(tenant, user, **args))
    except ToolError as exc:
        return json.dumps({'error': str(exc)}, ensure_ascii=False)
    except (ValueError, TypeError) as exc:
        return json.dumps({'error': f'معاملات غير صالحة: {exc}'}, ensure_ascii=False)
    except Exception as exc:  # خطأ ORM (قيمة فلتر غير صالحة...) يُعاد للمساعد ليعدّل استعلامه
        return json.dumps({'error': f'تعذّر تنفيذ الاستعلام: {type(exc).__name__}: {exc}'}, ensure_ascii=False)
