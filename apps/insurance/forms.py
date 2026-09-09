from django import forms

from .models import InsuranceCompany, CustomerInsurancePolicy


class InsuranceCompanyForm(forms.ModelForm):
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
            'default_coverage_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
            'settlement_period_days': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class CustomerInsurancePolicyForm(forms.ModelForm):
    class Meta:
        model = CustomerInsurancePolicy
        fields = [
            'customer', 'insurance_company', 'policy_number', 'coverage_percent',
            'valid_from', 'valid_to', 'is_active', 'notes',
        ]
        widgets = {
            'policy_number': forms.TextInput(attrs={'class': 'form-control'}),
            'coverage_percent': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0', 'max': '100'}),
            'valid_from': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'valid_to': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
