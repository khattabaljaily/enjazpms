from django import forms
from .models import Stock


class StockForm(forms.ModelForm):
    class Meta:
        model = Stock
        fields = [
            'name', 'code', 'stock_type', 'address', 'notes', 'is_active', 'is_default',
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'مثال: المخزن الرئيسي',
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'يُولَّد تلقائياً إن تُرك فارغاً',
            }),
            'stock_type': forms.Select(attrs={'class': 'form-select'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم المخزن',
            'code': 'الرمز',
            'stock_type': 'نوع المخزن',
            'address': 'العنوان / الموقع',
            'notes': 'ملاحظات',
            'is_active': 'نشط',
            'is_default': 'مخزن افتراضي',
        }
