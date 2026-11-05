"""
Activity logging service — thin wrapper around UserActivity.
Never raises: logging must never break the calling view.
"""


def log_activity(request, title: str, details: str = '', action_type: str = 'other') -> None:
    """
    Create a UserActivity record and mark the request as already-logged so the
    middleware won't create a second entry for the same request.
    """
    try:
        from .models import UserActivity
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            return
        tenant = getattr(request, 'tenant', None)
        if not tenant:
            return
        # المشرف يتصفح كصاحب نشاط (وضع انتحال الهوية): لا يُسجَّل له أي نشاط
        session = getattr(request, 'session', None)
        if getattr(request, '_impersonating', False) or (session is not None and session.get('_impersonator_id')):
            request._activity_logged = True
            return
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        ip = forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR', '') or None
        UserActivity.objects.create(
            tenant=tenant,
            user=request.user,
            branch=getattr(request, 'branch', None),
            title=title,
            action_type=action_type,
            details=details,
            ip_address=ip or None,
        )
        request._activity_logged = True
    except Exception:
        pass
