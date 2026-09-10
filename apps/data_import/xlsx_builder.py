"""
Reusable styled .xlsx template builder for the "استيراد البيانات" section.

Generic on purpose: any future import type (customers, suppliers...) can call
`build_import_template()` with its own field schema instead of writing xlsx
styling/data-validation code again.
"""
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill(start_color='2F5773', end_color='2F5773', fill_type='solid')
HEADER_FILL_REQUIRED = PatternFill(start_color='1B3A4F', end_color='1B3A4F', fill_type='solid')
HEADER_FONT = Font(bold=True, color='FFFFFF', size=11)
HEADER_ALIGNMENT = Alignment(horizontal='center', vertical='center', wrap_text=True)
THIN_BORDER = Border(*([Side(style='thin', color='B0B0B0')] * 4))

EXAMPLE_FILL = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
EXAMPLE_FONT = Font(italic=True, color='808080')

MIN_COL_WIDTH = 10
MAX_COL_WIDTH = 45
DATA_ROWS_FOR_VALIDATION = 1000

BOOL_LIST = '"نعم,لا"'


def _column_width(spec) -> float:
    header_len = len(spec['header_ar']) + 2
    typical = spec.get('typical_width', 16)
    return max(MIN_COL_WIDTH, min(MAX_COL_WIDTH, max(header_len, typical)))


def _write_instructions_sheet(wb, sheet_title, schema, instructions):
    ws = wb.active
    ws.title = 'تعليمات'
    ws.sheet_view.rightToLeft = True
    ws.column_dimensions['A'].width = 90

    lines = [
        f'تعليمات تعبئة ملف {sheet_title}',
        '',
        '• الحقول المميزة بعلامة (*) في اسم العمود مطلوبة، والباقي اختياري.',
        '• أي خلية تُترك فارغة تأخذ قيمة افتراضية مناسبة تلقائياً.',
        '• حالة "نشط" ليست عموداً في هذا الملف — كل منتج يُستورد يكون نشطاً تلقائياً.',
        '• الأعمدة التي تحتوي على قائمة منسدلة (▼) يجب اختيار قيمة منها مباشرة وليس كتابتها يدوياً.',
        '• الصف الثاني في شيت البيانات هو صف مثال — احذفه قبل إدخال بياناتك الحقيقية.',
    ]
    if instructions:
        lines += ['', 'ملاحظات إضافية:'] + [f'• {line}' for line in instructions]

    required_fields = [s['header_ar'] for s in schema if s.get('required')]
    if required_fields:
        lines += ['', 'الحقول المطلوبة في هذا الملف:'] + [f'• {h}' for h in required_fields]

    for i, text in enumerate(lines, start=1):
        cell = ws.cell(row=i, column=1, value=text)
        cell.alignment = Alignment(horizontal='right', wrap_text=True)
        if i == 1:
            cell.font = Font(bold=True, size=13)


def _add_hidden_lists_sheet(wb, tenant_lists: dict):
    """One column per tenant-data list (category/unit/stock names), hidden from the user."""
    ws = wb.create_sheet('_Lists')
    ws.sheet_state = 'hidden'
    column_ranges = {}
    for col_idx, (key, values) in enumerate(tenant_lists.items(), start=1):
        col_letter = get_column_letter(col_idx)
        ws.cell(row=1, column=col_idx, value=key)
        for row_idx, value in enumerate(values, start=2):
            ws.cell(row=row_idx, column=col_idx, value=value)
        if values:
            column_ranges[key] = f"'_Lists'!${col_letter}$2:${col_letter}${len(values) + 1}"
    return column_ranges


def build_import_template(schema: list, tenant_lists: dict, sheet_title: str, instructions: list = None):
    """
    schema: resolved field-spec list (see apps/data_import/schemas.py)
    tenant_lists: {tenant_source_key: [names...]} e.g. {'category': [...], 'unit': [...], 'stock': [...]}
    sheet_title: Arabic sheet/tab name, e.g. "المنتجات"
    instructions: optional extra bullet lines for the instructions sheet
    """
    wb = Workbook()
    _write_instructions_sheet(wb, sheet_title, schema, instructions)

    ws = wb.create_sheet(sheet_title)
    ws.sheet_view.rightToLeft = True
    ws.freeze_panes = 'A2'
    ws.row_dimensions[1].height = 32

    for col_idx, spec in enumerate(schema, start=1):
        col_letter = get_column_letter(col_idx)
        cell = ws.cell(row=1, column=col_idx, value=spec['header_ar'])
        cell.font = HEADER_FONT
        cell.alignment = HEADER_ALIGNMENT
        cell.fill = HEADER_FILL_REQUIRED if spec.get('required') else HEADER_FILL
        cell.border = THIN_BORDER
        ws.column_dimensions[col_letter].width = _column_width(spec)

    tenant_ranges = _add_hidden_lists_sheet(wb, tenant_lists) if tenant_lists else {}

    last_row = DATA_ROWS_FOR_VALIDATION + 1
    for col_idx, spec in enumerate(schema, start=1):
        col_letter = get_column_letter(col_idx)
        data_range = f'{col_letter}2:{col_letter}{last_row}'
        dv = None

        if spec['dtype'] == 'bool':
            dv = DataValidation(type='list', formula1=BOOL_LIST, allow_blank=True, showErrorMessage=True)
            dv.error = 'اختر "نعم" أو "لا" من القائمة'
        elif spec['dtype'] == 'choice_fixed':
            labels = ','.join(label for _, label in spec['choices'])
            dv = DataValidation(type='list', formula1=f'"{labels}"', allow_blank=True, showErrorMessage=True)
            dv.error = 'اختر قيمة من القائمة'
        elif spec['dtype'] == 'choice_tenant':
            source = spec.get('tenant_source')
            cell_range = tenant_ranges.get(source)
            if cell_range:
                dv = DataValidation(type='list', formula1=cell_range, allow_blank=True, showErrorMessage=True)
                dv.error = 'اختر قيمة من القائمة'

        if dv is not None:
            dv.errorTitle = 'قيمة غير صحيحة'
            ws.add_data_validation(dv)
            dv.add(data_range)

    _write_example_row(ws, schema, tenant_lists)
    wb.active = 1
    return wb


def _write_example_row(ws, schema, tenant_lists):
    """Row 2: one illustrative, clearly-marked example row (mirrors the old CSV template's convention)."""
    samples = {
        'name': 'منتج تجريبي — احذف هذا الصف',
        'category': (tenant_lists.get('category') or [''])[0],
        'base_unit_name': 'حبة',
        'cost_price': 100,
        'selling_price': 150,
        'min_selling_price': 0,
        'opening_quantity': 0,
    }
    for col_idx, spec in enumerate(schema, start=1):
        field = spec['field']
        value = samples.get(field, '')
        if field == 'item_type' and spec['dtype'] == 'choice_fixed':
            value = spec['choices'][0][1] if spec['choices'] else ''
        cell = ws.cell(row=2, column=col_idx, value=value)
        cell.font = EXAMPLE_FONT
        cell.fill = EXAMPLE_FILL
