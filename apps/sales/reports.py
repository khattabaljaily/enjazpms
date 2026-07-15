"""
تقارير المبيعات
================

يحتوي على الدوال المساعدة لإنشاء تقارير مختلفة:
  - ملخص المبيعات (بالفترة، الإجمالي، المتوسط)
  - المبيعات حسب العميل
  - المبيعات حسب المنتج
  - المبيعات حسب التاريخ (يومي / أسبوعي / شهري)
"""

from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Sum, Count, F, Q
from django.utils import timezone

from .models import SaleInvoice, SaleInvoiceLine, SaleReturn, SalePayment, CustomerLedger


def format_number(value, decimals=2):
    """تنسيق الرقم بالفواصل الإنجليزية (فاصلة عشرية نقطة، فاصلة الآلاف فاصلة)"""
    try:
        if decimals == 0:
            return f"{int(value):,}"
        else:
            return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


class SalesReportGenerator:
    """فئة شاملة لإنشاء تقارير المبيعات"""
    
    def __init__(self, tenant, start_date=None, end_date=None):
        self.tenant = tenant
        self.start_date = start_date or (timezone.localdate() - timedelta(days=30))
        self.end_date = end_date or timezone.localdate()
        
    def get_summary_report(self):
        """تقرير ملخص المبيعات — مع قائمة الفواتير التفصيلية"""
        invoices = SaleInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date
        ).select_related('customer').prefetch_related('lines').order_by('-invoice_date')

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
                'customer_name': invoice.customer.name if invoice.customer else '—',
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
    
    def get_by_customer_report(self, customer_id=None):
        """تقرير المبيعات حسب العميل"""
        from apps.customers.models import Customer
        # If a specific customer is requested, return invoice-level details
        if customer_id:
            try:
                customer = Customer.objects.get(tenant=self.tenant, id=customer_id)
            except Customer.DoesNotExist:
                return {'period': {'start': self.start_date, 'end': self.end_date}, 'data': []}

            invoices = customer.sale_invoices.filter(
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
                    # Fallback if queryset not available
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
                'customer': {'id': customer.id, 'name': customer.name},
                'data': detail_rows
            }

        # Default: aggregated per-customer
        customers = Customer.objects.filter(
            tenant=self.tenant,
            sale_invoices__status='confirmed',
            sale_invoices__invoice_date__gte=self.start_date,
            sale_invoices__invoice_date__lte=self.end_date
        ).distinct().prefetch_related('sale_invoices')

        data = []
        for customer in customers:
            invoices = customer.sale_invoices.filter(
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
                    'customer_id': customer.id,
                    'customer_name': customer.name,
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
        """تقرير المبيعات حسب المنتج — يدعم التفصيل لمنتج واحد"""
        from apps.items.models import Item

        if item_id:
            try:
                item = Item.objects.get(tenant=self.tenant, id=item_id)
            except Item.DoesNotExist:
                return {'period': {'start': self.start_date, 'end': self.end_date}, 'item': None, 'data': [], 'summary': {}}

            lines = item.sale_lines.filter(
                invoice__status='confirmed',
                invoice__invoice_date__gte=self.start_date,
                invoice__invoice_date__lte=self.end_date
            ).select_related('invoice', 'invoice__customer').order_by('-invoice__invoice_date')

            data = []
            total_qty = Decimal('0')
            total_amount = Decimal('0')
            for line in lines:
                qty = line.quantity or Decimal('0')
                price = line.unit_price or Decimal('0')
                subtotal = qty * price
                total_qty += qty
                total_amount += subtotal
                data.append({
                    'invoice_number': line.invoice.invoice_number,
                    'invoice_date': line.invoice.invoice_date,
                    'customer_name': line.invoice.customer.name if line.invoice.customer else '—',
                    'quantity': format_number(float(qty), 2),
                    'unit_price': format_number(float(price), 2),
                    'line_total': format_number(float(subtotal), 2),
                })

            return {
                'period': {'start': self.start_date, 'end': self.end_date},
                'item': {'id': item.id, 'name': item.name, 'unit': item.unit.name if item.unit else ''},
                'summary': {
                    'total_quantity': format_number(float(total_qty), 2),
                    'total_amount': format_number(float(total_amount), 2),
                },
                'data': data,
            }

        items = Item.objects.filter(
            tenant=self.tenant,
            sale_lines__invoice__status='confirmed',
            sale_lines__invoice__invoice_date__gte=self.start_date,
            sale_lines__invoice__invoice_date__lte=self.end_date
        ).distinct().prefetch_related('sale_lines')

        data = []
        for item in items:
            lines = item.sale_lines.filter(
                invoice__status='confirmed',
                invoice__invoice_date__gte=self.start_date,
                invoice__invoice_date__lte=self.end_date
            )
            total_quantity = Decimal('0')
            total_amount = Decimal('0')
            for line in lines:
                total_quantity += line.quantity or 0
                total_amount += (line.quantity or 0) * (line.unit_price or 0)
            if lines.exists():
                data.append({
                    'item_id': item.id,
                    'item_name': item.name,
                    'unit': item.unit.name if item.unit else '',
                    'quantity_sold': format_number(float(total_quantity), 2),
                    'total_amount': format_number(float(total_amount), 2),
                    'avg_unit_price': format_number(float(total_amount / total_quantity) if total_quantity > 0 else 0, 2),
                    'sale_lines': format_number(lines.count(), 0),
                })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'item': None,
            'data': sorted(data, key=lambda x: x['total_amount'], reverse=True),
        }
    
    def get_by_date_report(self, group_by='day'):
        """تقرير المبيعات حسب التاريخ (يومي/أسبوعي/شهري)"""
        invoices = SaleInvoice.objects.filter(
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

    def get_customer_statement(self, customer_id):
        """كشف حساب عميل — CustomerLedger مرتبة بالتاريخ"""
        from apps.customers.models import Customer
        try:
            customer = Customer.objects.get(pk=customer_id, tenant=self.tenant)
        except Customer.DoesNotExist:
            return None

        # Opening balance = last entry before start_date, or customer.opening_balance
        pre_entry = CustomerLedger.objects.filter(
            tenant=self.tenant,
            customer=customer,
            entry_date__lt=self.start_date,
        ).order_by('entry_date', 'id').last()
        if pre_entry:
            opening_balance = float(pre_entry.running_balance)
        else:
            opening_balance = float(customer.opening_balance or 0)

        all_entries = list(CustomerLedger.objects.filter(
            tenant=self.tenant,
            customer=customer,
            entry_date__gte=self.start_date,
            entry_date__lte=self.end_date,
        ).order_by('entry_date', 'id'))

        from apps.sales.services import build_customer_statement_timeline
        entry_type_labels = dict(CustomerLedger.ENTRY_TYPE_CHOICES)
        entries = build_customer_statement_timeline(self.tenant, all_entries)

        customer_opening = float(customer.opening_balance or 0)

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
            bal = float(e.running_balance) + customer_opening
            data.append({
                'entry_date': e.entry_date,
                'entry_type': entry_type_labels.get(e.entry_type, e.entry_type),
                'entry_type_key': e.entry_type,
                'amount': format_number(abs(float(e.amount)), 2),
                'running_balance': format_number(bal, 2),
                'notes': e.notes,
                'is_edited': e.is_edited,
                'is_reversal': e.is_reversal,
            })

        if entries:
            last = entries[-1]
            total_debit = sum(float(e.amount) for e in entries if float(e.amount) > 0)
            total_credit = abs(sum(float(e.amount) for e in entries if float(e.amount) < 0))
            closing_balance = float(last.running_balance) + customer_opening
        else:
            total_debit = total_credit = 0.0
            closing_balance = opening_balance

        return {
            'customer': customer,
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'opening_balance': format_number(opening_balance, 2),
                'total_debit': format_number(total_debit, 2),
                'total_credit': format_number(total_credit, 2),
                'closing_balance': format_number(closing_balance, 2),
            },
            'data': data,
        }

    def get_customer_balances(self):
        """أرصدة العملاء — آخر رصيد تراكمي لكل عميل"""
        from apps.customers.models import Customer
        from django.db.models import Max

        customers = Customer.objects.filter(tenant=self.tenant).order_by('name')
        data = []
        for c in customers:
            last_entry = (
                CustomerLedger.objects
                .filter(tenant=self.tenant, customer=c)
                .order_by('-entry_date', '-id')
                .first()
            )
            balance = float(last_entry.running_balance) if last_entry else float(c.opening_balance)
            data.append({
                'code': c.code,
                'name': c.name,
                'phone': c.phone,
                'credit_limit': format_number(float(c.credit_limit), 2),
                'balance': format_number(balance, 2),
                'balance_raw': balance,
            })

        total_balance = sum(r['balance_raw'] for r in data)
        total_debtors = sum(1 for r in data if r['balance_raw'] > 0)
        return {
            'data': data,
            'summary': {
                'total_customers': format_number(len(data), 0),
                'total_debtors': format_number(total_debtors, 0),
                'total_balance': format_number(total_balance, 2),
            },
        }

    def get_payments_report(self, customer_id=None):
        """تقرير مدفوعات العملاء (CustomerLedger entry_type=payment) بالفترة"""
        METHOD_LABELS = {
            'customer_payment_cash': ('نقداً', 'cash'),
            'customer_payment_bank': ('بنكي', 'bank'),
            'sale_payment':          ('دفعة فاتورة', 'invoice'),
        }

        qs = CustomerLedger.objects.filter(
            tenant=self.tenant,
            entry_type='payment',
            entry_date__gte=self.start_date,
            entry_date__lte=self.end_date,
            is_reversal=False,
        ).select_related('customer').order_by('-entry_date', '-id')

        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        data = []
        total_cash = total_bank = 0.0
        for e in qs:
            method_label, method_key = METHOD_LABELS.get(e.reference_type, ('—', 'other'))
            amt = abs(float(e.amount))
            if method_key == 'cash':
                total_cash += amt
            elif method_key == 'bank':
                total_bank += amt
            data.append({
                'payment_date': e.entry_date,
                'customer_name': e.customer.name if e.customer else '—',
                'payment_method': method_label,
                'payment_method_key': method_key,
                'amount': format_number(amt, 2),
                'notes': e.notes or '—',
            })

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'payment_count': format_number(len(data), 0),
                'total_cash': format_number(total_cash, 2),
                'total_bank': format_number(total_bank, 2),
                'total_amount': format_number(total_cash + total_bank, 2),
            },
            'data': data,
        }

    def get_returns_report(self):
        """تقرير مرتجعات المبيعات — صف لكل منتج مُرتجَع"""
        returns = SaleReturn.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            return_date__gte=self.start_date,
            return_date__lte=self.end_date,
        ).select_related('original_invoice', 'original_invoice__customer').prefetch_related('lines__item').order_by('-return_date')

        data = []
        total_returned = Decimal('0')
        for r in returns:
            for line in r.lines.all():
                data.append({
                    'return_date': r.return_date,
                    'return_number': r.return_number,
                    'invoice_number': r.original_invoice.invoice_number,
                    'customer_name': r.original_invoice.customer.name if r.original_invoice.customer else '—',
                    'item_name': line.item.name,
                    'returned_quantity': format_number(float(line.returned_quantity), 2),
                    'unit_price': format_number(float(line.unit_price), 2),
                    'line_total': format_number(float(line.line_total), 2),
                    'refund_method': r.get_refund_method_display(),
                })
                total_returned += line.line_total

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'return_count': format_number(returns.count(), 0),
                'total_returned': format_number(float(total_returned), 2),
            },
            'data': data,
        }

    def get_by_user_report(self, user_id=None):
        """تقرير المبيعات حسب المستخدم/البائع"""
        from collections import defaultdict

        base_qs = SaleInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date,
        ).select_related('customer', 'created_by').order_by('-invoice_date')

        if user_id:
            invoices = base_qs.filter(created_by_id=user_id)
            user_obj = invoices.first().created_by if invoices.exists() else None
            data = []
            total = Decimal('0')
            for inv in invoices:
                data.append({
                    'invoice_number': inv.invoice_number,
                    'invoice_date': inv.invoice_date,
                    'customer_name': inv.customer.name if inv.customer else '—',
                    'grand_total': format_number(float(inv.grand_total or 0), 2),
                })
                total += inv.grand_total or 0
            return {
                'period': {'start': self.start_date, 'end': self.end_date},
                'user': {'id': user_id, 'name': str(user_obj) if user_obj else '—'},
                'summary': {
                    'invoice_count': format_number(len(data), 0),
                    'total_amount': format_number(float(total), 2),
                },
                'data': data,
            }

        agg = defaultdict(lambda: {'invoice_count': 0, 'total_amount': Decimal('0'), 'user_name': '—'})
        for inv in base_qs:
            uid = inv.created_by_id or 0
            agg[uid]['user_name'] = str(inv.created_by) if inv.created_by else '—'
            agg[uid]['invoice_count'] += 1
            agg[uid]['total_amount'] += inv.grand_total or 0

        data = [
            {
                'user_id': uid,
                'user_name': v['user_name'],
                'invoice_count': format_number(v['invoice_count'], 0),
                'total_amount': format_number(float(v['total_amount']), 2),
            }
            for uid, v in sorted(agg.items(), key=lambda x: x[1]['total_amount'], reverse=True)
        ]
        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'user': None,
            'data': data,
        }

    def get_profit_margin_report(self, item_id=None):
        """تقرير هامش الربح لكل منتج — الإيراد مقابل التكلفة"""
        from apps.items.models import Item

        base_lines = SaleInvoiceLine.objects.filter(
            invoice__tenant=self.tenant,
            invoice__status='confirmed',
            invoice__invoice_date__gte=self.start_date,
            invoice__invoice_date__lte=self.end_date,
        ).select_related('invoice', 'invoice__customer', 'item')

        if item_id:
            try:
                item = Item.objects.get(tenant=self.tenant, id=item_id)
            except Item.DoesNotExist:
                return {'period': {'start': self.start_date, 'end': self.end_date}, 'item': None, 'data': [], 'summary': {}}

            lines = base_lines.filter(item=item).order_by('-invoice__invoice_date')
            data = []
            total_qty = Decimal('0')
            total_revenue = Decimal('0')
            total_cogs = Decimal('0')
            for line in lines:
                qty = line.quantity or Decimal('0')
                price = line.unit_price or Decimal('0')
                cost = line.cost_price_snapshot if line.cost_price_snapshot else (line.item.cost_price or Decimal('0'))
                revenue = qty * price
                cogs = qty * cost
                profit = revenue - cogs
                margin = (profit / revenue * 100) if revenue else Decimal('0')
                total_qty += qty
                total_revenue += revenue
                total_cogs += cogs
                data.append({
                    'invoice_number': line.invoice.invoice_number,
                    'invoice_date': line.invoice.invoice_date,
                    'customer_name': line.invoice.customer.name if line.invoice.customer else '—',
                    'quantity': format_number(float(qty), 2),
                    'unit_price': format_number(float(price), 2),
                    'unit_cost': format_number(float(cost), 2),
                    'revenue': format_number(float(revenue), 2),
                    'cogs': format_number(float(cogs), 2),
                    'profit': format_number(float(profit), 2),
                    'margin': format_number(float(margin), 1),
                    'profit_raw': float(profit),
                })
            total_profit = total_revenue - total_cogs
            total_margin = (total_profit / total_revenue * 100) if total_revenue else Decimal('0')
            return {
                'period': {'start': self.start_date, 'end': self.end_date},
                'item': {'id': item.id, 'name': item.name},
                'summary': {
                    'total_quantity': format_number(float(total_qty), 2),
                    'total_revenue': format_number(float(total_revenue), 2),
                    'total_cogs': format_number(float(total_cogs), 2),
                    'total_profit': format_number(float(total_profit), 2),
                    'avg_margin': format_number(float(total_margin), 1),
                    'total_profit_raw': float(total_profit),
                },
                'data': data,
            }

        from collections import defaultdict
        agg = defaultdict(lambda: {
            'item_name': '', 'unit': '', 'cost_price': Decimal('0'),
            'total_qty': Decimal('0'), 'total_revenue': Decimal('0'), 'total_cogs': Decimal('0'),
        })
        for line in base_lines:
            iid = line.item_id
            agg[iid]['item_name'] = line.item.name
            agg[iid]['unit'] = line.item.base_unit_name
            qty = line.quantity or Decimal('0')
            price = line.unit_price or Decimal('0')
            cost = line.cost_price_snapshot if line.cost_price_snapshot else (line.item.cost_price or Decimal('0'))
            agg[iid]['cost_price'] = cost
            agg[iid]['total_qty'] += qty
            agg[iid]['total_revenue'] += qty * price
            agg[iid]['total_cogs'] += qty * cost

        data = []
        for iid, v in agg.items():
            rev = v['total_revenue']
            cogs = v['total_cogs']
            profit = rev - cogs
            margin = (profit / rev * 100) if rev else Decimal('0')
            data.append({
                'item_id': iid,
                'item_name': v['item_name'],
                'unit': v['unit'],
                'total_qty': format_number(float(v['total_qty']), 2),
                'total_revenue': format_number(float(rev), 2),
                'total_cogs': format_number(float(cogs), 2),
                'gross_profit': format_number(float(profit), 2),
                'gross_margin': format_number(float(margin), 1),
                'gross_profit_raw': float(profit),
                'gross_margin_raw': float(margin),
            })

        data.sort(key=lambda x: x['gross_profit_raw'], reverse=True)
        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'item': None,
            'data': data,
        }

    def get_by_payment_method_report(self):
        """تقرير المبيعات حسب طريقة الدفع"""
        from collections import defaultdict

        payments = SalePayment.objects.filter(
            tenant=self.tenant,
            payment_date__gte=self.start_date,
            payment_date__lte=self.end_date,
            is_reversed=False,
        ).select_related('invoice')

        method_agg = defaultdict(lambda: {'label': '', 'count': 0, 'total': Decimal('0')})
        for p in payments:
            key = p.payment_method
            method_agg[key]['label'] = p.get_payment_method_display()
            method_agg[key]['count'] += 1
            method_agg[key]['total'] += p.amount or Decimal('0')

        rows = []
        grand_total = Decimal('0')
        for key, v in sorted(method_agg.items()):
            grand_total += v['total']
            rows.append({
                'method_key': key,
                'method_label': v['label'],
                'payment_count': format_number(v['count'], 0),
                'total_amount': format_number(float(v['total']), 2),
                'total_raw': float(v['total']),
            })

        invoices = SaleInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date,
        )
        total_invoiced = sum(float(inv.grand_total or 0) for inv in invoices)
        total_paid = float(grand_total)
        outstanding = total_invoiced - total_paid

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'total_invoiced': format_number(total_invoiced, 2),
                'total_paid': format_number(total_paid, 2),
                'outstanding': format_number(outstanding, 2),
                'outstanding_raw': outstanding,
                'payment_count': format_number(payments.count(), 0),
            },
            'data': rows,
        }


class IncomeStatementGenerator:
    """قائمة الدخل (الإيرادات مقابل المصروفات)"""

    def __init__(self, tenant, start_date=None, end_date=None):
        from datetime import timedelta
        self.tenant = tenant
        self.start_date = start_date or (timezone.localdate() - timedelta(days=30))
        self.end_date = end_date or timezone.localdate()

    def get_report(self):
        from decimal import Decimal
        from apps.expenses.models import Expense

        # Revenue: confirmed sale invoices
        invoices = SaleInvoice.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            invoice_date__gte=self.start_date,
            invoice_date__lte=self.end_date,
        )
        total_revenue = sum(float(inv.grand_total or 0) for inv in invoices)
        total_tax = sum(float(inv.tax_amount or 0) for inv in invoices)
        invoice_count = invoices.count()

        # Returns deducted from revenue
        returns = SaleReturn.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            return_date__gte=self.start_date,
            return_date__lte=self.end_date,
        )
        total_returns = sum(float(r.total_returned or 0) for r in returns)
        net_revenue = total_revenue - total_returns

        # Expenses: confirmed expenses
        expenses = Expense.objects.filter(
            tenant=self.tenant,
            status='confirmed',
            expense_date__gte=self.start_date,
            expense_date__lte=self.end_date,
        )
        total_expenses = float(expenses.aggregate(t=__import__('django').db.models.Sum('amount'))['t'] or 0)

        # COGS: actual cost of goods sold (cost_price_snapshot × qty from confirmed sale lines)
        from django.db.models import Sum, F, ExpressionWrapper, DecimalField
        from .models import SaleInvoiceLine
        cogs_qs = SaleInvoiceLine.objects.filter(
            tenant=self.tenant,
            invoice__status='confirmed',
            invoice__invoice_date__gte=self.start_date,
            invoice__invoice_date__lte=self.end_date,
        ).aggregate(
            total=Sum(
                ExpressionWrapper(F('quantity') * F('cost_price_snapshot'), output_field=DecimalField())
            )
        )
        total_purchases = float(cogs_qs['total'] or 0)

        gross_profit = net_revenue - total_purchases
        net_profit = gross_profit - total_expenses

        # Expense breakdown by category
        from apps.expenses.models import ExpenseCategory
        from django.db.models import Sum
        expense_by_cat = []
        for cat in ExpenseCategory.objects.filter(tenant=self.tenant).order_by('name'):
            cat_total = float(
                expenses.filter(category=cat).aggregate(t=Sum('amount'))['t'] or 0
            )
            if cat_total > 0:
                expense_by_cat.append({'name': cat.name, 'amount': format_number(cat_total, 2)})

        uncat = float(expenses.filter(category=None).aggregate(t=Sum('amount'))['t'] or 0)
        if uncat > 0:
            expense_by_cat.append({'name': 'مصروفات متنوعة', 'amount': format_number(uncat, 2)})

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'revenue': {
                'gross_revenue': format_number(total_revenue, 2),
                'total_returns': format_number(total_returns, 2),
                'net_revenue': format_number(net_revenue, 2),
                'invoice_count': format_number(invoice_count, 0),
                'total_tax': format_number(total_tax, 2),
            },
            'cost': {
                'total_purchases': format_number(total_purchases, 2),
                'gross_profit': format_number(gross_profit, 2),
                'gross_margin': format_number((gross_profit / net_revenue * 100) if net_revenue else 0, 1),
            },
            'expenses': {
                'total_expenses': format_number(total_expenses, 2),
                'breakdown': expense_by_cat,
            },
            'bottom_line': {
                'net_profit': format_number(net_profit, 2),
                'net_profit_raw': net_profit,
                'net_margin': format_number((net_profit / net_revenue * 100) if net_revenue else 0, 1),
            },
        }
