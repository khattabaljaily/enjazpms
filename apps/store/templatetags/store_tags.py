from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    if not dictionary:
        from apps.store.models import DEFAULT_HOURS
        return DEFAULT_HOURS.get(key, {'enabled': False, 'open': '08:00', 'close': '22:00'})
    return dictionary.get(key, {'enabled': False, 'open': '08:00', 'close': '22:00'})


@register.filter
def get_qty(qty_map, item_id):
    if not qty_map:
        return None
    return qty_map.get(item_id)


@register.filter
def format_price(value):
    """Format number as 1,000.00"""
    try:
        return f"{float(value):,.2f}"
    except (ValueError, TypeError):
        return value
