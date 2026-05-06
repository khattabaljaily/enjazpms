"""
Permission decorators for protecting views based on permission keys.
استخدم decorators هذه على views لفحص الصلاحيات من مفاتيح JSON
"""
from functools import wraps
from django.shortcuts import redirect
from .models import User
from .permissions import access_allowed


def require_permission(permission_key):
    """
    Decorator to check if user has specific permission.
    
    Usage:
        @require_permission('view_customers')
        def customer_list(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            
            # Superuser and tenant admins have all permissions
            if request.user.is_superuser or request.user.is_tenant_admin:
                return view_func(request, *args, **kwargs)
            
            # Check if user has the permission via their groups
            if request.user.has_perm_key(permission_key):
                return view_func(request, *args, **kwargs)
            
            # Permission denied
            return redirect('core:no_permission')
        
        return wrapper
    return decorator


def require_any_permission(*permission_keys):
    """
    Decorator to check if user has ANY of the specified permissions.
    
    Usage:
        @require_any_permission('add_items', 'change_items', 'delete_items')
        def item_manage(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            
            # Superuser and tenant admins have all permissions
            if request.user.is_superuser or request.user.is_tenant_admin:
                return view_func(request, *args, **kwargs)
            
            # Check if user has ANY of the permissions
            for perm_key in permission_keys:
                if request.user.has_perm_key(perm_key):
                    return view_func(request, *args, **kwargs)
            
            # Permission denied
            return redirect('core:no_permission')
        
        return wrapper
    return decorator


def require_all_permissions(*permission_keys):
    """
    Decorator to check if user has ALL of the specified permissions.
    
    Usage:
        @require_all_permissions('view_items', 'add_items')
        def item_advanced_manage(request):
            ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('accounts:login')
            
            # Superuser and tenant admins have all permissions
            if request.user.is_superuser or request.user.is_tenant_admin:
                return view_func(request, *args, **kwargs)
            
            # Check if user has ALL of the permissions
            for perm_key in permission_keys:
                if not request.user.has_perm_key(perm_key):
                    return redirect('core:no_permission')
            
            return view_func(request, *args, **kwargs)
        
        return wrapper
    return decorator
