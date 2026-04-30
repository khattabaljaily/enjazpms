from django.http import HttpResponseForbidden
from django.template.loader import render_to_string

def csrf_failure(request, reason=""):
    """
    Custom CSRF failure view.
    """
    return HttpResponseForbidden(
        render_to_string('403_csrf_failure.html', {'reason': reason}, request),
        content_type='text/html'
    )