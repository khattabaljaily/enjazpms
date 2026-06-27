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
        self.start_date = start_date or (tz.localdate() - timedelta(days=30))
        self.end_date = end_date or tz.localdate()

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
        ).prefetch_related('stock_quantities', 'item_units')

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
                'item_unit': item.base_unit_name or 'وحدة',
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
        ).select_related('item', 'stock').prefetch_related('item__item_units').order_by('-id')

        if item_id:
            movements = movements.filter(item_id=item_id)
        if stock_id:
            movements = movements.filter(stock_id=stock_id)

        import re as _re
        _ref_pattern = _re.compile(r'\b([A-Z]{2,6}-\d{3,7})\b')

        # Opening quantity: balance_after of last movement before start_date
        opening_qty = None
        if item_id:
            pre_qs = StockMovement.objects.filter(
                tenant=self.tenant,
                item_id=item_id,
                movement_date__lt=self.start_date,
            )
            if stock_id:
                pre_qs = pre_qs.filter(stock_id=stock_id)
            pre_mv = pre_qs.order_by('movement_date', 'id').last()
            if pre_mv:
                opening_qty = float(pre_mv.balance_after)
            else:
                from apps.stocks.models import StockQuantity
                sq_qs = StockQuantity.objects.filter(tenant=self.tenant, item_id=item_id)
                if stock_id:
                    sq_qs = sq_qs.filter(stock_id=stock_id)
                opening_qty = float(sq_qs.aggregate(s=Sum('opening_quantity'))['s'] or 0)

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
                'item_unit': m.item.base_unit_name,
                'stock_name': m.stock.name,
                'movement_type': m.get_movement_type_display(),
                'movement_type_key': m.movement_type,
                'direction': m.direction,
                'quantity_in': format_number(qty_in, 2) if qty_in else '—',
                'quantity_out': format_number(qty_out, 2) if qty_out else '—',
                'balance_after': format_number(float(m.balance_after), 2),
                'unit_cost': format_number(float(m.unit_cost), 2),
                'notes': m.notes,
                'source_ref': (_ref_pattern.search(m.notes or '').group(1) if _ref_pattern.search(m.notes or '') else ''),
                'is_reversal': m.is_reversal,
            })

        # Build item/stock filter options
        items = Item.objects.filter(tenant=self.tenant).order_by('name')
        stocks = Stock.objects.filter(tenant=self.tenant).order_by('name')

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'opening_qty': format_number(opening_qty, 2) if opening_qty is not None else None,
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
        ).select_related('item', 'stock').prefetch_related('item__item_units').order_by('item__name', 'stock__name')

        data = []
        for sq in quantities:
            if sq.is_low_stock:
                data.append({
                    'item_name': sq.item.name,
                    'item_unit': sq.item.base_unit_name,
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

    def get_valuation_report(self):
        """تقرير تقييم المخزون — قيمة كل صنف بسعر التكلفة، مجمّعة حسب الفئة"""
        from apps.items.models import Category

        quantities = StockQuantity.objects.filter(
            tenant=self.tenant,
            quantity__gt=0,
        ).select_related('item', 'item__category', 'stock').prefetch_related('item__item_units')

        category_data = {}
        grand_total_qty = Decimal('0')
        grand_total_value = Decimal('0')
        item_agg = {}

        for sq in quantities:
            iid = sq.item_id
            if iid not in item_agg:
                item_agg[iid] = {
                    'item_name': sq.item.name,
                    'item_unit': sq.item.base_unit_name,
                    'category_name': sq.item.category.name if sq.item.category else 'غير مصنف',
                    'category_id': sq.item.category_id,
                    'cost_price': float(sq.item.cost_price or 0),
                    'total_qty': Decimal('0'),
                }
            item_agg[iid]['total_qty'] += sq.quantity or Decimal('0')

        rows = []
        for iid, v in item_agg.items():
            value = float(v['total_qty']) * v['cost_price']
            grand_total_qty += v['total_qty']
            grand_total_value += Decimal(str(value))
            rows.append({
                'item_name': v['item_name'],
                'item_unit': v['item_unit'],
                'category_name': v['category_name'],
                'category_id': v['category_id'],
                'cost_price': format_number(v['cost_price'], 2),
                'total_qty': format_number(float(v['total_qty']), 2),
                'total_value': format_number(value, 2),
                'total_value_raw': value,
            })

        rows.sort(key=lambda x: (x['category_name'], -x['total_value_raw']))

        by_category = {}
        for row in rows:
            cat = row['category_name']
            if cat not in by_category:
                by_category[cat] = {'items': [], 'subtotal': 0.0}
            by_category[cat]['items'].append(row)
            by_category[cat]['subtotal'] += row['total_value_raw']

        category_list = [
            {
                'name': cat,
                'items': v['items'],
                'subtotal': format_number(v['subtotal'], 2),
                'subtotal_raw': v['subtotal'],
            }
            for cat, v in sorted(by_category.items(), key=lambda x: -x[1]['subtotal'])
        ]

        return {
            'summary': {
                'item_count': format_number(len(item_agg), 0),
                'total_qty': format_number(float(grand_total_qty), 2),
                'grand_total': format_number(float(grand_total_value), 2),
                'category_count': format_number(len(by_category), 0),
            },
            'categories': category_list,
            'all_rows': rows,
        }

    def get_non_moving_report(self):
        """تقرير الأصناف الراكدة — لها مخزون لكن لا حركة خروج في الفترة"""
        from django.db.models import Max

        quantities = StockQuantity.objects.filter(
            tenant=self.tenant,
            quantity__gt=0,
        ).select_related('item', 'item__category', 'stock').prefetch_related('item__item_units')

        moving_item_ids = set(
            StockMovement.objects.filter(
                tenant=self.tenant,
                direction='out',
                movement_date__gte=self.start_date,
                movement_date__lte=self.end_date,
            ).values_list('item_id', flat=True)
        )

        item_agg = {}
        for sq in quantities:
            if sq.item_id in moving_item_ids:
                continue
            iid = sq.item_id
            if iid not in item_agg:
                item_agg[iid] = {
                    'item_name': sq.item.name,
                    'item_unit': sq.item.base_unit_name,
                    'category_name': sq.item.category.name if sq.item.category else 'غير مصنف',
                    'cost_price': float(sq.item.cost_price or 0),
                    'total_qty': Decimal('0'),
                }
            item_agg[iid]['total_qty'] += sq.quantity or Decimal('0')

        last_movement_qs = (
            StockMovement.objects.filter(tenant=self.tenant)
            .values('item_id')
            .annotate(last_date=Max('movement_date'))
        )
        last_movement = {m['item_id']: m['last_date'] for m in last_movement_qs}

        today = tz.localdate()
        data = []
        total_value = 0.0
        for iid, v in item_agg.items():
            qty = float(v['total_qty'])
            value = qty * v['cost_price']
            total_value += value
            last_date = last_movement.get(iid)
            days_idle = (today - last_date).days if last_date else None
            data.append({
                'item_name': v['item_name'],
                'item_unit': v['item_unit'],
                'category_name': v['category_name'],
                'total_qty': format_number(qty, 2),
                'cost_price': format_number(v['cost_price'], 2),
                'total_value': format_number(value, 2),
                'total_value_raw': value,
                'last_movement_date': last_date,
                'days_idle': days_idle,
            })

        data.sort(key=lambda x: -(x['days_idle'] or 99999))

        return {
            'period': {'start': self.start_date, 'end': self.end_date},
            'summary': {
                'non_moving_count': format_number(len(data), 0),
                'total_value': format_number(total_value, 2),
            },
            'data': data,
        }
