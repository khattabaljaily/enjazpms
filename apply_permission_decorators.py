#!/usr/bin/env python3
"""
Script to apply permission decorators to selected view modules.
This helps ensure consistent permission checks across the app.

Usage:
  python3 apply_permission_decorators.py
"""
import re
from pathlib import Path

# Mapping of module names to view function names and their required permission keys.
PERMISSION_MAPPING = {
    'apps.suppliers.views': {
        'supplier_list': 'view_suppliers',
        'supplier_table_api': 'view_suppliers',
        'supplier_create_api': 'add_suppliers',
        'supplier_create': 'add_suppliers',
        'supplier_detail_api': 'view_suppliers',
        'supplier_transactions_api': 'view_suppliers',
        'supplier_update_api': 'change_suppliers',
        'supplier_delete_api': 'delete_suppliers',
        'supplier_import_api': 'import_suppliers',
        'supplier_export_api': 'export_suppliers',
        'supplier_payments': 'view_supplier_payments',
        'supplier_payments_table_api': 'view_supplier_payments',
        'supplier_payment_create_api': 'add_supplier_payments',
        'supplier_payment_detail_api': 'view_supplier_payments',
        'supplier_payment_cancel_api': 'cancel_supplier_payments',
        'download_template': 'import_suppliers',
    },
    'apps.items.views': {
        'item_list': 'view_items',
        'item_table_api': 'view_items',
        'item_create_api': 'add_items',
        'item_detail_api': 'view_items',
        'item_update_api': 'change_items',
        'item_delete_api': 'delete_items',
        'item_search_api': 'view_items',
        'item_transactions_api': 'view_items',
        'category_list': 'view_categories',
        'category_table_api': 'view_categories',
        'category_create_api': 'add_categories',
        'category_detail_api': 'view_categories',
        'category_update_api': 'change_categories',
        'category_delete_api': 'delete_categories',
        'unit_list': 'view_units',
        'unit_table_api': 'view_units',
        'unit_create_api': 'add_units',
        'unit_detail_api': 'view_units',
        'unit_update_api': 'change_units',
        'unit_delete_api': 'delete_units',
    },
    'apps.stocks.views': {
        'stock_list': 'view_stocks',
        'stock_table_api': 'view_stocks',
        'stock_create_api': 'add_stocks',
        'stock_detail_api': 'view_stocks',
        'stock_update_api': 'change_stocks',
        'stock_delete_api': 'delete_stocks',
        'stock_set_default_api': 'change_stocks',
        'opening_balance_list': 'view_stocks',
        'opening_balance_table_api': 'view_stocks',
        'opening_balance_save_api': 'change_stocks',
        'stock_quantities_list': 'view_stock_quantities',
        'stock_quantities_table_api': 'view_stock_quantities',
    },
    'apps.sales.views': {
        'invoice_list': 'view_sales',
        'invoice_table_api': 'view_sales',
        'invoice_create': 'add_sales',
        'invoice_detail': 'view_sales',
        'invoice_edit': 'change_sales',
        'invoice_delete_draft_ajax': 'delete_sales',
        'invoice_confirm_ajax': 'change_sales',
        'invoice_cancel_ajax': 'delete_sales',
        'record_payment_ajax': 'change_sales',
        'return_list': 'view_sales_returns',
        'return_table_api': 'view_sales_returns',
        'return_lines_api': 'view_sales_returns',
        'return_create': 'add_sales_returns',
        'return_detail': 'view_sales_returns',
        'return_confirm_ajax': 'add_sales_returns',
        'return_cancel_ajax': 'add_sales_returns',
        'item_info_api': 'view_items',
        'customer_info_api': 'view_customers',
        'stock_items_api': 'view_items',
        'quote_list': 'view_quotes',
        'quote_table_api': 'view_quotes',
        'quote_create': 'add_quotes',
        'quote_detail': 'view_quotes',
        'quote_edit': 'change_quotes',
        'quote_send_ajax': 'change_quotes',
        'quote_accept_ajax': 'change_quotes',
        'quote_reject_ajax': 'change_quotes',
        'quote_cancel_ajax': 'change_quotes',
        'quote_delete_draft_ajax': 'delete_quotes',
        'quote_convert_ajax': 'change_quotes',
    },
    'apps.purchases.views': {
        'order_list': 'view_purchases',
        'order_table_api': 'view_purchases',
        'order_create': 'add_purchases',
        'order_detail': 'view_purchases',
        'order_edit': 'change_purchases',
        'order_print': 'view_purchases',
        'order_confirm_ajax': 'change_purchases',
        'order_cancel_ajax': 'delete_purchases',
        'return_list': 'view_purchase_returns',
        'return_table_api': 'view_purchase_returns',
        'return_lines_api': 'view_purchase_returns',
        'return_create': 'add_purchase_returns',
        'return_detail': 'view_purchase_returns',
        'return_confirm_ajax': 'add_purchase_returns',
        'return_cancel_ajax': 'add_purchase_returns',
    },
    'apps.expenses.views': {
        'expense_list': 'view_expenses',
        'expense_table_api': 'view_expenses',
        'expense_create': 'add_expenses',
        'expense_detail_api': 'view_expenses',
        'expense_edit': 'change_expenses',
        'expense_confirm_ajax': 'change_expenses',
        'expense_cancel_ajax': 'delete_expenses',
        'category_list_api': 'view_expense_categories',
        'category_create_api': 'add_expense_categories',
    },
    'apps.treasury.views': {
        'treasury_list': 'view_treasuries',
        'treasury_table_api': 'view_treasuries',
        'treasury_create_api': 'add_treasuries',
        'treasury_detail_api': 'view_treasuries',
        'treasury_update_api': 'change_treasuries',
        'treasury_delete_api': 'delete_treasuries',
        'treasury_transactions_api': 'view_treasuries',
    },
}

DECORATOR_LINE_TEMPLATE = "@require_permission('{perm}')\n"
IMPORT_LINE = 'from apps.accounts.decorators import require_permission\n'
LOGIN_REQUIRED_IMPORT = 'from django.contrib.auth.decorators import login_required\n'

FUNC_DEF_PATTERN = re.compile(r'^def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(')


def _get_view_path(module_name):
    path = module_name.replace('.', '/') + '.py'
    return Path(path)


def _has_require_permission_import(lines):
    return any(line.strip() == IMPORT_LINE.strip() for line in lines)


def _add_import(lines):
    if _has_require_permission_import(lines):
        return lines
    for idx, line in enumerate(lines):
        if line.strip() == LOGIN_REQUIRED_IMPORT.strip():
            lines.insert(idx + 1, IMPORT_LINE)
            return lines
    lines.insert(0, IMPORT_LINE)
    return lines


def _has_decorator(lines, start_idx, decorator):
    for idx in range(start_idx - 1, -1, -1):
        if lines[idx].strip().startswith('@'):
            if lines[idx].strip() == decorator.strip():
                return True
        elif lines[idx].strip() == '':
            continue
        else:
            break
    return False


def _insert_decorator(lines, func_idx, decorator):
    insert_idx = func_idx
    found_login_required = False
    for idx in range(func_idx - 1, -1, -1):
        stripped = lines[idx].strip()
        if stripped.startswith('@'):
            if stripped == LOGIN_REQUIRED_IMPORT.strip().replace('import ', ''):
                continue
            if stripped == '@login_required':
                found_login_required = True
                continue
            if found_login_required:
                insert_idx = idx + 1
                break
            insert_idx = idx
        elif stripped == '':
            continue
        else:
            break
    if not found_login_required:
        insert_idx = func_idx
    lines.insert(insert_idx, decorator)
    return lines


def apply_decorators_to_file(file_path, functions_map):
    path = Path(file_path)
    if not path.exists():
        return 0, []

    content = path.read_text()
    lines = content.splitlines(keepends=True)
    modified = False
    added = []

    if not _has_require_permission_import(lines):
        lines = _add_import(lines)
        modified = True

    for idx, line in enumerate(lines):
        match = FUNC_DEF_PATTERN.match(line)
        if not match:
            continue
        func_name = match.group('name')
        if func_name not in functions_map:
            continue
        perm = functions_map[func_name]
        decorator = DECORATOR_LINE_TEMPLATE.format(perm=perm)
        if _has_decorator(lines, idx, decorator):
            continue
        lines = _insert_decorator(lines, idx, decorator)
        modified = True
        added.append((func_name, perm))

    if modified:
        path.write_text(''.join(lines))
    return len(added), added


def main():
    total_added = 0
    report = []
    for module_name, functions_map in PERMISSION_MAPPING.items():
        file_path = _get_view_path(module_name)
        added_count, added = apply_decorators_to_file(file_path, functions_map)
        total_added += added_count
        if added:
            report.append((file_path, added))

    print(f'Updated {len(report)} file(s), added {total_added} decorator(s).')
    for file_path, added in report:
        print(f'  {file_path}:')
        for func_name, perm in added:
            print(f'    {func_name} -> {perm}')


if __name__ == '__main__':
    main()
    