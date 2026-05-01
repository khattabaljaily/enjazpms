"""
Views للحسابات - التسجيل وتسجيل الدخول
"""
import json

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from datetime import datetime, timedelta

from apps.core.models import Tenant, Settings
from apps.core.constants import COUNTRY_TIMEZONE_MAP, DEFAULT_COUNTRY, get_timezone_for_country
from .models import User
from .forms import Step1UserForm, Step2BusinessForm, Step3SettingsForm, LoginForm


def _wants_json(request):
    return getattr(request, 'is_api', False)


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


def _first_error_message(errors_dict, default='يرجى التحقق من الحقول المطلوبة'):
    for _, messages_list in errors_dict.items():
        if messages_list:
            return messages_list[0]
    return default


def register_step1(request):
    """الخطوة 1: معلومات المستخدم"""
    
    if request.method == 'POST':
        form = Step1UserForm(request.POST)
        if form.is_valid():
            # حفظ البيانات في Session
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
        # استرجاع البيانات من Session إذا كانت موجودة
        initial = request.session.get('reg_step1', {})
        form = Step1UserForm(initial=initial)
    
    return render(request, 'accounts/register_step1.html', {
        'form': form,
        'step': 1,
        'total_steps': 3,
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
