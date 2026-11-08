from django import template
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def brand_font_url():
    """رابط خط المستندات (Al Mohanad)، أو '' إن لم يكن في قائمة الملفات المجمّعة: الصفحة العامة لا تسقط بسبب خط."""
    try:
        return static('brand/almohanad-regular.ttf')
    except ValueError:
        return ''
