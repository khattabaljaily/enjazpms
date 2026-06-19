from decimal import Decimal

from django import forms

from .models import Customer


class CustomerForm(forms.ModelForm):
    opening_balance = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        label='المديونية الافتتاحية',
    )
    credit_limit = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        label='الحد الائتماني',
    )

    class Meta:
        model = Customer
        fields = [
            'name',
            'phone',
            'email',
            'city',
            'address',
            'opening_balance',
            'credit_limit',
            'notes',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: أحمد محمد'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'type': 'tel', 'placeholder': 'مثال: 0912345678'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'example@email.com'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'الخرطوم'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم العميل',
            'phone': 'رقم الهاتف',
            'email': 'البريد الإلكتروني',
            'city': 'المدينة',
            'address': 'العنوان',
            'notes': 'ملاحظات',
            'is_active': 'نشط',
        }

    def clean_opening_balance(self):
        value = self.cleaned_data.get('opening_balance')
        return value if value is not None else Decimal('0')

    def clean_credit_limit(self):
        value = self.cleaned_data.get('credit_limit')
        return value if value is not None else Decimal('0')
