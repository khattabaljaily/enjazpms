"""
Views للحسابات - التسجيل وتسجيل الدخول
"""
import json

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import PasswordResetConfirmView
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from .decorators import require_permission
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta
from django.utils import timezone as _tz

from apps.core.models import Tenant, Settings
from .models import UserActivity
from .activity_service import log_activity
from apps.core.constants import COUNTRY_TIMEZONE_MAP, COUNTRY_CURRENCY_MAP, TIMEZONE_CURRENCY_MAP, CURRENCY_AR, DEFAULT_COUNTRY, get_timezone_for_country
from .models import PermissionGroup, User
from .forms import Step1UserForm, Step2BusinessForm, Step3SettingsForm, RegistrationRequestForm, LoginForm, UserManagementForm, PasswordResetForm, SetPasswordForm
from .permissions import get_permission_keys, get_permission_schema


def _wants_json(request):
    return (
        getattr(request, 'is_api', False)
        or request.headers.get('x-requested-with') == 'XMLHttpRequest'
    )


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


def _first_error_message(errors_dict, default='يرجى التحقق من الحقول المطلوبة'):
    if '__all__' in errors_dict and errors_dict['__all__']:
        return errors_dict['__all__'][0]

    for _, messages_list in errors_dict.items():
        if messages_list:
            return messages_list[0]
    return default


def _ensure_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return None
    return tenant


def _json_error(message, status=400):
    return JsonResponse({'success': False, 'message': message}, status=status, json_dumps_params={'ensure_ascii': False})


def _clear_messages(request):
    """Clear any pending Django messages from the session."""
    storage = get_messages(request)
    for _ in storage:
        pass


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data is not None:
        payload['data'] = data
    return JsonResponse(payload, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_users')
def user_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = User.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    inactive = total - active

    groups = PermissionGroup.objects.filter(
        tenant=tenant,
        is_active=True
    ).values('id', 'name')

    context = {
        'stats': {
            'total': total,
            'active': active,
            'inactive': inactive,
        },
        'form': UserManagementForm(tenant=tenant),
        'permission_groups': json.dumps(list(groups), ensure_ascii=False),
    }
    return render(request, 'accounts/user_list.html', context)


@login_required
@require_permission('view_users')
def user_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400, json_dumps_params={'ensure_ascii': False})

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    queryset = User.objects.for_tenant(tenant)
    records_total = queryset.count()

    if status == 'active':
        queryset = queryset.filter(is_active=True)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False)

    if search_value:
        queryset = queryset.filter(
            Q(username__icontains=search_value)
            | Q(email__icontains=search_value)
            | Q(first_name__icontains=search_value)
            | Q(last_name__icontains=search_value)
        )

    records_filtered = queryset.count()

    order_column_index = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    order_column_name = request.GET.get(f'columns[{order_column_index}][data]', 'date_joined')

    allowed_order_fields = {
        'username': 'username',
        'full_name': 'first_name',
        'email': 'email',
        'is_active': 'is_active',
        'date_joined': 'date_joined',
    }
    order_field = allowed_order_fields.get(order_column_name, 'date_joined')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    queryset = queryset.order_by(order_field)[start:start + length]

    data = [
        {
            'id': user.id,
            'username': user.username,
            'full_name': user.get_full_name(),
            'email': user.email or '-',
            'is_active': user.is_active,
            'date_joined': user.date_joined.strftime('%Y-%m-%d'),
        }
        for user in queryset
    ]

    return JsonResponse({
        'draw': draw,
        'recordsTotal': records_total,
        'recordsFiltered': records_filtered,
        'data': data,
        'perms': {
            'edit': request.user.has_perm_key('change_users'),
            'delete': request.user.has_perm_key('delete_users'),
        },
    }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('add_users')
def user_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'الطريقة غير مسموحة'}, status=405, json_dumps_params={'ensure_ascii': False})
    
    current_count = User.objects.filter(tenant=tenant).count()
    if current_count >= tenant.max_users:
        return JsonResponse(
            {
                'success': False,
                'message': (
                    f'وصلت إلى الحد الأقصى للمستخدمين ({tenant.max_users}). '
                    'يرجى التواصل مع الدعم لترقية الاشتراك.'
                ),
            },
            status=403,
            json_dumps_params={'ensure_ascii': False}
        )

    form = UserManagementForm(request.POST, tenant=tenant)
    if not form.is_valid():
        return JsonResponse({
            'success': False,
            'message': 'يرجى التحقق من الحقول المطلوبة',
            'errors': _serialize_form_errors(form),
        }, status=400, json_dumps_params={'ensure_ascii': False})

    user = form.save()
    log_activity(request, 'إضافة مستخدم جديد',
                 f"المستخدم: {user.get_full_name()}\nاسم الدخول: {user.username}", 'create')

    return _json_ok({'id': user.id}, 'تم إضافة المستخدم بنجاح')


@login_required
@require_permission('view_users')
def user_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    user = get_object_or_404(User.objects.for_tenant(tenant), pk=pk)
    
    active_groups = [group.id for group in user.permission_groups.filter(is_active=True)]
    
    return _json_ok({
        'id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'phone': getattr(user, 'phone', ''),
        'is_tenant_admin': getattr(user, 'is_tenant_admin', False),
        'is_active': user.is_active,
        'permission_groups': active_groups,
        'is_agent_user': hasattr(user, 'agent_profile'),
    })


@login_required
@require_permission('change_users')
def user_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'الطريقة غير مسموحة'}, status=405, json_dumps_params={'ensure_ascii': False})

    user = get_object_or_404(User.objects.for_tenant(tenant), pk=pk)
    
    form = UserManagementForm(request.POST, instance=user, tenant=tenant)
    if not form.is_valid():
        return JsonResponse({
            'success': False,
            'message': 'يرجى التحقق من الحقول المطلوبة',
            'errors': _serialize_form_errors(form),
        }, status=400, json_dumps_params={'ensure_ascii': False})
    
    user_obj = form.save()
    
    return _json_ok(None, 'تم تحديث بيانات المستخدم بنجاح')


@login_required
@require_permission('delete_users')
def user_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'الطريقة غير مسموحة'}, status=405, json_dumps_params={'ensure_ascii': False})

    if request.user.pk == pk:
        return _json_error('لا يمكن حذف المستخدم الحالي')

    user = get_object_or_404(User.objects.for_tenant(tenant), pk=pk)
    if hasattr(user, 'agent_profile'):
        return _json_error('لا يمكن حذف هذا المستخدم لأنه مرتبط بمندوب. احذف المندوب أولاً أو افصل الحساب منه.')
    user.delete()
    return _json_ok(None, 'تم حذف المستخدم بنجاح')


@login_required
@require_permission('view_permissiongroups')
def permission_group_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    # تمرير المستخدمين والمجموعات إلى الـ template
    users = User.objects.for_tenant(tenant).filter(is_active=True).values('id', 'username', 'first_name', 'last_name')
    
    schema = get_permission_schema()

    # Remove permissions for features the tenant doesn't have enabled
    if not getattr(tenant, 'hard_currency_mode', False):
        hc_keys = {'transfer_treasuries'}
        schema = {
            section: {k: v for k, v in perms.items() if k not in hc_keys}
            for section, perms in schema.items()
        }

    return render(request, 'accounts/permission_group_list.html', {
        'permission_schema': json.dumps(schema, ensure_ascii=False),
        'users': json.dumps(list(users), ensure_ascii=False),
    })


@login_required
@require_permission('view_permissiongroups')
def permission_group_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()

    queryset = PermissionGroup.objects.filter(tenant=tenant)
    total = queryset.count()

    if search_value:
        queryset = queryset.filter(name__icontains=search_value)

    filtered = queryset.count()
    groups = queryset.order_by('name')[start:start + length]

    data = [
        {
            'id': group.id,
            'name': group.name,
            'description': group.description or '—',
            'member_count': group.users.count(),
            'permission_count': len(group.get_permission_keys()),
            'is_active': group.is_active,
        }
        for group in groups
    ]

    return JsonResponse({
        'draw': draw,
        'recordsTotal': total,
        'recordsFiltered': filtered,
        'data': data,
    }, json_dumps_params={'ensure_ascii': False})


@login_required
@require_permission('view_permissiongroups')
def permission_group_schema_api(request):
    return _json_ok(get_permission_schema())


@login_required
@require_permission('view_permissiongroups')
def permission_group_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    group = get_object_or_404(PermissionGroup.objects.filter(tenant=tenant), pk=pk)
    return _json_ok({
        'id': group.id,
        'name': group.name,
        'description': group.description,
        'is_active': group.is_active,
        'permissions': group.permissions,
        'users': [user.id for user in group.users.filter(is_active=True)],
    })


@login_required
@require_permission('add_permissiongroups')
def permission_group_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('الطريقة غير مسموحة', status=405)

    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    permissions_json = request.POST.get('permissions', '{}')
    user_ids = request.POST.getlist('users[]')

    if not name:
        return _json_error('يرجى إدخال اسم المجموعة')

    try:
        permissions = json.loads(permissions_json)
    except ValueError:
        permissions = {}

    valid_keys = set(get_permission_keys())
    # Build complete permissions dict with all valid keys (default to False)
    sanitized_permissions = {
        key: bool(permissions.get(key, False))
        for key in valid_keys
    }

    group = PermissionGroup.objects.create(
        tenant=tenant,
        name=name,
        description=description,
        permissions=sanitized_permissions,
        is_active=request.POST.get('is_active') == 'on',
    )

    if user_ids:
        group.users.set(User.objects.filter(tenant=tenant, id__in=user_ids))

    enabled_count = sum(1 for p in sanitized_permissions.values() if p)
    log_activity(request, 'إنشاء مجموعة صلاحيات جديدة',
                 f"المجموعة: {group.name}\nعدد الصلاحيات المفعّلة: {enabled_count}", 'create')
    return _json_ok({
        'id': group.id,
        'saved_permissions': sanitized_permissions,
        'permission_count': enabled_count,
        'total_permissions': len(valid_keys),
    }, 'تم إنشاء المجموعة بنجاح')


@login_required
@require_permission('change_permissiongroups')
def permission_group_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('الطريقة غير مسموحة', status=405)

    group = get_object_or_404(PermissionGroup.objects.filter(tenant=tenant), pk=pk)
    name = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    permissions_json = request.POST.get('permissions', '{}')
    user_ids = request.POST.getlist('users[]')

    if not name:
        return _json_error('يرجى إدخال اسم المجموعة')

    try:
        permissions = json.loads(permissions_json)
    except ValueError:
        permissions = {}

    valid_keys = set(get_permission_keys())
    # Build complete permissions dict with all valid keys (default to False)
    sanitized_permissions = {
        key: bool(permissions.get(key, False))
        for key in valid_keys
    }

    group.name = name
    group.description = description
    group.permissions = sanitized_permissions
    group.is_active = request.POST.get('is_active') == 'on'
    group.save()

    # فقط تحديث المستخدمين إذا تم إرسال مستخدمين (قائمة غير فارغة)
    if user_ids:
        group.users.set(User.objects.filter(tenant=tenant, id__in=user_ids))

    enabled_count = sum(1 for p in sanitized_permissions.values() if p)
    return _json_ok({
        'id': group.id,
        'saved_permissions': sanitized_permissions,
        'permission_count': enabled_count,
        'total_permissions': len(valid_keys),
    }, 'تم تحديث المجموعة بنجاح')


@login_required
@require_permission('delete_permissiongroups')
def permission_group_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return _json_error('الطريقة غير مسموحة', status=405)

    group = get_object_or_404(PermissionGroup.objects.filter(tenant=tenant), pk=pk)
    group.delete()
    return _json_ok(None, 'تم حذف المجموعة بنجاح')




REGISTRATION_WHATSAPP_NUMBER = '249110100110'
REGISTRATION_CONTACT_EMAIL = 'info@enjaztechnology.com'
REGISTRATION_REQUEST_RECIPIENT = 'khattabaljaily@gmail.com'


def registration_closed(request):
    """صفحة إيقاف التسجيل الذاتي المؤقت - نموذج طلب تواصل بديل"""
    _clear_messages(request)
    form = RegistrationRequestForm()
    return render(request, 'accounts/registration_closed.html', {
        'form': form,
        'whatsapp_number': REGISTRATION_WHATSAPP_NUMBER,
        'contact_email': REGISTRATION_CONTACT_EMAIL,
    })


def registration_request_api(request):
    """استقبال طلب التواصل لإنشاء حساب أثناء إيقاف التسجيل الذاتي"""
    if request.method != 'POST':
        return _json_error('الطريقة غير مسموحة', status=405)

    form = RegistrationRequestForm(request.POST)
    if not form.is_valid():
        errors = _serialize_form_errors(form)
        return JsonResponse({
            'success': False,
            'message': _first_error_message(errors),
            'errors': errors,
        }, status=400, json_dumps_params={'ensure_ascii': False})

    data = form.cleaned_data
    version_label = dict(RegistrationRequestForm.VERSION_CHOICES).get(data['version_type'], data['version_type'])

    email_context = {
        'personal_email': data['personal_email'],
        'business_type': data['business_type'].name_ar,
        'business_name': data['business_name'],
        'phone': data['phone'],
        'address': data['address'],
        'version_type': version_label,
        'hard_currency_mode': 'مفعّل' if data['hard_currency_mode'] else 'غير مفعّل',
        'now': _tz.now(),
    }

    subject = f"طلب إنشاء حساب جديد - {data['business_name']}"
    html_message = render_to_string('accounts/email/registration_request_email.html', email_context)

    try:
        send_mail(
            subject=subject,
            message='',
            html_message=html_message,
            from_email=None,
            recipient_list=[REGISTRATION_REQUEST_RECIPIENT],
            fail_silently=False,
        )
    except Exception:
        pass

    return _json_ok(None, 'تم استلام طلبك بنجاح')


def register_step1(request):
    """الخطوة 1: معلومات المستخدم"""
    from apps.core.models import PlatformSettings
    if not PlatformSettings.get().self_registration_enabled:
        return registration_closed(request)

    _clear_messages(request)
    if request.method == 'POST':
        form = Step1UserForm(request.POST)
        if form.is_valid():
            request.session['reg_step1'] = {
                'username': form.cleaned_data['username'],
                'email': form.cleaned_data['email'],
                'password': form.cleaned_data['password'],
            }

            if _wants_json(request):
                return JsonResponse({
                    'success': True,
                    'message': 'تم حفظ بيانات المستخدم بنجاح',
                    'redirect_url': reverse('accounts:register_step2'),
                }, json_dumps_params={'ensure_ascii': False})

            return redirect('accounts:register_step2')
        if _wants_json(request):
            errors = _serialize_form_errors(form)
            return JsonResponse({
                'success': False,
                'message': _first_error_message(errors),
                'errors': errors,
            }, status=400, json_dumps_params={'ensure_ascii': False})
    else:
        initial = request.session.get('reg_step1', {})
        form = Step1UserForm(initial=initial)

    return render(request, 'accounts/register_step1.html', {
        'form': form,
        'step': 1,
        'total_steps': 3,
    })


@login_required
def profile_view(request):
    """الملف الشخصي"""
    return render(request, 'accounts/profile.html', {
        'user': request.user,
    })


def register_step2(request):
    """الخطوة 2: معلومات النشاط التجاري"""
    _clear_messages(request)
    
    # التحقق من إتمام الخطوة 1
    if 'reg_step1' not in request.session:
        if _wants_json(request):
            return JsonResponse({
                'success': False,
                'message': 'يرجى إكمال الخطوة الأولى أولاً',
                'redirect_url': reverse('accounts:register_step1'),
            }, status=400, json_dumps_params={'ensure_ascii': False})
        return redirect('accounts:register_step1')
    
    country_timezone_map_json = json.dumps(COUNTRY_TIMEZONE_MAP, ensure_ascii=False)
    country_currency_map_json = json.dumps(COUNTRY_CURRENCY_MAP, ensure_ascii=False)
    timezone_preview = get_timezone_for_country(DEFAULT_COUNTRY)

    if request.method == 'POST':
        form = Step2BusinessForm(request.POST)
        if form.is_valid():
            country_value = form.cleaned_data['country']
            timezone_value = get_timezone_for_country(country_value)
            # حفظ البيانات في Session
            request.session['reg_step2'] = {
                'business_type_id': form.cleaned_data['business_type'].id,
                'business_name': form.cleaned_data['business_name'],
                'phone': form.cleaned_data['phone'],
                'address': form.cleaned_data['address'],
                'city': form.cleaned_data['city'],
                'country': country_value,
                'timezone': timezone_value,
            }

            if _wants_json(request):
                return JsonResponse({
                    'success': True,
                    'message': 'تم حفظ بيانات النشاط التجاري بنجاح',
                    'redirect_url': reverse('accounts:register_step3'),
                }, json_dumps_params={'ensure_ascii': False})

            return redirect('accounts:register_step3')
        if _wants_json(request):
            errors = _serialize_form_errors(form)
            return JsonResponse({
                'success': False,
                'message': _first_error_message(errors),
                'errors': errors,
            }, status=400, json_dumps_params={'ensure_ascii': False})
        timezone_preview = get_timezone_for_country(request.POST.get('country', DEFAULT_COUNTRY))
    else:
        initial = request.session.get('reg_step2', {})
        form = Step2BusinessForm(initial=initial)
        timezone_preview = get_timezone_for_country(initial.get('country', DEFAULT_COUNTRY))

    return render(request, 'accounts/register_step2.html', {
        'form': form,
        'step': 2,
        'total_steps': 3,
        'country_timezone_map_json': country_timezone_map_json,
        'country_currency_map_json': country_currency_map_json,
        'timezone_preview': timezone_preview,
    })


def register_step3(request):
    """الخطوة 3: إعدادات النظام وإنشاء الحساب"""
    _clear_messages(request)
    
    # التحقق من إتمام الخطوات السابقة
    if 'reg_step1' not in request.session or 'reg_step2' not in request.session:
        if _wants_json(request):
            return JsonResponse({
                'success': False,
                'message': 'يرجى إكمال خطوات التسجيل السابقة أولاً',
                'redirect_url': reverse('accounts:register_step1'),
            }, status=400, json_dumps_params={'ensure_ascii': False})
        return redirect('accounts:register_step1')
    
    if request.method == 'POST':
        form = Step3SettingsForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    # استرجاع بيانات الخطوات السابقة
                    step1_data = request.session['reg_step1']
                    step2_data = request.session['reg_step2']
                    
                    # 1. إنشاء Tenant
                    from apps.core.models import BusinessType
                    business_type = BusinessType.objects.get(id=step2_data['business_type_id'])
                    
                    subscription_plan = form.cleaned_data.get('subscription_plan', 'trial')
                    trial_duration = 30

                    hard_currency_mode = form.cleaned_data.get('hard_currency_mode', False)
                    tenant = Tenant.objects.create(
                        name=step2_data['business_name'],
                        business_type=business_type,
                        phone=step2_data['phone'],
                        address=step2_data['address'],
                        city=step2_data['city'],
                        subscription_plan=subscription_plan,
                        subscription_start=_tz.localdate(),
                        subscription_expires=_tz.localdate() + timedelta(days=trial_duration),  # شهران تجريبيان مجاناً
                        version_type=form.cleaned_data['version_type'],
                        max_stocks=form.cleaned_data['num_stocks'],
                        timezone=form.cleaned_data['timezone'],
                        currency=form.cleaned_data['currency'],
                        hard_currency_mode=hard_currency_mode,
                        hard_currency=form.cleaned_data.get('hard_currency', 'USD') if hard_currency_mode else 'USD',
                        exchange_rate=form.cleaned_data.get('exchange_rate') or 1,
                        exchange_rate_updated_at=_tz.now() if hard_currency_mode else None,
                    )
                    
                    # 2. إنشاء User
                    user = User.objects.create_user(
                        username=step1_data['username'],
                        email=step1_data['email'],
                        password=step1_data['password'],
                        tenant=tenant,
                        is_tenant_admin=True,
                    )

                    # 2.1 إنشاء مجموعة مدير النشاط الافتراضية وتعيين جميع الصلاحيات لها
                    owner_group = PermissionGroup.create_owner_group(tenant, name='مدير النشاط')
                    owner_group.users.add(user)
                    
                    # 3. إنشاء Settings
                    settings = Settings.objects.create(
                        tenant=tenant,
                        tax_enabled=form.cleaned_data['tax_enabled'],
                        tax_value=form.cleaned_data.get('tax_value', 0),
                    )
                    
                    # 4. تسجيل دخول المستخدم تلقائياً
                    login(request, user)
                    
                    # 5. مسح بيانات Session
                    request.session.pop('reg_step1', None)
                    request.session.pop('reg_step2', None)

                    # 6. Send admin notification email
                    try:
                        from django.core.mail import EmailMessage
                        from django.template.loader import render_to_string
                        from django.utils import timezone as tz
                        from django.conf import settings as django_settings
                        html_body = render_to_string('core/email/new_tenant_notification.html', {
                            'tenant': tenant,
                            'admin_full_name': user.get_full_name() or step1_data['username'],
                            'admin_username': step1_data['username'],
                            'admin_email': step1_data.get('email', ''),
                            'created_at': tz.now(),
                            'dashboard_url': request.build_absolute_uri('/tenants/'),
                        })
                        msg = EmailMessage(
                            subject=f'New Tenant Registered: {tenant.name}',
                            body=html_body,
                            from_email='EnjazIMS <{}>'.format(django_settings.EMAIL_HOST_USER),
                            to=['khattabaljaily@gmail.com'],
                        )
                        msg.content_subtype = 'html'
                        msg.send(fail_silently=False)
                    except Exception as _email_err:
                        import logging
                        logging.getLogger(__name__).error('registration email failed: %s', _email_err, exc_info=True)

                    if _wants_json(request):
                        return JsonResponse({
                            'success': True,
                            'message': f'مرحباً {user.get_full_name()}! تم إنشاء حسابك بنجاح',
                            'redirect_url': reverse('core:dashboard'),
                        })

                    messages.success(request, f'مرحباً {user.get_full_name()}! تم إنشاء حسابك بنجاح')
                    return redirect('core:dashboard')
                    
            except Exception as e:
                if _wants_json(request):
                    return JsonResponse({
                        'success': False,
                        'message': f'حدث خطأ أثناء إنشاء الحساب: {str(e)}',
                    }, status=500, json_dumps_params={'ensure_ascii': False})
                messages.error(request, f'حدث خطأ: {str(e)}')
        elif _wants_json(request):
            errors = _serialize_form_errors(form)
            return JsonResponse({
                'success': False,
                'message': _first_error_message(errors),
                'errors': errors,
            }, status=400, json_dumps_params={'ensure_ascii': False})
    else:
        session_data = request.session.get('reg_step2', {})
        initial_timezone = session_data.get('timezone', get_timezone_for_country(session_data.get('country', DEFAULT_COUNTRY)))
        form = Step3SettingsForm(initial={'timezone': initial_timezone})

    session_data = request.session.get('reg_step2', {})
    session_country = session_data.get('country', DEFAULT_COUNTRY)
    suggested_currency = COUNTRY_CURRENCY_MAP.get(session_country, 'SDG')

    return render(request, 'accounts/register_step3.html', {
        'form': form,
        'step': 3,
        'total_steps': 3,
        'timezone_currency_map_json': json.dumps(TIMEZONE_CURRENCY_MAP, ensure_ascii=False),
        'currency_ar_json': json.dumps(CURRENCY_AR, ensure_ascii=False),
        'suggested_currency': suggested_currency,
    })


def login_view(request):
    """تسجيل الدخول"""
    
    if request.user.is_authenticated:
        if _wants_json(request):
            return JsonResponse({
                'success': True,
                'redirect_url': reverse('core:admin_dashboard') if request.user.is_superuser else reverse('core:dashboard'),
            }, json_dumps_params={'ensure_ascii': False})
        return redirect('core:admin_dashboard' if request.user.is_superuser else 'core:dashboard')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            remember_me = form.cleaned_data.get('remember_me', False)
            user = form.get_user()

            if user is not None:
                login(request, user)
                request.tenant = user.tenant
                if user.tenant:
                    log_activity(request, 'تسجيل دخول للنظام', f'المستخدم: {user.get_full_name()}\nاسم الدخول: {user.username}', 'login')

                # Remember me
                if not remember_me:
                    request.session.set_expiry(0)  # Session expires when browser closes

                # Redirect based on user type
                if user.is_superuser:
                    next_url = reverse('core:admin_dashboard')
                else:
                    next_url = request.POST.get('next') or request.GET.get('next', 'core:dashboard')

                if _wants_json(request):
                    return JsonResponse({
                        'success': True,
                        'message': f'مرحباً {user.get_full_name()}!',
                        'redirect_url': next_url if next_url.startswith('/') else reverse('core:dashboard'),
                    })

                messages.success(request, f'مرحباً {user.get_full_name()}!')
                return redirect(next_url)
        if _wants_json(request):
            errors = _serialize_form_errors(form)
            return JsonResponse({
                'success': False,
                'message': _first_error_message(errors, default='اسم المستخدم أو كلمة المرور غير صحيحة'),
                'errors': errors,
            }, status=400, json_dumps_params={'ensure_ascii': False})
        else:
            messages.error(request, 'اسم المستخدم أو كلمة المرور غير صحيحة')
    else:
        form = LoginForm()
    
    return render(request, 'accounts/login.html', {
        'form': form,
    })


def register_step1_api(request):
    request.is_api = True
    return register_step1(request)


def register_step2_api(request):
    request.is_api = True
    return register_step2(request)


def register_step3_api(request):
    request.is_api = True
    return register_step3(request)


def login_api(request):
    request.is_api = True
    return login_view(request)


@login_required
def logout_view(request):
    """تسجيل الخروج"""
    log_activity(request, 'تسجيل خروج من النظام', f'المستخدم: {request.user.get_full_name()}\nاسم الدخول: {request.user.username}', 'login')
    logout(request)
    messages.info(request, 'تم تسجيل الخروج بنجاح')
    return redirect('accounts:login')


@require_POST
@login_required
def login_as_tenant_api(request, tenant_id):
    """تسجيل الدخول كمدير مشترك (للمشرف العام فقط)"""
    if not request.user.is_superuser:
        return JsonResponse({'success': False, 'message': 'غير مصرح'}, status=403, json_dumps_params={'ensure_ascii': False})

    password = request.POST.get('password', '')
    if not request.user.check_password(password):
        return JsonResponse({'success': False, 'message': 'كلمة مرور المشرف غير صحيحة'}, status=400, json_dumps_params={'ensure_ascii': False})

    from apps.core.models import Tenant
    try:
        tenant = Tenant.objects.get(pk=tenant_id)
    except Tenant.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'المشترك غير موجود'}, status=404, json_dumps_params={'ensure_ascii': False})

    target_user = User.objects.filter(tenant=tenant, is_tenant_admin=True, is_active=True).first()
    if target_user is None:
        return JsonResponse({'success': False, 'message': 'لا يوجد مدير نشط لهذا المشترك'}, status=404, json_dumps_params={'ensure_ascii': False})

    impersonator_id = request.user.pk
    target_user.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, target_user)
    request.session['_impersonator_id'] = impersonator_id

    return JsonResponse({'success': True, 'redirect_url': reverse('core:dashboard')}, json_dumps_params={'ensure_ascii': False})


@login_required
def exit_impersonation(request):
    """الخروج من وضع الانتحال والعودة لحساب المشرف"""
    impersonator_id = request.session.get('_impersonator_id')
    if not impersonator_id:
        return redirect('core:dashboard')

    try:
        superuser = User.objects.get(pk=impersonator_id, is_superuser=True)
    except User.DoesNotExist:
        logout(request)
        return redirect('accounts:login')

    superuser.backend = 'django.contrib.auth.backends.ModelBackend'
    login(request, superuser)

    return redirect('core:admin_dashboard')


@login_required
def profile_view(request):
    """الملف الشخصي"""
    return render(request, 'accounts/profile.html', {
        'user': request.user,
    })


def password_reset_request(request):
    """طلب إعادة تعيين كلمة المرور"""
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    if request.method == 'POST':
        form = PasswordResetForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            user = User.objects.get(email=email)

            # Generate token
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)

            # Send email
            reset_url = request.build_absolute_uri(
                reverse('accounts:password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
            )

            subject = 'إعادة تعيين كلمة المرور - منصة إنجاز'
            message = render_to_string('accounts/email/password_reset_email.html', {
                'user': user,
                'reset_url': reset_url,
            })

            try:
                send_mail(
                    subject=subject,
                    message='',  # Empty for HTML email
                    html_message=message,
                    from_email=None,  # Use DEFAULT_FROM_EMAIL
                    recipient_list=[email],
                    fail_silently=False,
                )
                messages.success(request, 'تم إرسال رابط إعادة التعيين إلى بريدك الإلكتروني')
                # Stay on the same page instead of redirecting to login
            except Exception as e:
                messages.error(request, 'حدث خطأ في إرسال البريد الإلكتروني. يرجى المحاولة لاحقاً')

    else:
        form = PasswordResetForm()

    return render(request, 'accounts/password_reset_request.html', {
        'form': form,
    })


def password_reset_confirm(request, uidb64, token):
    """تأكيد إعادة تعيين كلمة المرور"""
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        if request.method == 'POST':
            form = SetPasswordForm(request.POST)
            if form.is_valid():
                user.set_password(form.cleaned_data['new_password1'])
                user.save()
                messages.success(request, 'تم تغيير كلمة المرور بنجاح. يمكنك الآن تسجيل الدخول')
                # Stay on the same page instead of redirecting to login
        else:
            form = SetPasswordForm()
    else:
        messages.error(request, 'رابط إعادة التعيين غير صحيح أو منتهي الصلاحية')
        return redirect('accounts:password_reset_request')

    return render(request, 'accounts/password_reset_confirm.html', {
        'form': form,
        'valid_link': True,
    })


def password_reset_complete(request):
    """تم إعادة تعيين كلمة المرور بنجاح"""
    if request.user.is_authenticated:
        return redirect('core:dashboard')

    return render(request, 'accounts/password_reset_complete.html')


# ─────────────────────────────────────────────
#   ACTIVITY LOG
# ─────────────────────────────────────────────

@login_required
def activity_log(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:dashboard')

    is_admin = request.user.is_tenant_admin

    users_qs = None
    selected_user_id = request.GET.get('user', '').strip()

    if is_admin:
        from .models import User as AccountUser
        users_qs = AccountUser.objects.filter(tenant=tenant).order_by('first_name', 'username')
        qs = UserActivity.objects.filter(tenant=tenant).select_related('user')
        if selected_user_id:
            qs = qs.filter(user_id=selected_user_id)
    else:
        qs = UserActivity.objects.filter(tenant=tenant, user=request.user)

    qs = qs.order_by('-created_at')[:500]

    return render(request, 'accounts/activity_log.html', {
        'activities': qs,
        'users': users_qs,
        'selected_user_id': selected_user_id,
        'is_admin_view': is_admin,
    })