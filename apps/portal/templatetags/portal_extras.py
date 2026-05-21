from django import template

register = template.Library()


@register.filter
def money_fmt(value):
    try:
        return '{:,.2f}'.format(float(value))
    except (TypeError, ValueError):
        return '0.00'
