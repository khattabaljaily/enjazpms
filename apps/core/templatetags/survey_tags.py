from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """قيمة المفتاح من قاموس (فارغة إن لم يوجد)."""
    if isinstance(mapping, dict):
        return mapping.get(key, '')
    return ''


@register.filter
def has_choice(values, value):
    """هل القيمة ضمن قائمة الاختيارات المحددة؟"""
    return isinstance(values, (list, tuple)) and value in values
