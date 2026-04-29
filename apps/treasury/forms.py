from django import forms

from .models import Treasury


class TreasuryForm(forms.ModelForm):
    class Meta:
        model = Treasury
        fields = ['name', 'code', 'is_default', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'cx-input', 'placeholder': 'اسم الخزينة'}),
            'code': forms.TextInput(attrs={'class': 'cx-input', 'placeholder': 'رمز الخزينة'}),
            'notes': forms.Textarea(attrs={'class': 'cx-input', 'rows': 3, 'placeholder': 'ملاحظات'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
