from decimal import Decimal, InvalidOperation

from django import template
from apps.core.constants import CURRENCY_AR

register = template.Library()


@register.filter(name='currency_ar')
def currency_ar(code):
    """Convert currency code to Arabic symbol. SDG → ج.س"""
    return CURRENCY_AR.get(str(code).strip().upper(), code)


@register.filter(name='qty')
def qty(value, max_decimals=3):
    """Format quantities without thousands separators and trim trailing zeros.

    Examples: 3 -> "3", 3.5 -> "3.5", 3.000 -> "3", 3.1254 -> "3.125"
    """
    if value is None or value == '':
        return '0'

    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    try:
        decimals = int(max_decimals)
    except (TypeError, ValueError):
        decimals = 3

    if decimals < 0:
        decimals = 0

    q = Decimal('1') if decimals == 0 else Decimal('1').scaleb(-decimals)
    d = d.quantize(q)
    out = format(d, 'f')
    if '.' in out:
        out = out.rstrip('0').rstrip('.')
    return out


@register.filter(name='money')
def money(value):
    """Format monetary amounts with thousands separator and 2 decimals.

    Example: 5800 -> "5,800.00"
    """
    if value is None or value == '':
        return '0.00'

    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    return format(d, ',.2f')
