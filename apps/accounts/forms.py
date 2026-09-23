"""
Forms للتسجيل وتسجيل الدخول
"""
from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from apps.core.constants import COUNTRY_CHOICES, DEFAULT_COUNTRY
from apps.core.models import BusinessType, Tenant, Settings, Branch
from .models import PermissionGroup, User


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


class UserManagementForm(forms.ModelForm):
    password = forms.CharField(
        label='كلمة المرور',
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••'
        })
    )
    password_confirm = forms.CharField(
        label='تأكيد كلمة المرور',
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': '••••••••'
        })
    )

    permission_groups = forms.ModelMultipleChoiceField(
        queryset=PermissionGroup.objects.none(),
        required=False,
        label='مجموعة الصلاحيات',
        widget=forms.SelectMultiple(attrs={
            'class': 'form-select',
            'size': 6,
        })
    )

    class Meta:
        model = User
        # ملاحظة: is_tenant_admin عمداً غير مدرج هنا — كل نشاط تجاري له مدير
        # واحد فقط (يُعيَّن عند التسجيل)، ولا يجوز ترقية مستخدم آخر لمدير
        # النشاط من شاشة إدارة المستخدمين هذه (لا حتى عبر POST مباشر، بما أن
        # الحقل غير موجود في الفورم أصلاً).
        fields = [
            'username',
            'first_name',
            'last_name',
            'email',
            'phone',
            'branch',
            'is_branch_supervisor',
            'is_active',
            'permission_groups',
        ]
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المستخدم'}),
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'الاسم الأول'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم العائلة'}),
            'email': forms.EmailInput(attrs={'class': 'form-control text-start', 'placeholder': 'example@email.com', 'dir': 'ltr'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+000 000 000000'}),
            'branch': forms.Select(attrs={'class': 'form-select'}),
            'is_branch_supervisor': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'permission_groups': forms.SelectMultiple(attrs={
                'class': 'form-select',
                'size': 6,
            }),
        }

    def __init__(self, *args, **kwargs):
        self.tenant = kwargs.pop('tenant', None)
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        if self.tenant is not None:
            # مجموعة "مدير النشاط" التلقائية (is_owner_group) تحمل كل صلاحيات
            # مالك الاشتراك — تُستبعد هنا عمداً حتى لا تُمنح لموظف عادي عبر
            # خانة تبدو كأي مجموعة أخرى (راجع PermissionGroup.is_owner_group).
            self.fields['permission_groups'].queryset = PermissionGroup.objects.filter(
                tenant=self.tenant,
                is_active=True,
                is_owner_group=False,
            ).order_by('name')
            self.fields['branch'].queryset = Branch.objects.filter(
                tenant=self.tenant,
                is_active=True
            ).order_by('name')
        self.fields['branch'].required = False
        for field_name, field in self.fields.items():
            if field_name in ['password', 'password_confirm']:
                field.widget.attrs['autocomplete'] = 'new-password'
            else:
                field.widget.attrs['autocomplete'] = 'off'

    def clean_username(self):
        username = self.cleaned_data.get('username')
        qs = User.objects.filter(username=username)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('اسم المستخدم موجود بالفعل')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not email:
            return email
        qs = User.objects.filter(email=email)
        if self.tenant is not None:
            qs = qs.filter(tenant=self.tenant)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError('البريد الإلكتروني مستخدم بالفعل')
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        if password or password_confirm:
            if password != password_confirm:
                raise ValidationError('كلمات المرور غير متطابقة')
        elif not self.instance.pk:
            raise ValidationError('كلمة المرور مطلوبة عند إنشاء مستخدم جديد')

        is_branch_supervisor = cleaned_data.get('is_branch_supervisor')
        branch = cleaned_data.get('branch')

        # هذا الفورم لا يُستخدم أبداً لتعديل مدير النشاط نفسه (is_tenant_admin
        # غير مدرج فيه أصلاً — راجع تعليق Meta.fields أعلاه)، فأي مستخدم يُنشأ/
        # يُعدَّل هنا في نسخة المؤسسات هو بالضرورة موظف فرع، ويجب أن يتبع فرعاً
        # محدداً — وإلا ورث request.branch = None فتصبح كل سجلاته (مصروفات،
        # فواتير...) يتيمة بلا فرع، ظاهرة خطأً لكل الفروع (نفس فئة الخلل التي
        # سبّبت خزينة عملة صعبة يتيمة — راجع apps/core/signals.py).
        if (
            self.tenant and self.tenant.is_enterprise() and not branch
            and not getattr(self.instance, 'is_tenant_admin', False)
        ):
            raise ValidationError('يجب اختيار الفرع لهذا المستخدم — كل مستخدم في نسخة المؤسسات يجب أن يتبع فرعاً محدداً.')

        if is_branch_supervisor:
            if not branch:
                raise ValidationError('يجب اختيار الفرع أولاً لتعيين المستخدم كمشرف عليه')
            existing = User.objects.filter(
                tenant=self.tenant, branch=branch, is_branch_supervisor=True,
            )
            if self.instance.pk:
                existing = existing.exclude(pk=self.instance.pk)
            other = existing.first()
            if other:
                raise ValidationError(
                    f'الفرع "{branch.name}" لديه مشرف بالفعل ({other.get_full_name()}). '
                    'يرجى إلغاء إشراف المستخدم الحالي أولاً قبل تعيين مشرف جديد.'
                )
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get('password')
        if password:
            user.set_password(password)
        if self.tenant and not user.tenant:
            user.tenant = self.tenant
        if commit:
            user.save()
            # Manually handle M2M relationship since permission_groups is defined in form
            permission_groups = self.cleaned_data.get('permission_groups')
            if permission_groups is not None:
                user.permission_groups.set(permission_groups)
        return user


class Step2BusinessForm(forms.Form):
    """الخطوة 2: معلومات النشاط التجاري"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        for field in self.fields.values():
            field.widget.attrs['autocomplete'] = 'off'
        default_business_type = BusinessType.objects.filter(is_active=True).order_by('display_order').first()
        if default_business_type:
            self.fields['business_type'].initial = default_business_type.pk

    business_type = forms.ModelChoiceField(
        label='نوع النشاط',
        queryset=BusinessType.objects.filter(is_active=True).order_by('display_order'),
        widget=forms.RadioSelect(),
        empty_label=None,
    )

    business_name = forms.CharField(
        label='اسم النشاط التجاري',
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'مثال: صيدلية النور أو شركة الأمل للتوزيع'
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
            'rows': 2,
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

    PLAN_CHOICES = (
        ('basic', 'أساسي'),
        ('pro', 'احترافي'),
        ('enterprise', 'مؤسسات'),
    )

    VERSION_CHOICES = (
        ('single_store', 'محل واحد بمخزن واحد'),
        ('multi_stock', 'محل واحد بمخازن متعددة'),
        ('multi_branch', 'فروع متعددة (محلات ومخازن)'),
    )
    
    subscription_plan = forms.ChoiceField(
        label='الباقة المطلوبة',
        choices=PLAN_CHOICES,
        initial='basic',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )

    version_type = forms.ChoiceField(
        label='نوع النسخة',
        choices=VERSION_CHOICES,
        initial='single_store',
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-select',
            'disabled': True,
            'tabindex': '-1',
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
        initial='SDG',
        widget=forms.TextInput(attrs={
            'class': 'form-control text-start',
            'dir': 'ltr',
            'placeholder': 'مثال: SDG'
        })
    )
    
    tax_enabled = forms.BooleanField(
        label='تفعيل الضرائب',
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    hard_currency_mode = forms.BooleanField(
        label='تفعيل وضع العملة الصعبة',
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
    )

    hard_currency = forms.ChoiceField(
        label='العملة الصعبة',
        choices=[
            ('USD', 'دولار أمريكي — USD'),
            ('CNY', 'يوان صيني — CNY'),
            ('AED', 'درهم إماراتي — AED'),
            ('SAR', 'ريال سعودي — SAR'),
            ('EGP', 'جنيه مصري — EGP'),
        ],
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )

    exchange_rate = forms.DecimalField(
        label='سعر الصرف الحالي',
        min_value=0.0001,
        max_digits=12,
        decimal_places=2,
        required=False,
        widget=forms.NumberInput(attrs={
            'class': 'form-control',
            'step': '0.01',
            'placeholder': 'مثال: 5500'
        }),
        help_text='كم وحدة من عملتك المحلية تعادل وحدة واحدة من العملة الصعبة؟'
    )

    def clean(self):
        from apps.core.models import Tenant
        cleaned_data = super().clean()
        plan = cleaned_data.get('subscription_plan')

        plan_limits = Tenant.PLAN_LIMITS.get(plan, Tenant.PLAN_LIMITS['basic'])

        # نوع النسخة وحدود المخازن/الفروع/المستخدمين كلها إجبارية ومشتقة من
        # الباقة مباشرة — الحقل معطّل بالواجهة، ويُفرض هنا بغض النظر عمّا وصل.
        cleaned_data['version_type'] = plan_limits['allowed_version_types'][-1]
        cleaned_data['max_stocks'] = plan_limits['max_stocks']
        cleaned_data['max_branches'] = plan_limits['max_branches']
        cleaned_data['max_users'] = plan_limits['max_users']

        if cleaned_data.get('hard_currency_mode'):
            if not cleaned_data.get('exchange_rate'):
                self.add_error('exchange_rate', 'يرجى إدخال سعر الصرف الحالي')

        return cleaned_data


class RegistrationRequestForm(forms.Form):
    """طلب تواصل لإنشاء حساب (أثناء إيقاف التسجيل الذاتي المؤقت)"""

    VERSION_CHOICES = Step3SettingsForm.VERSION_CHOICES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        for field in self.fields.values():
            field.widget.attrs['autocomplete'] = 'off'

    personal_email = forms.EmailField(
        label='البريد الإلكتروني الشخصي',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'example@email.com',
            'dir': 'ltr'
        })
    )

    business_name = forms.CharField(
        label='اسم النشاط التجاري',
        max_length=200,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'مثال: صيدلية النور أو شركة الأمل للتوزيع'
        })
    )

    phone = forms.CharField(
        label='رقم الهاتف',
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '+000 000 000000',
            'dir': 'ltr'
        })
    )

    address = forms.CharField(
        label='العنوان',
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 3,
            'placeholder': 'أدخل العنوان الكامل'
        })
    )

    version_type = forms.ChoiceField(
        label='نوع النسخة',
        choices=VERSION_CHOICES,
        initial='single_store',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )

    hard_currency_mode = forms.BooleanField(
        label='تفعيل وضع العملة الصعبة',
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
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


class PasswordResetForm(forms.Form):
    """نموذج طلب إعادة تعيين كلمة المرور"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        self.fields['email'].widget.attrs['autocomplete'] = 'email'

    email = forms.EmailField(
        label='البريد الإلكتروني',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'أدخل بريدك الإلكتروني',
            'autofocus': True
        })
    )

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if not User.objects.filter(email=email, is_active=True).exists():
            raise ValidationError('لا يوجد حساب بهذا البريد الإلكتروني')
        return email


class SetPasswordForm(forms.Form):
    """نموذج تعيين كلمة مرور جديدة"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_arabic_error_messages(self)
        for field_name, field in self.fields.items():
            field.widget.attrs['autocomplete'] = 'new-password'

    new_password1 = forms.CharField(
        label='كلمة المرور الجديدة',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'أدخل كلمة مرور قوية'
        }),
        help_text='كلمة المرور يجب أن تكون على الأقل 8 أحرف وتحتوي على أرقام وحروف'
    )

    new_password2 = forms.CharField(
        label='تأكيد كلمة المرور الجديدة',
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'أعد إدخال كلمة المرور'
        })
    )

    def clean_new_password2(self):
        password1 = self.cleaned_data.get('new_password1')
        password2 = self.cleaned_data.get('new_password2')
        if password1 and password2 and password1 != password2:
            raise ValidationError('كلمات المرور غير متطابقة')
        return password2

    def clean_new_password1(self):
        password = self.cleaned_data.get('new_password1')
        if len(password) < 8:
            raise ValidationError('كلمة المرور يجب أن تكون على الأقل 8 أحرف')
        return password
