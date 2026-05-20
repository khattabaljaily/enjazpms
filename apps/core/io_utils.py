"""
Shared import/export utilities used across all apps.
"""
import csv
import io
from decimal import Decimal, InvalidOperation
from datetime import datetime

from django.http import HttpResponse


# ── Response helpers ────────────────────────────────────────────────────────

def csv_response(filename: str) -> HttpResponse:
    """Return an HttpResponse ready for CSV download with UTF-8 BOM (Excel-safe)."""
    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('﻿')
    return response


def csv_writer(response) -> csv.writer:
    return csv.writer(response)


# ── Parsing helpers ─────────────────────────────────────────────────────────

def parse_uploaded_file(file) -> tuple[list[dict], str | None]:
    """
    Parse an uploaded CSV or Excel file.
    Returns (rows, error_message).
    rows is a list of dicts keyed by the header row values.
    """
    name = file.name.lower()
    if not name.endswith(('.csv', '.xlsx', '.xls')):
        return [], 'نوع الملف غير مدعوم. الرجاء رفع ملف Excel أو CSV'

    try:
        if name.endswith('.csv'):
            decoded = file.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(decoded))
            return list(reader), None
        else:
            import openpyxl
            wb = openpyxl.load_workbook(file, data_only=True)
            ws = wb.active
            headers = [str(c.value).strip() if c.value is not None else '' for c in ws[1]]
            rows = []
            for row in ws.iter_rows(min_row=2, values_only=True):
                if all(v is None for v in row):
                    continue
                rows.append(dict(zip(headers, row)))
            return rows, None
    except Exception as exc:
        return [], f'تعذّر قراءة الملف: {exc}'


def get(row: dict, *keys, default='') -> str:
    """Return the first non-empty value matching any of the given keys (Arabic or English)."""
    for key in keys:
        val = row.get(key, '')
        if val is not None:
            val = str(val).strip()
            if val and val.lower() not in ('none', 'nan'):
                return val
    return default


def safe_decimal(val, default=Decimal('0')) -> Decimal:
    try:
        return Decimal(str(val).replace(',', '').strip())
    except (InvalidOperation, ValueError, TypeError):
        return default


def safe_date(val, default=None):
    if val is None:
        return default
    if isinstance(val, datetime):
        return val.date()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            pass
    return default


def bool_from_str(val) -> bool:
    return str(val).strip() in ('نعم', 'yes', '1', 'true', 'True')
