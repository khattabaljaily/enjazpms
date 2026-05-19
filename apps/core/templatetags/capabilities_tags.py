from django import template

register = template.Library()


@register.filter(name='has_capability')
def has_capability(capabilities, name):
    """
    Usage: {% if tenant_capabilities|has_capability:"has_expiry_dates" %}
    Returns False safely if capabilities is None or field doesn't exist.
    """
    if capabilities is None:
        return False
    return bool(getattr(capabilities, name, False))
