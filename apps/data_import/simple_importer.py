"""
Generic row-processing engine for "simple" entities — customers and suppliers
today. Unlike products, these have no tenant master-data dropdowns, no
capability gating, and no side effects beyond the model row itself (opening
balance is a plain field on Customer/Supplier, not a separate ledger entry —
see apps/customers/models.py / apps/suppliers/models.py), so one generic
function replaces what would otherwise be two near-identical importers.
"""
import logging
from decimal import Decimal, InvalidOperation
from django.db import transaction, IntegrityError

from apps.ai.services import smart_map_headers
from apps.core.io_utils import parse_uploaded_file, smart_get, safe_decimal, bool_from_str, clean_phone, clean_email

logger = logging.getLogger('data_import')


def import_simple_entities(tenant, uploaded_file, user, model, schema, entity_label):
    """Returns {'created': int, 'errors': [{'row', 'field', 'message'}, ...]}"""
    rows, err = parse_uploaded_file(uploaded_file)
    if err:
        return {'created': 0, 'errors': [{'row': 0, 'field': '', 'message': err}]}
    if not rows:
        return {'created': 0, 'errors': [{'row': 0, 'field': '', 'message': 'الملف فارغ أو لا يحتوي على بيانات'}]}

    ai_schema = [
        {'field': s['field'], 'description': s['description'], 'required': s.get('required', False)}
        for s in schema
    ]
    actual_headers = list(rows[0].keys())
    try:
        mapping = smart_map_headers(actual_headers, ai_schema)
    except Exception:
        mapping = {}

    name_field = schema[0]['field']  # 'name' is always first and required
    created = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        try:
            with transaction.atomic():
                name = smart_get(row, name_field, mapping, schema[0]['header_ar'], 'الاسم', name_field)
                if not name:
                    errors.append({'row': i, 'field': name_field, 'message': f'اسم {entity_label} مطلوب'})
                    continue

                kwargs = {'tenant': tenant, 'created_by': user, 'updated_by': user, 'is_active': True}
                for spec in schema:
                    field = spec['field']
                    if field == name_field:
                        continue
                    raw_value = smart_get(row, field, mapping, spec['header_ar'], field)
                    if spec['dtype'] == 'decimal':
                        kwargs[field] = safe_decimal(raw_value, default=Decimal('0'))
                    elif spec['dtype'] == 'bool':
                        kwargs[field] = bool_from_str(raw_value) if raw_value else False
                    elif spec['dtype'] == 'choice_fixed':
                        label_to_value = {label: value for value, label in spec['choices']}
                        kwargs[field] = label_to_value.get(raw_value, raw_value if raw_value in dict(spec['choices']) else '')
                    elif field == 'phone':
                        kwargs[field] = clean_phone(raw_value) or ''
                    elif field == 'email':
                        kwargs[field] = clean_email(raw_value) or ''
                    else:
                        kwargs[field] = raw_value

                model.objects.create(**{name_field: name}, **kwargs)
                created += 1
        except IntegrityError as exc:
            logger.error('%s import row %d: %s', entity_label, i, exc)
            errors.append({'row': i, 'field': '', 'message': 'بيانات مكررة أو غير صالحة'})
        except (InvalidOperation, ValueError) as exc:
            logger.error('%s import row %d: %s', entity_label, i, exc)
            errors.append({'row': i, 'field': '', 'message': f'قيمة غير صالحة: {exc}'})
        except Exception as exc:
            logger.error('%s import row %d: %s', entity_label, i, exc, exc_info=True)
            errors.append({'row': i, 'field': '', 'message': str(exc)})

    return {'created': created, 'errors': errors}
