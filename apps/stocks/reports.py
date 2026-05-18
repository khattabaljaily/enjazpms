"""
تقارير المخزن
================

يحتوي على الدوال المساعدة لإنشاء تقارير مختلفة:
  - ملخص المخزن (إجمالي الكميات والقيمة)
  - المخزن حسب المنتج
  - المخزن حسب الفئة
  - المخزن حسب الموقع (المخزن)
"""

from decimal import Decimal
from django.db.models import Sum, Count, F, Q
from django.utils import timezone

from datetime import timedelta
from django.utils import timezone as tz

from .models import StockQuantity, Stock
from apps.items.models import Item
from apps.sales.models import StockMovement


def format_number(value, decimals=2):
    try:
        if decimals == 0:
            return f"{int(value):,}"
        return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


class StocksReportGenerator:
    """فئة شاملة لإنشاء تقارير المخزن"""

    def __init__(self, tenant, start_date=None, end_date=None):
        self.tenant = tenant
        self.start_date = start_date or (tz.now().date() - timedelta(days=30))
        self.end_date = end_date or tz.now().date()

    def get_summary_report(self):
        """تقرير ملخص المخزن - إجمالي الكميات والقيمة"""
        stock_quantities = StockQuantity.objects.filter(
            tenant=self.tenant
        ).select_related('item', 'stock')

        total_quantity = Decimal('0')
        total_reserved = Decimal('0')
        total_available = Decimal('0')
        low_stock_count = 0
        total_value = Decimal('0')
        unique_items = set()

        for sq in stock_quantities:
            unique_items.add(sq.item_id)
            total_quantity += sq.quantity or 0
            total_reserved += sq.reserved_quantity or 0
            total_available += sq.available_quantity or 0
            if sq.is_low_stock:
                low_stock_count += 1
            # حساب القيمة بناءً على سعر التكلفة
            if sq.item.cost_price:
                total_value += (sq.quantity or 0) * sq.item.cost_price

        return {
            'summary': {
                'total_quantity': float(total_quantity),
                'total_reserved': float(total_reserved),
                'total_available': float(total_available),
                'unique_items': len(unique_items),
                'low_stock_items': low_stock_count,
                'total_value': float(total_value),
            },
            'data': []
        }

    def get_by_item_report(self):
        """تقرير المخزن حسب المنتج"""
        items = Item.objects.filter(
            tenant=self.tenant
        ).prefetch_related('stock_quantities')

        data = []
        for item in items:
            stock_qty_list = item.stock_quantities.all()
            
            total_qty = Decimal('0')
            total_reserved = Decimal('0')
            total_available = Decimal('0')
            low_stock = False

            for sq in stock_qty_list:
                total_qty += sq.quantity or 0
                total_reserved += sq.reserved_quantity or 0
                total_available += sq.available_quantity or 0
                if sq.is_low_stock:
                    low_stock = True

            data.append({
                'item_id': item.id,
                'item_name': item.name,
                'item_unit': item.unit or 'وحدة',
                'total_quantity': float(total_qty),
                'total_reserved': float(total_reserved),
                'total_available': float(total_available),
                'low_stock': low_stock,
                'cost_price': float(item.cost_price or 0),
                'total_value': float((total_qty or 0) * (item.cost_price or 0)),
                'storage_locations': len(stock_qty_list)
            })

        return {
            'data': sorted(data, key=lambda x: x['total_quantity'], reverse=True)
        }

    def get_by_category_report(self):
        """تقرير المخزن حسب الفئة"""
        from apps.items.models import Category

        categories = Category.objects.filter(
            tenant=self.tenant
        ).prefetch_related('items')

        data = []
        for category in categories:
            items_in_category = category.items.all()
            
            total_qty = Decimal('0')
            total_reserved = Decimal('0')
            total_available = Decimal('0')
            total_value = Decimal('0')
            item_count = 0
            low_stock_count = 0

            for item in items_in_category:
                item_count += 1
                stock_quantities = StockQuantity.objects.filter(
                    tenant=self.tenant,
                    item=item
                )
                
                for sq in stock_quantities:
                    total_qty += sq.quantity or 0
                    total_reserved += sq.reserved_quantity or 0
                    total_available += sq.available_quantity or 0
                    if sq.is_low_stock:
                        low_stock_count += 1
                    total_value += (sq.quantity or 0) * (item.cost_price or 0)

            if item_count > 0:
                data.append({
                    'category_id': category.id,
                    'category_name': category.name,
                    'item_count': item_count,
                    'total_quantity': float(total_qty),
                    'total_reserved': float(total_reserved),
                    'total_available': float(total_available),
                    'low_stock_items': low_stock_count,
                    'total_value': float(total_value),
                })

        return {
            'data': sorted(data, key=lambda x: x['total_quantity'], reverse=True)
        }

    def get_by_stock_report(self, stock_id=None):
        """تقرير المخزن حسب الموقع (المخزن)
        إذا تم تحديد `stock_id`، نُرجع فقط بيانات ذلك المخزن (كم صف واحد في القائمة).
        """
        stocks_qs = Stock.objects.filter(
            tenant=self.tenant,
            is_active=True
        )

        if stock_id:
            stocks_qs = stocks_qs.filter(id=stock_id)

        stocks = stocks_qs.prefetch_related('quantities')

        data = []
        for stock in stocks:
            stock_qty_list = stock.quantities.all()
            
            total_qty = Decimal('0')
            total_reserved = Decimal('0')
            total_available = Decimal('0')
            total_value = Decimal('0')
            item_count = 0
            low_stock_count = 0

            for sq in stock_qty_list:
                if sq.quantity and sq.quantity > 0:
                    item_count += 1
                    total_qty += sq.quantity
                    total_reserved += sq.reserved_quantity or 0
                    total_available += sq.available_quantity or 0
                    total_value += sq.quantity * (sq.item.cost_price or 0)
                    if sq.is_low_stock:
                        low_stock_count += 1

            data.append({
                'stock_id': stock.id,
                'stock_name': stock.name,
                'stock_type': stock.get_stock_type_display(),
                'item_count': item_count,
                'total_quantity': float(total_qty),
                'total_reserved': float(total_reserved),
                'total_available': float(total_available),
                'low_stock_items': low_stock_count,
                'total_value': float(total_value),
            })

        return {
            'data': sorted(data, key=lambda x: x['total_quantity'], reverse=True)
        }

    def get_item_movement_report(self, item_id=None, stock_id=None):
        """حركة الأصناف خلال فترة زمنية"""
        movements = StockMovement.objects.filter(
            tenant=self.tenant,
            movement_date__gte=self.start_date,
            movement_date__lte=self.end_date,
        ).select_related('item', 'stock').order_by('movement_date', 'id')

        if item_id:
            movements = movements.filter(item_id=item_id)
        if stock_id:
            movements = movements.filter(stock_id=stock_id)

        data = []
        total_in = 0
        total_out = 0
        for m in movements:
            qty_in = float(m.quantity) if m.direction == 'in' else 0
            qty_out = float(m.quantity) if m.direction == 'out' else 0
            total_in += qty_in
            total_out += qty_out
            data.append({
                'movement_date': m.movement_date,
                'item_name': m.item.name,
                'stock_name': m.stock.name,
                'movement_type': m.get_movement_type_display(),
                'movement_type_key': m.movement_type,
                'direction': m.direction,
                'quantity_in': format_number(qty_in, 2) if qty_in else '—',
                'quantity_out': format_number(qty_out, 2) if qty_out else '—',
                'balance_after': format_number(float(m.balance_after), 2),
                'unit_cost': format_number(float(m.unit_cost), 2),
                'notes': m.notes,
            })

        # Build item/stock filter options
        items = Item.objects.filter(tenant=self.tenant).order_by('name')
        stocks = Stock.objects.filter(tenant=self.tenant).order_by('name')

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'movement_count': format_number(len(data), 0),
                'total_in': format_number(total_in, 2),
                'total_out': format_number(total_out, 2),
                'net': format_number(total_in - total_out, 2),
            },
            'data': data,
            'items': items,
            'stocks': stocks,
        }

    def get_low_stock_report(self):
        """تنبيهات المخزون المنخفض — المنتجات التي وصلت أو تجاوزت حد الحد الأدنى"""
        quantities = StockQuantity.objects.filter(
            tenant=self.tenant,
            min_quantity__gt=0,
        ).select_related('item', 'stock', 'item__unit').order_by('item__name', 'stock__name')

        data = []
        for sq in quantities:
            if sq.is_low_stock:
                data.append({
                    'item_name': sq.item.name,
                    'item_unit': sq.item.unit.name if sq.item.unit else '',
                    'stock_name': sq.stock.name,
                    'quantity': format_number(float(sq.quantity), 2),
                    'available': format_number(float(sq.available_quantity), 2),
                    'reserved': format_number(float(sq.reserved_quantity), 2),
                    'min_quantity': format_number(float(sq.min_quantity), 2),
                    'shortage': format_number(max(0, float(sq.min_quantity) - float(sq.available_quantity)), 2),
                })

        return {
            'summary': {
                'low_stock_count': format_number(len(data), 0),
            },
            'data': data,
        }
