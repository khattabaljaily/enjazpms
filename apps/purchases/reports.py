"""
تقارير المشتريات
================

يحتوي على الدوال المساعدة لإنشاء تقارير مختلفة:
  - ملخص المشتريات (بالفترة، الإجمالي، المتوسط)
  - المشتريات حسب المورد
  - المشتريات حسب المنتج
  - المشتريات حسب التاريخ (يومي / أسبوعي / شهري)
"""

from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Sum, Count, F, Q
from django.utils import timezone

from .models import PurchaseInvoice, PurchaseInvoiceLine, PurchaseReturn, PurchasePayment, SupplierLedger


def format_number(value, decimals=2):
    """تنسيق الرقم بالفواصل الإنجليزية (فاصلة عشرية نقطة، فاصلة الآلاف فاصلة)"""
    try:
        if decimals == 0:
            return f"{int(value):,}"
        else:
            return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


class PurchasesReportGenerator:
    """فئة شاملة لإنشاء تقارير المشتريات"""

    def __init__(self, tenant, start_date=None, end_date=None):
        self.tenant = tenant
        self.start_date = start_date or (timezone.localdate() - timedelta(days=30))
        self.end_date = end_date or timezone.localdate()

    def get_summary_report(self):
        """تقرير ملخص المشتريات — مع قائمة الفواتير التفصيلية"""
        invoices = PurchaseInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date
        ).select_related('supplier').prefetch_related('lines').order_by('-invoice_date')

        total_amount = Decimal('0')
        total_tax = Decimal('0')
        total_quantity = Decimal('0')
        total_count = 0
        details = []

        for invoice in invoices:
            inv_qty = Decimal('0')
            for line in invoice.lines.all():
                inv_qty += line.quantity or 0
                total_quantity += line.quantity or 0
            total_amount += invoice.grand_total or 0
            total_tax += invoice.tax_amount or 0
            total_count += 1
            details.append({
                'invoice_number': invoice.invoice_number,
                'invoice_date': invoice.invoice_date,
                'supplier_name': invoice.supplier.name if invoice.supplier else '—',
                'total_quantity': format_number(float(inv_qty), 2),
                'grand_total': format_number(float(invoice.grand_total or 0), 2),
            })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'invoice_count': format_number(total_count, 0),
                'total_quantity': format_number(float(total_quantity), 2),
                'total_amount': format_number(float(total_amount), 2),
                'total_tax': format_number(float(total_tax), 2),
                'avg_invoice_amount': format_number(float(total_amount / total_count) if total_count > 0 else 0, 2),
            },
            'details': details,
        }

    def get_by_supplier_report(self, supplier_id=None):
        """تقرير المشتريات حسب المورد"""
        from apps.suppliers.models import Supplier
        # If a specific supplier is requested, return invoices detail for that supplier
        if supplier_id:
            try:
                supplier = Supplier.objects.get(tenant=self.tenant, id=supplier_id)
            except Supplier.DoesNotExist:
                return {'period': {'start': self.start_date, 'end': self.end_date}, 'data': []}

            invoices = supplier.purchase_invoices.filter(
                status='confirmed',
                invoice_date__gte=self.start_date,
                invoice_date__lte=self.end_date
            ).prefetch_related('lines')

            detail_rows = []
            for invoice in invoices:
                total_quantity = Decimal('0')
                for line in invoice.lines.all():
                    total_quantity += line.quantity or 0

                # count distinct items in the invoice (عدد الأصناف)
                try:
                    item_count = invoice.lines.values_list('item', flat=True).distinct().count()
                except Exception:
                    item_count = len({l.item_id for l in invoice.lines.all()})

                detail_rows.append({
                    'invoice_id': invoice.id,
                    'invoice_number': invoice.invoice_number,
                    'invoice_date': invoice.invoice_date,
                    'item_count': format_number(item_count, 0),
                    'total_quantity': format_number(float(total_quantity), 2),
                    'grand_total': format_number(float(invoice.grand_total or 0), 2),
                })

            return {
                'period': {'start': self.start_date, 'end': self.end_date},
                'supplier': {'id': supplier.id, 'name': supplier.name},
                'data': detail_rows
            }

        # Default: aggregated per-supplier
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

    def get_by_item_report(self, item_id=None):
        """تقرير المشتريات حسب المنتج"""
        from apps.items.models import Item

        if item_id:
            try:
                item = Item.objects.get(tenant=self.tenant, id=item_id)
            except Item.DoesNotExist:
                return {'period': {'start': self.start_date, 'end': self.end_date}, 'item': None, 'data': []}

            lines = item.purchase_lines.filter(
                invoice__status='confirmed',
                invoice__invoice_date__gte=self.start_date,
                invoice__invoice_date__lte=self.end_date,
            ).select_related('invoice', 'invoice__supplier').order_by('-invoice__invoice_date')

            data = []
            total_quantity = Decimal('0')
            total_amount = Decimal('0')
            for line in lines:
                qty = line.quantity or Decimal('0')
                cost = line.unit_cost or Decimal('0')
                lt = qty * cost
                total_quantity += qty
                total_amount += lt
                data.append({
                    'invoice_number': line.invoice.invoice_number,
                    'invoice_date': line.invoice.invoice_date,
                    'supplier_name': line.invoice.supplier.name if line.invoice.supplier else '—',
                    'quantity': format_number(float(qty), 2),
                    'unit_cost': format_number(float(cost), 2),
                    'line_total': format_number(float(lt), 2),
                })

            return {
                'period': {'start': self.start_date, 'end': self.end_date},
                'item': {'id': item.id, 'name': item.name, 'unit': item.unit.name if item.unit else ''},
                'summary': {
                    'total_quantity': format_number(float(total_quantity), 2),
                    'total_amount': format_number(float(total_amount), 2),
                },
                'data': data,
            }

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
            'item': None,
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
    def get_supplier_statement(self, supplier_id):
        """كشف حساب مورد — SupplierLedger مرتبة بالتاريخ"""
        from apps.suppliers.models import Supplier
        try:
            supplier = Supplier.objects.get(pk=supplier_id, tenant=self.tenant)
        except Supplier.DoesNotExist:
            return None

        hc_mode = getattr(self.tenant, 'hard_currency_mode', False)
        supplier_currency = (supplier.currency or '').strip()
        is_hc_supplier = hc_mode and bool(supplier_currency)

        # Opening balance = last entry before start_date
        if is_hc_supplier:
            pre_entry = SupplierLedger.objects.filter(
                tenant=self.tenant, supplier=supplier,
                entry_date__lt=self.start_date,
                hc_running_balance__isnull=False,
            ).order_by('entry_date', 'id').last()
            opening_balance = float(pre_entry.hc_running_balance) if pre_entry else float(supplier.opening_balance or 0)
        else:
            pre_entry = SupplierLedger.objects.filter(
                tenant=self.tenant, supplier=supplier,
                entry_date__lt=self.start_date,
            ).order_by('entry_date', 'id').last()
            opening_balance = float(pre_entry.running_balance) if pre_entry else float(supplier.opening_balance or 0)

        all_entries = list(SupplierLedger.objects.filter(
            tenant=self.tenant,
            supplier=supplier,
            entry_date__gte=self.start_date,
            entry_date__lte=self.end_date,
        ).order_by('entry_date', 'id'))

        # Collapse edit patterns within the date range
        max_reversal_id = {}
        for e in all_entries:
            if e.is_reversal and e.reference_type and e.reference_id:
                key = (e.reference_type, e.reference_id)
                max_reversal_id[key] = max(max_reversal_id.get(key, 0), e.id)

        entries = []
        for e in all_entries:
            if e.is_reversal:
                continue
            key = (e.reference_type, e.reference_id) if (e.reference_type and e.reference_id) else None
            rev_id = max_reversal_id.get(key, 0) if key else 0
            if rev_id > 0 and e.id < rev_id:
                continue
            e._is_edited = rev_id > 0
            entries.append(e)

        supplier_opening = float(supplier.opening_balance or 0)

        data = [{
            'entry_date': self.start_date,
            'entry_type': 'رصيد افتتاحي',
            'entry_type_key': 'opening',
            'amount': format_number(opening_balance, 2),
            'running_balance': format_number(opening_balance, 2),
            'notes': 'رصيد أول المدة',
            'is_edited': False,
        }]
        for e in entries:
            if is_hc_supplier and e.hc_amount is not None:
                amt = float(e.hc_amount)
                bal = float(e.hc_running_balance) if e.hc_running_balance is not None else None
            else:
                amt = float(e.amount)
                bal = float(e.running_balance) + supplier_opening
            data.append({
                'entry_date': e.entry_date,
                'entry_type': e.get_entry_type_display(),
                'entry_type_key': e.entry_type,
                'amount': format_number(abs(amt), 2),
                'running_balance': format_number(bal, 2) if bal is not None else '—',
                'notes': e.notes,
                'is_edited': getattr(e, '_is_edited', False),
            })

        if entries:
            last = entries[-1]
            if is_hc_supplier and last.hc_running_balance is not None:
                total_debit  = sum(float(e.hc_amount) for e in entries if e.hc_amount and float(e.hc_amount) > 0)
                total_credit = abs(sum(float(e.hc_amount) for e in entries if e.hc_amount and float(e.hc_amount) < 0))
                closing_balance = float(last.hc_running_balance)
            else:
                total_debit  = sum(float(e.amount) for e in entries if float(e.amount) > 0)
                total_credit = abs(sum(float(e.amount) for e in entries if float(e.amount) < 0))
                closing_balance = float(last.running_balance) + supplier_opening
        else:
            total_debit = total_credit = 0.0
            closing_balance = opening_balance

        return {
            'supplier': supplier,
            'supplier_currency': supplier_currency,
            'is_hc_supplier': is_hc_supplier,
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'opening_balance': format_number(opening_balance, 2),
                'total_debit': format_number(total_debit, 2),
                'total_credit': format_number(total_credit, 2),
                'closing_balance': format_number(closing_balance, 2),
            },
            'data': data,
        }

    def get_supplier_balances(self):
        """أرصدة الموردين — مجموع حركات الدفتر + الرصيد الافتتاحي لكل مورد"""
        from apps.suppliers.models import Supplier
        from django.db.models import Sum as _Sum

        hc_mode = getattr(self.tenant, 'hard_currency_mode', False)
        hc_rate = Decimal(str(self.tenant.exchange_rate or 1)) if hc_mode and self.tenant.exchange_rate else None

        suppliers = Supplier.objects.filter(tenant=self.tenant).order_by('name')
        data = []
        for s in suppliers:
            supplier_currency = (s.currency or '').strip()
            # Mirror exact logic from suppliers list page: HC = has currency + hc_mode + rate exists
            is_hc = hc_mode and bool(supplier_currency) and hc_rate is not None

            opening = float(s.opening_balance or 0)
            if is_hc:
                hc_sum = float(
                    SupplierLedger.objects
                    .filter(tenant=self.tenant, supplier=s, hc_amount__isnull=False)
                    .aggregate(s=_Sum('hc_amount'))['s'] or 0
                )
                balance = hc_sum + opening
            else:
                local_sum = float(
                    SupplierLedger.objects
                    .filter(tenant=self.tenant, supplier=s)
                    .aggregate(s=_Sum('amount'))['s'] or 0
                )
                balance = local_sum + opening

            if balance > 0:
                data.append({
                    'code': s.code,
                    'name': s.name,
                    'phone': s.phone,
                    'credit_limit': format_number(float(s.credit_limit), 2),
                    'balance': format_number(balance, 2),
                    'balance_raw': balance,
                    'currency': supplier_currency,
                    'is_hc': is_hc,
                })

        total_local = sum(r['balance_raw'] for r in data if not r['is_hc'])
        total_hc = sum(r['balance_raw'] for r in data if r['is_hc'])
        hc_currency = next((r['currency'] for r in data if r['is_hc']), '')
        return {
            'data': data,
            'summary': {
                'total_local': format_number(total_local, 2),
                'total_hc': format_number(total_hc, 2),
                'hc_currency': hc_currency,
            },
        }

    def get_payments_report(self, supplier_id=None):
        """تقرير مدفوعات الموردين — من SupplierLedger (يشمل المدفوعات المستقلة والمرتبطة بفواتير)"""
        qs = SupplierLedger.objects.filter(
            tenant=self.tenant,
            entry_type='payment',
            entry_date__gte=self.start_date,
            entry_date__lte=self.end_date,
        ).select_related('supplier').order_by('-entry_date', '-id')

        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)

        entries = list(qs)

        METHOD_MAP = {
            'supplier_payment_cash':   ('نقداً', 'cash'),
            'supplier_payment_hc_cash':('نقداً', 'cash'),
            'supplier_payment_bank':   ('تحويل بنكي', 'bank'),
            'purchase_payment_cash':   ('نقداً', 'cash'),
            'purchase_payment_bank':   ('تحويل بنكي', 'bank'),
            'purchase_invoice':        ('نقداً', 'cash'),  # legacy entries
        }

        # Bulk-fetch invoice numbers for purchase_payment_* entries
        invoice_ref_ids = {
            e.reference_id for e in entries
            if e.reference_type in ('purchase_payment_cash', 'purchase_payment_bank', 'purchase_invoice')
            and e.reference_id
        }
        invoice_map = {}
        if invoice_ref_ids:
            invoice_map = {
                inv.id: inv.invoice_number
                for inv in PurchaseInvoice.objects.filter(id__in=invoice_ref_ids).only('id', 'invoice_number')
            }

        hc_mode = getattr(self.tenant, 'hard_currency_mode', False)
        tenant_currency = (getattr(self.tenant, 'currency', '') or '').strip()
        # totals keyed by currency string ('' = local)
        totals: dict = {}  # currency -> {'cash': float, 'bank': float}
        data = []

        for e in entries:
            method_label, method_key = METHOD_MAP.get(e.reference_type, ('—', ''))

            sup_currency = (e.supplier.currency or '').strip() if e.supplier else ''
            # HC supplier: hc_mode on, has a currency different from tenant's local currency
            is_hc = hc_mode and bool(sup_currency) and sup_currency != tenant_currency

            if is_hc:
                amt_in_currency = abs(float(e.hc_amount or 0))
                currency_label = sup_currency
            else:
                amt_in_currency = abs(float(e.amount))
                currency_label = ''

            display_amount = format_number(amt_in_currency, 2)

            # Track totals per currency
            cur_key = currency_label or '__local__'
            if cur_key not in totals:
                totals[cur_key] = {'currency': currency_label, 'cash': 0.0, 'bank': 0.0}
            if method_key == 'cash':
                totals[cur_key]['cash'] += amt_in_currency
            elif method_key == 'bank':
                totals[cur_key]['bank'] += amt_in_currency

            # Invoice number linked to this payment
            invoice_number = ''
            if e.reference_type in ('purchase_payment_cash', 'purchase_payment_bank', 'purchase_invoice') and e.reference_id:
                invoice_number = invoice_map.get(e.reference_id, '')

            # Clean notes
            notes_clean = (e.notes or '').strip()
            if 'مرجع:' in notes_clean:
                notes_clean = notes_clean.split('|')[0].replace('مرجع:', '').strip().strip(' |')

            data.append({
                'payment_date': e.entry_date,
                'supplier_name': e.supplier.name if e.supplier else '—',
                'payment_method': method_label,
                'payment_method_key': method_key,
                'amount': display_amount,
                'currency_label': currency_label,
                'invoice_number': invoice_number,
                'notes': notes_clean,
            })

        # Build summary list per currency for template
        currency_summaries = []
        for cur_key, t in totals.items():
            cur_label = t['currency']
            currency_summaries.append({
                'currency': cur_label,
                'cash': format_number(t['cash'], 2),
                'bank': format_number(t['bank'], 2),
                'total': format_number(t['cash'] + t['bank'], 2),
            })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'payment_count': format_number(len(data), 0),
                'currency_summaries': currency_summaries,
            },
            'data': data,
        }

    def get_returns_report(self):
        """تقرير مرتجعات المشتريات بالفترة — سطر لكل صنف مرتجع"""
        from .models import PurchaseReturnLine
        lines = PurchaseReturnLine.objects.filter(
            tenant=self.tenant,
            purchase_return__status='confirmed',
            purchase_return__return_date__gte=self.start_date,
            purchase_return__return_date__lte=self.end_date,
        ).select_related(
            'purchase_return',
            'purchase_return__original_invoice',
            'purchase_return__original_invoice__supplier',
            'item',
        ).order_by('-purchase_return__return_date')

        data = []
        total_returned = Decimal('0')
        for line in lines:
            r = line.purchase_return
            lt = line.line_total or Decimal('0')
            total_returned += lt
            data.append({
                'return_date': r.return_date,
                'return_number': r.return_number,
                'invoice_number': r.original_invoice.invoice_number,
                'supplier_name': r.original_invoice.supplier.name if r.original_invoice.supplier else '—',
                'item_name': line.item.name if line.item else '—',
                'returned_quantity': format_number(float(line.returned_quantity or 0), 2),
                'unit_cost': format_number(float(line.unit_cost or 0), 2),
                'line_total': format_number(float(lt), 2),
                'refund_method': r.get_refund_method_display(),
            })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'line_count': format_number(len(data), 0),
                'total_returned': format_number(float(total_returned), 2),
            },
            'data': data,
        }

    def get_by_user_report(self, user_id=None):
        """تقرير المشتريات حسب المستخدم"""
        from django.contrib.auth import get_user_model
        User = get_user_model()

        base_qs = PurchaseInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date,
        )

        if user_id:
            try:
                user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                return {'period': {'start': self.start_date, 'end': self.end_date}, 'user': None, 'data': []}

            invoices = base_qs.filter(created_by=user).select_related('supplier').prefetch_related('lines').order_by('-invoice_date')
            data = []
            for invoice in invoices:
                inv_qty = Decimal('0')
                for line in invoice.lines.all():
                    inv_qty += line.quantity or 0
                data.append({
                    'invoice_number': invoice.invoice_number,
                    'invoice_date': invoice.invoice_date,
                    'supplier_name': invoice.supplier.name if invoice.supplier else '—',
                    'total_quantity': format_number(float(inv_qty), 2),
                    'grand_total': format_number(float(invoice.grand_total or 0), 2),
                })

            return {
                'period': {'start': self.start_date, 'end': self.end_date},
                'user': {'id': user.id, 'name': user.get_full_name() or user.username},
                'data': data,
            }

        user_ids = base_qs.values_list('created_by', flat=True).distinct()
        users = User.objects.filter(pk__in=user_ids)

        data = []
        for user in users:
            invoices = base_qs.filter(created_by=user)
            total_amount = Decimal('0')
            for invoice in invoices:
                total_amount += invoice.grand_total or 0
            count = invoices.count()
            data.append({
                'user_id': user.id,
                'user_name': user.get_full_name() or user.username,
                'invoice_count': format_number(count, 0),
                'total_amount': format_number(float(total_amount), 2),
                'avg_invoice_amount': format_number(float(total_amount / count) if count > 0 else 0, 2),
            })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'user': None,
            'data': sorted(data, key=lambda x: x['total_amount'], reverse=True),
        }

    def get_price_history_report(self, item_id=None):
        """تقرير تاريخ أسعار الشراء لكل منتج"""
        from apps.items.models import Item
        from collections import defaultdict

        base_lines = PurchaseInvoiceLine.objects.filter(
            invoice__tenant=self.tenant,
            invoice__status='confirmed',
            invoice__invoice_date__gte=self.start_date,
            invoice__invoice_date__lte=self.end_date,
        ).select_related('invoice', 'invoice__supplier', 'item', 'item__unit').order_by('invoice__invoice_date')

        if item_id:
            try:
                item = Item.objects.get(tenant=self.tenant, id=item_id)
            except Item.DoesNotExist:
                return {'period': {'start': self.start_date, 'end': self.end_date}, 'item': None, 'data': [], 'summary': {}}

            lines = base_lines.filter(item=item)
            data = []
            prices = []
            total_qty = Decimal('0')
            total_spent = Decimal('0')
            for line in lines:
                qty = line.quantity or Decimal('0')
                cost = line.unit_cost or Decimal('0')
                lt = qty * cost
                total_qty += qty
                total_spent += lt
                prices.append(float(cost))
                data.append({
                    'invoice_date': line.invoice.invoice_date,
                    'invoice_number': line.invoice.invoice_number,
                    'supplier_name': line.invoice.supplier.name if line.invoice.supplier else '—',
                    'quantity': format_number(float(qty), 2),
                    'unit_cost': format_number(float(cost), 2),
                    'unit_cost_raw': float(cost),
                    'line_total': format_number(float(lt), 2),
                })

            min_price = min(prices) if prices else 0
            max_price = max(prices) if prices else 0
            avg_price = sum(prices) / len(prices) if prices else 0

            return {
                'period': {'start': self.start_date, 'end': self.end_date},
                'item': {'id': item.id, 'name': item.name, 'unit': item.unit.name if item.unit else ''},
                'summary': {
                    'total_qty': format_number(float(total_qty), 2),
                    'total_spent': format_number(float(total_spent), 2),
                    'min_price': format_number(min_price, 2),
                    'max_price': format_number(max_price, 2),
                    'avg_price': format_number(avg_price, 2),
                    'purchase_count': format_number(len(data), 0),
                },
                'data': data,
            }

        agg = defaultdict(lambda: {
            'item_name': '', 'unit': '',
            'prices': [], 'total_qty': Decimal('0'), 'last_date': None, 'last_supplier': '—',
        })
        for line in base_lines:
            iid = line.item_id
            agg[iid]['item_name'] = line.item.name
            agg[iid]['unit'] = line.item.base_unit_name
            cost = float(line.unit_cost or 0)
            agg[iid]['prices'].append(cost)
            agg[iid]['total_qty'] += line.quantity or Decimal('0')
            if agg[iid]['last_date'] is None or line.invoice.invoice_date > agg[iid]['last_date']:
                agg[iid]['last_date'] = line.invoice.invoice_date
                agg[iid]['last_price'] = cost
                agg[iid]['last_supplier'] = line.invoice.supplier.name if line.invoice.supplier else '—'

        data = []
        for iid, v in agg.items():
            prices = v['prices']
            data.append({
                'item_id': iid,
                'item_name': v['item_name'],
                'unit': v['unit'],
                'purchase_count': format_number(len(prices), 0),
                'last_purchase_date': v['last_date'],
                'last_supplier': v['last_supplier'],
                'last_price': format_number(v.get('last_price', 0), 2),
                'min_price': format_number(min(prices), 2),
                'max_price': format_number(max(prices), 2),
                'avg_price': format_number(sum(prices) / len(prices), 2),
                'price_variance': format_number(max(prices) - min(prices), 2),
                'price_variance_raw': max(prices) - min(prices),
            })

        data.sort(key=lambda x: x['price_variance_raw'], reverse=True)
        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'item': None,
            'data': data,
        }
