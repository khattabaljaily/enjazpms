from decimal import Decimal

from django import forms
from .models import Agent


class AgentForm(forms.ModelForm):
    opening_balance = forms.DecimalField(
        label='المستحقات الافتتاحية',
        required=False,
        min_value=0,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0.00', 'step': '0.01'}),
    )
    commission_rate = forms.DecimalField(
        label='معدل العمولة',
        required=False,
        min_value=0,
        decimal_places=4,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0', 'step': '0.01'}),
    )
    commission_rate_collection = forms.DecimalField(
        label='معدل عمولة التحصيل',
        required=False,
        min_value=0,
        decimal_places=4,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': '0', 'step': '0.01'}),
    )

    class Meta:
        model = Agent
        fields = [
            'name', 'phone', 'email', 'city', 'address',
            'commission_type', 'commission_basis',
            'commission_rate', 'commission_rate_collection',
            'opening_balance', 'notes', 'is_active',
        ]
        widgets = {
            'name':              forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المندوب'}),
            'phone':             forms.TextInput(attrs={'class': 'form-control', 'type': 'tel', 'placeholder': 'رقم الهاتف'}),
            'email':             forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'البريد الإلكتروني'}),
            'city':              forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'المدينة'}),
            'address':           forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'commission_type':   forms.Select(attrs={'class': 'form-control'}),
            'commission_basis':  forms.Select(attrs={'class': 'form-control'}),
            'notes':             forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active':         forms.CheckboxInput(attrs={'class': 'cx-toggle-input'}),
        }

    def clean_opening_balance(self):
        value = self.cleaned_data.get('opening_balance')
        return value if value is not None else Decimal('0')

    def clean_commission_rate(self):
        value = self.cleaned_data.get('commission_rate')
        return value if value is not None else Decimal('0')

    def clean_commission_rate_collection(self):
        value = self.cleaned_data.get('commission_rate_collection')
        return value if value is not None else Decimal('0')
