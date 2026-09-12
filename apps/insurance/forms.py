from decimal import Decimal

from django import forms

from .models import InsuranceCompany


class InsuranceCompanyForm(forms.ModelForm):
    default_coverage_percent = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
        label='نسبة التغطية الافتراضية %',
    )
    settlement_period_days = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
        label='مدة التسوية المتوقعة (يوم)',
    )

    class Meta:
        model = InsuranceCompany
        fields = [
            'name', 'code', 'contact_person', 'phone', 'email', 'address',
            'default_coverage_percent', 'settlement_period_days', 'is_active', 'notes',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'يُولَّد تلقائياً إن تُرك فارغاً'}),
            'contact_person': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }

    def clean_default_coverage_percent(self):
        value = self.cleaned_data.get('default_coverage_percent')
        return value if value is not None else Decimal('0')

    def clean_settlement_period_days(self):
        value = self.cleaned_data.get('settlement_period_days')
        return value if value is not None else 30
