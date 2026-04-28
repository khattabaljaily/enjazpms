from django import forms

from .models import Customer


class CustomerForm(forms.ModelForm):
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
            'opening_balance': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'credit_limit': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم العميل',
            'phone': 'رقم الهاتف',
            'email': 'البريد الإلكتروني',
            'city': 'المدينة',
            'address': 'العنوان',
            'opening_balance': 'الرصيد الافتتاحي',
            'credit_limit': 'الحد الائتماني',
            'notes': 'ملاحظات',
            'is_active': 'نشط',
        }
