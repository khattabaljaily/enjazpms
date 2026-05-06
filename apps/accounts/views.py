"""
Views للحسابات - التسجيل وتسجيل الدخول
"""
import json

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.views.decorators.http import require_POST
from datetime import datetime, timedelta

from apps.core.models import Tenant, Settings
from apps.core.constants import COUNTRY_TIMEZONE_MAP, DEFAULT_COUNTRY, get_timezone_for_country
from .models import User
from .forms import Step1UserForm, Step2BusinessForm, Step3SettingsForm, LoginForm, UserManagementForm


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
    return JsonResponse({'success': False, 'message': message}, status=status)


def _json_ok(data=None, msg='تمت العملية بنجاح'):
    payload = {'success': True, 'message': msg}
    if data is not None:
        payload['data'] = data
    return JsonResponse(payload)


@login_required
def user_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = User.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    inactive = total - active

    context = {
        'stats': {
            'total': total,
            'active': active,
            'inactive': inactive,
        },
        'form': UserManagementForm(tenant=tenant),
    }
    return render(request, 'accounts/user_list.html', context)


@login_required
def user_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

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
            | Q(role__icontains=search_value)
        )

    records_filtered = queryset.count()

    order_column_index = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    order_column_name = request.GET.get(f'columns[{order_column_index}][data]', 'date_joined')

    allowed_order_fields = {
        'username': 'username',
        'full_name': 'first_name',
        'email': 'email',
        'role': 'role',
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
            'role': getattr(user, 'role', '-'),
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
    })


@login_required
def user_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'الطريقة غير مسموحة'}, status=405)

    form = UserManagementForm(request.POST, tenant=tenant)
    if form.is_valid():
        user = form.save(commit=False)
        user.tenant = tenant
        user.save()
        return _json_ok({'id': user.id}, 'تم إضافة المستخدم بنجاح')

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
def user_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')

    user = get_object_or_404(User.objects.for_tenant(tenant), pk=pk)
    return _json_ok({
        'id': user.id,
        'username': user.username,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'email': user.email,
        'phone': getattr(user, 'phone', ''),
        'role': getattr(user, 'role', ''),
        'is_tenant_admin': getattr(user, 'is_tenant_admin', False),
        'is_active': user.is_active,
    })


@login_required
def user_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'الطريقة غير مسموحة'}, status=405)

    user = get_object_or_404(User.objects.for_tenant(tenant), pk=pk)
    form = UserManagementForm(request.POST, instance=user, tenant=tenant)
    if form.is_valid():
        form.save()
        return _json_ok(None, 'تم تحديث بيانات المستخدم بنجاح')

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
def user_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return _json_error('لا يوجد نشاط تجاري')
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'الطريقة غير مسموحة'}, status=405)

    if request.user.pk == pk:
        return _json_error('لا يمكن حذف المستخدم الحالي')

    user = get_object_or_404(User.objects.for_tenant(tenant), pk=pk)
    user.delete()
    return _json_ok(None, 'تم حذف المستخدم بنجاح')


def register_step1(request):
    """الخطوة 1: معلومات المستخدم"""
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
                })

            return redirect('accounts:register_step2')
        if _wants_json(request):
            errors = _serialize_form_errors(form)
            return JsonResponse({
                'success': False,
                'message': _first_error_message(errors),
                'errors': errors,
            }, status=400)
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
    
    # التحقق من إتمام الخطوة 1
    if 'reg_step1' not in request.session:
        if _wants_json(request):
            return JsonResponse({
                'success': False,
                'message': 'يرجى إكمال الخطوة الأولى أولاً',
                'redirect_url': reverse('accounts:register_step1'),
            }, status=400)
        return redirect('accounts:register_step1')
    
    country_timezone_map_json = json.dumps(COUNTRY_TIMEZONE_MAP, ensure_ascii=False)
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
                })

            return redirect('accounts:register_step3')
        if _wants_json(request):
            errors = _serialize_form_errors(form)
            return JsonResponse({
                'success': False,
                'message': _first_error_message(errors),
                'errors': errors,
            }, status=400)
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
        'timezone_preview': timezone_preview,
    })


def register_step3(request):
    """الخطوة 3: إعدادات النظام وإنشاء الحساب"""
    
    # التحقق من إتمام الخطوات السابقة
    if 'reg_step1' not in request.session or 'reg_step2' not in request.session:
        if _wants_json(request):
            return JsonResponse({
                'success': False,
                'message': 'يرجى إكمال خطوات التسجيل السابقة أولاً',
                'redirect_url': reverse('accounts:register_step1'),
            }, status=400)
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
                    
                    tenant = Tenant.objects.create(
                        name=step2_data['business_name'],
                        business_type=business_type,
                        phone=step2_data['phone'],
                        address=step2_data['address'],
                        city=step2_data['city'],
                        subscription_plan='trial',
                        subscription_start=datetime.now().date(),
                        subscription_expires=datetime.now().date() + timedelta(days=30),  # 30 يوم تجريبي
                        version_type=form.cleaned_data['version_type'],
                        timezone=form.cleaned_data['timezone'],
                        currency=form.cleaned_data['currency'],
                    )
                    
                    # 2. إنشاء User
                    user = User.objects.create_user(
                        username=step1_data['username'],
                        email=step1_data['email'],
                        password=step1_data['password'],
                        tenant=tenant,
                        role='owner',
                        is_tenant_admin=True,
                    )
                    
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
                    }, status=500)
                messages.error(request, f'حدث خطأ: {str(e)}')
        elif _wants_json(request):
            errors = _serialize_form_errors(form)
            return JsonResponse({
                'success': False,
                'message': _first_error_message(errors),
                'errors': errors,
            }, status=400)
    else:
        session_data = request.session.get('reg_step2', {})
        initial_timezone = session_data.get('timezone', get_timezone_for_country(session_data.get('country', DEFAULT_COUNTRY)))
        form = Step3SettingsForm(initial={'timezone': initial_timezone})
    
    return render(request, 'accounts/register_step3.html', {
        'form': form,
        'step': 3,
        'total_steps': 3,
    })


def login_view(request):
    """تسجيل الدخول"""
    
    if request.user.is_authenticated:
        if _wants_json(request):
            return JsonResponse({
                'success': True,
                'redirect_url': reverse('core:dashboard'),
            })
        return redirect('core:dashboard')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            remember_me = form.cleaned_data.get('remember_me', False)
            user = form.get_user()

            if user is not None:
                login(request, user)
                
                # Remember me
                if not remember_me:
                    request.session.set_expiry(0)  # Session expires when browser closes
                
                # Redirect to next or dashboard
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
            }, status=400)
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
    logout(request)
    messages.info(request, 'تم تسجيل الخروج بنجاح')
    return redirect('accounts:login')


@login_required
def profile_view(request):
    """الملف الشخصي"""
    return render(request, 'accounts/profile.html', {
        'user': request.user,
    })
