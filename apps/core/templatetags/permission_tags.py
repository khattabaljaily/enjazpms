"""
Custom template tags للتحقق من الصلاحيات
"""

import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def has_perm_key(user, permission_key):
    """
    فلتر Django: التحقق من وجود صلاحية محددة لدى المستخدم

    الاستخدام في الـ Template:
        {% if user|has_perm_key:'view_quotes' %}
            <a href="/quotes/">الاقتباسات</a>
        {% endif %}
    """
    if not user or not hasattr(user, 'has_perm_key'):
        return False
    return user.has_perm_key(permission_key)


@register.simple_tag(takes_context=True)
def user_perm_keys_json(context):
    """Return JSON array of all permission keys for the current user (for JS injection)."""
    request = context.get('request')
    if not request or not hasattr(request, 'user') or not request.user.is_authenticated:
        return mark_safe('[]')
    user = request.user
    if not hasattr(user, 'get_permission_keys'):
        return mark_safe('[]')
    return mark_safe(json.dumps(list(user.get_permission_keys())))
