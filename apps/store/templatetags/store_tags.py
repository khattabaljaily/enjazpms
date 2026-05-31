from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """{{ some_dict|get_item:key }} — returns hours dict for a day key."""
    if not dictionary:
        from apps.store.models import DEFAULT_HOURS
        return DEFAULT_HOURS.get(key, {'enabled': False, 'open': '08:00', 'close': '22:00'})
    return dictionary.get(key, {'enabled': False, 'open': '08:00', 'close': '22:00'})


@register.filter
def get_qty(qty_map, item_id):
    """{{ stock_qty_map|get_qty:item.id }} — returns total stock qty for an item."""
    if not qty_map:
        return None
    return qty_map.get(item_id)
