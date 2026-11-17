from .services import unread_count


def circulars_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not getattr(request, 'tenant', None):
        return {'circulars_unread': 0}
    try:
        return {'circulars_unread': unread_count(request)}
    except Exception:
        return {'circulars_unread': 0}
