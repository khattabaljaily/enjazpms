from decimal import Decimal

from django import forms

from .models import Supplier


class SupplierForm(forms.ModelForm):
    opening_balance = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        label='المديونية الافتتاحية',
    )
    credit_limit = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        label='حد الائتمان',
    )

    class Meta:
        model = Supplier
        fields = [
            'name',
            'phone',
            'email',
            'city',
            'address',
            'currency',
            'opening_balance',
            'credit_limit',
            'notes',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: شركة التوريدات المحدودة'}),
            'currency': forms.Select(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'type': 'tel', 'placeholder': 'مثال: 0912345678'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'example@email.com'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'الخرطوم'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_opening_balance(self):
        value = self.cleaned_data.get('opening_balance')
        return value if value is not None else Decimal('0')

    def clean_credit_limit(self):
        value = self.cleaned_data.get('credit_limit')
        return value if value is not None else Decimal('0')
