from django import forms
from django.utils import timezone

from .models import BusinessType, Tenant
from .constants import COUNTRY_CHOICES, CURRENCY_CHOICES, COUNTRY_TIMEZONE_MAP


class TenantForm(forms.ModelForm):
    subscription_expires = forms.DateField(
        label='نهاية الاشتراك',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    subscription_start = forms.DateField(
        label='بداية الاشتراك',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )

    class Meta:
        model = Tenant
        fields = [
            'name', 'business_type', 'email', 'phone', 'city', 'country',
            'address', 'subscription_plan', 'subscription_start',
            'subscription_expires', 'version_type', 'max_users',
            'max_stocks', 'max_branches', 'timezone', 'currency',
            'is_active', 'is_demo',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم النشاط التجاري'}),
            'business_type': forms.Select(attrs={'class': 'form-select'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'example@domain.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+249 ...'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'المدينة'}),
            'country': forms.Select(attrs={'class': 'form-select'}, choices=COUNTRY_CHOICES),
            'address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'العنوان التفصيلي'}),
            'subscription_plan': forms.Select(attrs={'class': 'form-select'}),
            'version_type': forms.Select(attrs={'class': 'form-select'}),
            'max_users': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'max_stocks': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'max_branches': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'timezone': forms.TextInput(attrs={'class': 'form-control', 'readonly': True, 'placeholder': 'يتحدد تلقائياً من البلد'}),
            'currency': forms.HiddenInput(),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_demo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['business_type'].queryset = BusinessType.objects.filter(is_active=True).order_by('display_order', 'name_ar')
        self.fields['business_type'].label = 'نوع النشاط'
        if not self.instance.pk:
            self.fields['subscription_start'].initial = timezone.localdate()
