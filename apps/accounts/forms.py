"""
Forms للتسجيل وتسجيل الدخول
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from apps.core.constants import COUNTRY_CHOICES, DEFAULT_COUNTRY
from apps.core.models import BusinessType, Tenant, Settings
from .models import User


def apply_arabic_error_messages(form_instance):
    """تعريب رسائل التحقق الافتراضية"""
    for field in form_instance.fields.values():
        field.error_messages['required'] = 'هذا الحقل مطلوب'

        if isinstance(field, forms.EmailField):
            field.error_messages['invalid'] = 'أدخل بريدًا إلكترونيًا صحيحًا'

        if isinstance(field, forms.DecimalField):
            field.error_messages['invalid'] = 'أدخل رقمًا صحيحًا'

        if isinstance(field, forms.IntegerField):
            field.error_messages['invalid'] = 'أدخل رقمًا صحيحًا'

        if isinstance(field, forms.ChoiceField):
            field.error_messages['invalid_choice'] = 'الاختيار غير صحيح'


class Step1UserForm(forms.Form):
    """الخطوة 1: معلومات المستخدم"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        for field_name, field in self.fields.items():
            if field_name in ['password', 'password_confirm']:
                field.widget.attrs['autocomplete'] = 'new-password'
            else:
                field.widget.attrs['autocomplete'] = 'off'
    
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
            'class': 'form-control text-start',
            'placeholder': 'example@email.com',
            'dir': 'ltr'
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        for field in self.fields.values():
            field.widget.attrs['autocomplete'] = 'off'
    
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
            'placeholder': 'مثال: اسم النشاط التجاري'
        })
    )
    
    phone = forms.CharField(
        label='رقم الهاتف',
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+000 000 000000'
        })
    )
    
    address = forms.CharField(
        label='العنوان',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'أدخل العنوان الكامل'
        })
    )
    
    city = forms.CharField(
        label='المدينة',
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'المدينة / المنطقة'
        })
    )

    country = forms.ChoiceField(
        label='البلد',
        choices=COUNTRY_CHOICES,
        initial=DEFAULT_COUNTRY,
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )


class Step3SettingsForm(forms.Form):
    """الخطوة 3: إعدادات النظام"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        for field in self.fields.values():
            field.widget.attrs['autocomplete'] = 'off'
    
    VERSION_CHOICES = (
        ('single_store', 'محل واحد بمخزن واحد'),
        ('multi_stock', 'محل واحد بمخازن متعددة'),
        ('multi_branch', 'فروع متعددة (محلات ومخازن)'),
    )
    
    version_type = forms.ChoiceField(
        label='نوع النسخة',
        choices=VERSION_CHOICES,
        initial='single_store',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    timezone = forms.CharField(
        label='المنطقة الزمنية',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'readonly': 'readonly',
            'placeholder': 'ستُضبط تلقائياً حسب البلد'
        })
    )
    
    currency = forms.CharField(
        label='العملة',
        max_length=3,
        initial='USD',
        widget=forms.TextInput(attrs={
            'class': 'form-control text-start',
            'dir': 'ltr',
            'placeholder': 'مثال: ج.س'
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

    error_messages = {
        'invalid_login': 'اسم المستخدم أو كلمة المرور غير صحيحة',
        'inactive': 'هذا الحساب غير نشط',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        self.fields['username'].widget.attrs['autocomplete'] = 'off'
        self.fields['password'].widget.attrs['autocomplete'] = 'new-password'
    
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
            'class': 'form-check-input',
            'autocomplete': 'off'
        })
    )
