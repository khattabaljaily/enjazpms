"""
Views للحسابات - التسجيل وتسجيل الدخول
"""
from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from datetime import datetime, timedelta

from apps.core.models import Tenant, Settings
from .models import User
from .forms import Step1UserForm, Step2BusinessForm, Step3SettingsForm, LoginForm


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
            return redirect('accounts:register_step2')
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
        return redirect('accounts:register_step1')
    
    if request.method == 'POST':
        form = Step2BusinessForm(request.POST)
        if form.is_valid():
            # حفظ البيانات في Session
            request.session['reg_step2'] = {
                'business_type_id': form.cleaned_data['business_type'].id,
                'business_name': form.cleaned_data['business_name'],
                'phone': form.cleaned_data['phone'],
                'address': form.cleaned_data['address'],
                'city': form.cleaned_data['city'],
            }
            return redirect('accounts:register_step3')
    else:
        initial = request.session.get('reg_step2', {})
        form = Step2BusinessForm(initial=initial)
    
    return render(request, 'accounts/register_step2.html', {
        'form': form,
        'step': 2,
        'total_steps': 3,
    })


def register_step3(request):
    """الخطوة 3: إعدادات النظام وإنشاء الحساب"""
    
    # التحقق من إتمام الخطوات السابقة
    if 'reg_step1' not in request.session or 'reg_step2' not in request.session:
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
                    
                    messages.success(request, f'مرحباً {user.get_full_name()}! تم إنشاء حسابك بنجاح')
                    return redirect('core:dashboard')
                    
            except Exception as e:
                messages.error(request, f'حدث خطأ: {str(e)}')
    else:
        form = Step3SettingsForm()
    
    return render(request, 'accounts/register_step3.html', {
        'form': form,
        'step': 3,
        'total_steps': 3,
    })


def login_view(request):
    """تسجيل الدخول"""
    
    if request.user.is_authenticated:
        return redirect('core:dashboard')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            remember_me = form.cleaned_data.get('remember_me', False)
            
            user = authenticate(request, username=username, password=password)
            
            if user is not None:
                login(request, user)
                
                # Remember me
                if not remember_me:
                    request.session.set_expiry(0)  # Session expires when browser closes
                
                messages.success(request, f'مرحباً {user.get_full_name()}!')
                
                # Redirect to next or dashboard
                next_url = request.GET.get('next', 'core:dashboard')
                return redirect(next_url)
        else:
            messages.error(request, 'اسم المستخدم أو كلمة المرور غير صحيحة')
    else:
        form = LoginForm()
    
    return render(request, 'accounts/login.html', {
        'form': form,
    })


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
