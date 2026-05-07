"""
Custom template tags للتحقق من الصلاحيات
"""

from django import template

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
