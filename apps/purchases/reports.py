"""
تقارير المشتريات
================

يحتوي على الدوال المساعدة لإنشاء تقارير مختلفة:
  - ملخص المشتريات (بالفترة، الإجمالي، المتوسط)
  - المشتريات حسب المورد
  - المشتريات حسب المنتج
  - المشتريات حسب التاريخ (يومي / أسبوعي / شهري)
"""

import locale
from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Sum, Count, F, Q
from django.utils import timezone

# Set locale for Arabic number formatting
try:
    locale.setlocale(locale.LC_ALL, 'ar_SA.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'ar.UTF-8')
    except locale.Error:
        # Fallback to C locale if Arabic not available
        locale.setlocale(locale.LC_ALL, 'C')

from .models import PurchaseInvoice, PurchaseInvoiceLine, PurchaseReturn


def format_number(value, decimals=2):
    """تنسيق الرقم بالفواصل العربية"""
    try:
        if decimals == 0:
            return locale.format_string('%.0f', value, grouping=True)
        else:
            return locale.format_string(f'%.{decimals}f', value, grouping=True)
    except (ValueError, TypeError):
        return str(value)


class PurchasesReportGenerator:
    """فئة شاملة لإنشاء تقارير المشتريات"""

    def __init__(self, tenant, start_date=None, end_date=None):
        self.tenant = tenant
        self.start_date = start_date or (timezone.now().date() - timedelta(days=30))
        self.end_date = end_date or timezone.now().date()

    def get_summary_report(self):
        """تقرير ملخص المشتريات"""
        invoices = PurchaseInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date
        ).prefetch_related('lines')

        total_amount = Decimal('0')
        total_tax = Decimal('0')
        total_quantity = Decimal('0')
        total_count = 0

        for invoice in invoices:
            total_amount += invoice.grand_total or 0
            total_tax += invoice.tax_amount or 0
            total_count += 1
            for line in invoice.lines.all():
                total_quantity += line.quantity or 0

        return {
            'period': {
                'start': self.start_date,
                'end': self.end_date
            },
            'summary': {
                'invoice_count': format_number(total_count, 0),
                'total_quantity': format_number(float(total_quantity), 2),
                'total_amount': format_number(float(total_amount), 2),
                'total_tax': format_number(float(total_tax), 2),
                'avg_invoice_amount': format_number(float(total_amount / total_count) if total_count > 0 else 0, 2),
            },
            'details': []
        }

    def get_by_supplier_report(self):
        """تقرير المشتريات حسب المورد"""
        from apps.suppliers.models import Supplier

        suppliers = Supplier.objects.filter(
            tenant=self.tenant,
            purchase_invoices__status='confirmed',
            purchase_invoices__invoice_date__gte=self.start_date,
            purchase_invoices__invoice_date__lte=self.end_date
        ).distinct().prefetch_related('purchase_invoices')

        data = []
        for supplier in suppliers:
            invoices = supplier.purchase_invoices.filter(
                status='confirmed',
                invoice_date__gte=self.start_date,
                invoice_date__lte=self.end_date
            )

            total_amount = Decimal('0')
            total_quantity = Decimal('0')

            for invoice in invoices:
                total_amount += invoice.grand_total or 0
                for line in invoice.lines.all():
                    total_quantity += line.quantity or 0

            if invoices.exists():
                data.append({
                    'supplier_id': supplier.id,
                    'supplier_name': supplier.name,
                    'invoice_count': format_number(invoices.count(), 0),
                    'total_quantity': format_number(float(total_quantity), 2),
                    'total_amount': format_number(float(total_amount), 2),
                    'avg_invoice_amount': format_number(float(total_amount / invoices.count()), 2),
                })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'data': sorted(data, key=lambda x: x['total_amount'], reverse=True)
        }

    def get_by_item_report(self):
        """تقرير المشتريات حسب المنتج"""
        from apps.items.models import Item

        items = Item.objects.filter(
            tenant=self.tenant,
            purchase_lines__invoice__status='confirmed',
            purchase_lines__invoice__invoice_date__gte=self.start_date,
            purchase_lines__invoice__invoice_date__lte=self.end_date
        ).distinct().prefetch_related('purchase_lines')

        data = []
        for item in items:
            lines = item.purchase_lines.filter(
                invoice__status='confirmed',
                invoice__invoice_date__gte=self.start_date,
                invoice__invoice_date__lte=self.end_date
            )

            total_quantity = Decimal('0')
            total_amount = Decimal('0')

            for line in lines:
                total_quantity += line.quantity or 0
                total_amount += (line.quantity or 0) * (line.unit_cost or 0)

            if lines.exists():
                data.append({
                    'item_id': item.id,
                    'item_name': item.name,
                    'unit': item.unit.name if item.unit else '',
                    'quantity_purchased': format_number(float(total_quantity), 2),
                    'total_amount': format_number(float(total_amount), 2),
                    'avg_unit_cost': format_number(float(total_amount / total_quantity) if total_quantity > 0 else 0, 2),
                    'purchase_lines': format_number(lines.count(), 0),
                })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'data': sorted(data, key=lambda x: x['total_amount'], reverse=True)
        }

    def get_by_date_report(self, group_by='day'):
        """تقرير المشتريات حسب التاريخ (يومي/أسبوعي/شهري)"""
        invoices = PurchaseInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date
        ).order_by('invoice_date').prefetch_related('lines')

        data = {}

        for invoice in invoices:
            if group_by == 'day':
                key = invoice.invoice_date.strftime('%Y-%m-%d')
                label = invoice.invoice_date.strftime('%d/%m/%Y')
            elif group_by == 'week':
                # Get week starting date
                start = invoice.invoice_date - timedelta(days=invoice.invoice_date.weekday())
                key = start.strftime('%Y-W%W')
                label = f"Week {start.strftime('%W/%Y')}"
            else:  # month
                key = invoice.invoice_date.strftime('%Y-%m')
                label = invoice.invoice_date.strftime('%B %Y')

            if key not in data:
                data[key] = {
                    'label': label,
                    'invoice_count': 0,
                    'total_amount': Decimal('0'),
                    'total_quantity': Decimal('0'),
                }

            data[key]['invoice_count'] += 1
            data[key]['total_amount'] += invoice.grand_total or 0
            for line in invoice.lines.all():
                data[key]['total_quantity'] += line.quantity or 0

        # Convert to list
        result = []
        for key in sorted(data.keys()):
            entry = data[key]
            entry['invoice_count'] = format_number(entry['invoice_count'], 0)
            entry['total_amount'] = format_number(float(entry['total_amount']), 2)
            entry['total_quantity'] = format_number(float(entry['total_quantity']), 2)
            result.append(entry)

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'group_by': group_by,
            'data': result
        }