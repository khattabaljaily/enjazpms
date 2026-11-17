from .services import red_unread_count, unread_count


def circulars_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not getattr(request, 'tenant', None):
        return {'circulars_unread': 0, 'circulars_red_unread': 0}
    try:
        return {'circulars_unread': unread_count(request), 'circulars_red_unread': red_unread_count(request)}
    except Exception:
        return {'circulars_unread': 0, 'circulars_red_unread': 0}
