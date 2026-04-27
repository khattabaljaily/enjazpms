"""
Forms للتسجيل وتسجيل الدخول
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from apps.core.models import BusinessType, Tenant, Settings
from .models import User


class Step1UserForm(forms.Form):
    """الخطوة 1: معلومات المستخدم"""
    
    username = forms.CharField(
        label='اسم المستخدم',
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'اسم المستخدم',
            'autofocus': True
        })
    )
    
    email = forms.EmailField(
        label='البريد الإلكتروني',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'example@email.com'
        })
    )
    
    password = forms.CharField(
        label='كلمة المرور',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••'
        })
    )
    
    password_confirm = forms.CharField(
        label='تأكيد كلمة المرور',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••'
        })
    )
    
    def clean_username(self):
        username = self.cleaned_data.get('username')
        if User.objects.filter(username=username).exists():
            raise ValidationError('اسم المستخدم موجود بالفعل')
        return username
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise ValidationError('البريد الإلكتروني مستخدم بالفعل')
        return email
    
    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        
        if password and password_confirm:
            if password != password_confirm:
                raise ValidationError('كلمات المرور غير متطابقة')
        
        return cleaned_data


class Step2BusinessForm(forms.Form):
    """الخطوة 2: معلومات النشاط التجاري"""
    
    business_type = forms.ModelChoiceField(
        label='نوع النشاط التجاري',
        queryset=BusinessType.objects.filter(is_active=True),
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        empty_label='-- اختر نوع النشاط --'
    )
    
    business_name = forms.CharField(
        label='اسم النشاط التجاري',
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'مثال: صيدلية النور'
        })
    )
    
    phone = forms.CharField(
        label='رقم الهاتف',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+20 123 456 7890'
        })
    )
    
    address = forms.CharField(
        label='العنوان',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'العنوان الكامل'
        })
    )
    
    city = forms.CharField(
        label='المدينة',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'القاهرة'
        })
    )


class Step3SettingsForm(forms.Form):
    """الخطوة 3: إعدادات النظام"""
    
    VERSION_CHOICES = (
        ('single_store', 'محل واحد بمخزن واحد'),
        ('multi_stock', 'محل واحد بمخازن متعددة'),
        ('multi_branch', 'فروع متعددة (محلات ومخازن)'),
    )
    
    TIMEZONE_CHOICES = (
        ('Africa/Cairo', 'القاهرة (GMT+2)'),
        ('Asia/Riyadh', 'الرياض (GMT+3)'),
        ('Asia/Dubai', 'دبي (GMT+4)'),
    )
    
    CURRENCY_CHOICES = (
        ('EGP', 'جنيه مصري'),
        ('SAR', 'ريال سعودي'),
        ('AED', 'درهم إماراتي'),
        ('USD', 'دولار أمريكي'),
    )
    
    version_type = forms.ChoiceField(
        label='نوع النسخة',
        choices=VERSION_CHOICES,
        initial='single_store',
        widget=forms.RadioSelect(attrs={
            'class': 'form-check-input'
        })
    )
    
    timezone = forms.ChoiceField(
        label='المنطقة الزمنية',
        choices=TIMEZONE_CHOICES,
        initial='Africa/Cairo',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    currency = forms.ChoiceField(
        label='العملة',
        choices=CURRENCY_CHOICES,
        initial='EGP',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    tax_enabled = forms.BooleanField(
        label='تفعيل الضرائب',
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )
    
    tax_value = forms.DecimalField(
        label='قيمة الضريبة (%)',
        max_digits=5,
        decimal_places=2,
        initial=0,
        required=False,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'min': '0',
            'max': '100'
        })
    )


class LoginForm(AuthenticationForm):
    """نموذج تسجيل الدخول"""
    
    username = forms.CharField(
        label='اسم المستخدم أو البريد الإلكتروني',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'اسم المستخدم',
            'autofocus': True
        })
    )
    
    password = forms.CharField(
        label='كلمة المرور',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••'
        })
    )
    
    remember_me = forms.BooleanField(
        label='تذكرني',
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )
