"""
Row-processing engine for the product import commit step.

Mirrors, rather than reinvents, four existing ORM/logic patterns:
- opening balance:  apps/stocks/views.py `opening_balance_save_api`
- batch creation:   apps/purchases/services.py `confirm_purchase_invoice`
- default stock:    apps/stocks/views.py (`filter(is_default=True).first() or .first()`)
- per-product units: apps/items/views.py `_save_item_units` (the same helper the
  manual "add product" form uses — a product's units are NOT a shared/tenant-level
  list, they're defined per-product as an optional smaller+larger pair)
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction, IntegrityError
from django.utils import timezone

from apps.items.models import Category, Item, ItemBatch
from apps.items.views import _apply_hc_prices, _save_item_units
from apps.stocks.models import Stock, StockQuantity
from apps.sales.models import StockMovement
from apps.ai.services import smart_map_headers, match_category_name
from apps.core.io_utils import parse_uploaded_file, smart_get, safe_decimal, safe_date, bool_from_str

from .schemas import get_product_schema
from .gating import products_import_blocked_reason

logger = logging.getLogger('data_import')


def _name_lookup(queryset):
    return {obj.name.strip().lower(): obj for obj in queryset}


def import_products(tenant, uploaded_file, user):
    """Returns {'created': int, 'errors': [{'row', 'field', 'message'}, ...]}"""
    if products_import_blocked_reason(tenant):
        return {'created': 0, 'errors': [{'row': 0, 'field': '', 'message': 'أضف تصنيفاً واحداً على الأقل أولاً'}]}

    rows, err = parse_uploaded_file(uploaded_file)
    if err:
        return {'created': 0, 'errors': [{'row': 0, 'field': '', 'message': err}]}
    if not rows:
        return {'created': 0, 'errors': [{'row': 0, 'field': '', 'message': 'الملف فارغ أو لا يحتوي على بيانات'}]}

    schema = get_product_schema(tenant)
    schema_by_field = {s['field']: s for s in schema}
    has_stock_column = 'stock' in schema_by_field

    ai_schema = [
        {'field': s['field'], 'description': s['description'], 'required': s.get('required', False)}
        for s in schema
    ]
    actual_headers = list(rows[0].keys())
    try:
        mapping = smart_map_headers(actual_headers, ai_schema)
    except Exception:
        mapping = {}

    categories = _name_lookup(Category.objects.for_tenant(tenant).filter(is_active=True))
    stocks = _name_lookup(Stock.objects.for_tenant(tenant).filter(is_active=True))

    default_stock = None
    if not has_stock_column:
        active_stocks = Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('-is_default', 'name')
        default_stock = active_stocks.filter(is_default=True).first() or active_stocks.first()

    category_ai_cache: dict = {}

    def resolve_category(raw: str):
        if not raw:
            return None, None
        key = raw.strip().lower()
        if key in categories:
            return categories[key], None
        if raw in category_ai_cache:
            cached = category_ai_cache[raw]
            return (categories.get(cached.strip().lower()), None) if cached else (None, f'التصنيف "{raw}" غير موجود')
        matched = match_category_name(raw, [c.name for c in categories.values()])
        category_ai_cache[raw] = matched
        if matched and matched.strip().lower() in categories:
            return categories[matched.strip().lower()], None
        return None, f'التصنيف "{raw}" غير موجود — اختره من القائمة المنسدلة'

    def resolve_stock(raw: str):
        if not raw:
            return default_stock, None
        key = raw.strip().lower()
        if key in stocks:
            return stocks[key], None
        return None, f'المخزن "{raw}" غير موجود'

    created = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            with transaction.atomic():
                name = smart_get(row, 'name', mapping, schema_by_field['name']['header_ar'], 'اسم المنتج', 'الاسم', 'name')
                if not name:
                    errors.append({'row': i, 'field': 'name', 'message': 'اسم المنتج مطلوب'})
                    continue

                category_raw = smart_get(row, 'category', mapping, schema_by_field.get('category', {}).get('header_ar', ''), 'التصنيف', 'category')
                category, cat_err = resolve_category(category_raw)
                if cat_err:
                    errors.append({'row': i, 'field': 'category', 'message': cat_err})
                    continue

                base_unit_name = smart_get(row, 'base_unit_name', mapping, schema_by_field.get('base_unit_name', {}).get('header_ar', ''), 'اسم الوحدة الأساسية', 'base_unit_name')
                large_unit_name = smart_get(row, 'large_unit_name', mapping, schema_by_field.get('large_unit_name', {}).get('header_ar', ''), 'اسم وحدة أكبر', 'large_unit_name')
                large_unit_count_raw = smart_get(row, 'large_unit_count', mapping, schema_by_field.get('large_unit_count', {}).get('header_ar', ''), 'large_unit_count')

                units_data = []
                if large_unit_name and not base_unit_name:
                    errors.append({'row': i, 'field': 'base_unit_name', 'message': 'حدّد اسم الوحدة الأساسية أولاً قبل تحديد وحدة أكبر'})
                    continue
                if base_unit_name:
                    units_data.append({'name': base_unit_name, 'factor': 1})
                if large_unit_name:
                    large_unit_count = safe_decimal(large_unit_count_raw, default=Decimal('0'))
                    if large_unit_count <= 0:
                        errors.append({'row': i, 'field': 'large_unit_count', 'message': 'حدّد كم وحدة أساسية داخل الوحدة الأكبر'})
                        continue
                    units_data.append({'name': large_unit_name, 'factor': float(large_unit_count)})

                target_stock = default_stock
                if has_stock_column:
                    stock_raw = smart_get(row, 'stock', mapping, schema_by_field['stock']['header_ar'], 'المخزن', 'stock')
                    target_stock, stock_err = resolve_stock(stock_raw)
                    if stock_err:
                        errors.append({'row': i, 'field': 'stock', 'message': stock_err})
                        continue

                kwargs = {
                    'tenant': tenant, 'created_by': user, 'updated_by': user,
                    'name': name, 'category': category,
                    'is_active': True,
                }

                for spec in schema:
                    field = spec['field']
                    if field in ('name', 'category', 'base_unit_name', 'large_unit_name', 'large_unit_count',
                                 'opening_quantity', 'stock', 'batch_number', 'expiry_date'):
                        continue
                    raw_value = smart_get(row, field, mapping, spec['header_ar'], field)
                    if spec['dtype'] == 'decimal':
                        kwargs[field] = safe_decimal(raw_value, default=Decimal('0'))
                    elif spec['dtype'] == 'bool':
                        kwargs[field] = bool_from_str(raw_value) if raw_value else False
                    elif spec['dtype'] == 'choice_fixed':
                        label_to_value = {label: value for value, label in spec['choices']}
                        kwargs[field] = label_to_value.get(raw_value, spec['choices'][0][0])
                    else:
                        kwargs[field] = raw_value

                item = Item(**kwargs)
                if tenant.hard_currency_mode:
                    _apply_hc_prices(item, tenant)
                item.save()

                if units_data:
                    _save_item_units(item, json.dumps(units_data), tenant)

                opening_qty = safe_decimal(
                    smart_get(row, 'opening_quantity', mapping, schema_by_field.get('opening_quantity', {}).get('header_ar', ''), 'الكمية الافتتاحية', 'opening_quantity'),
                    default=Decimal('0'),
                )

                if opening_qty and target_stock and item.item_type != 'service':
                    sq = StockQuantity.objects.select_for_update().get(
                        tenant=tenant, stock=target_stock, item=item,
                    )
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

                    batch_number = smart_get(row, 'batch_number', mapping, schema_by_field.get('batch_number', {}).get('header_ar', ''), 'رقم الدفعة', 'batch_number')
                    expiry_raw = smart_get(row, 'expiry_date', mapping, schema_by_field.get('expiry_date', {}).get('header_ar', ''), 'تاريخ الانتهاء', 'expiry_date')
                    expiry_date = safe_date(expiry_raw) if expiry_raw else None
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
            errors.append({'row': i, 'field': 'sku', 'message': 'رمز المنتج (SKU) أو بيانات أخرى مكررة'})
        except (InvalidOperation, ValueError) as exc:
            logger.error('product import row %d: %s', i, exc)
            errors.append({'row': i, 'field': '', 'message': f'قيمة غير صالحة: {exc}'})
        except Exception as exc:
            logger.error('product import row %d: %s', i, exc, exc_info=True)
            errors.append({'row': i, 'field': '', 'message': str(exc)})

    return {'created': created, 'errors': errors}
