from django import forms

from .models import BankAccount


class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = ['name', 'bank_name', 'account_number', 'iban', 'is_default', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'cx-input', 'placeholder': 'اسم الحساب'}),
            'bank_name': forms.TextInput(attrs={'class': 'cx-input', 'placeholder': 'اسم البنك'}),
            'account_number': forms.TextInput(attrs={'class': 'cx-input', 'placeholder': 'رقم الحساب'}),
            'iban': forms.TextInput(attrs={'class': 'cx-input', 'placeholder': 'الآيبان'}),
            'notes': forms.Textarea(attrs={'class': 'cx-input', 'rows': 3, 'placeholder': 'ملاحظات'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
