from django import forms
from .models import Stock


class StockForm(forms.ModelForm):
    class Meta:
        model = Stock
        fields = [
            'name', 'code', 'branch', 'address', 'notes', 'is_active', 'is_default',
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
            'branch': forms.Select(attrs={'class': 'form-select'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_default': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم المخزن',
            'code': 'الرمز',
            'branch': 'الفرع',
            'address': 'العنوان / الموقع',
            'notes': 'ملاحظات',
            'is_active': 'نشط',
            'is_default': 'مخزن افتراضي',
        }

    def __init__(self, *args, tenant=None, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['branch'].required = False
        if tenant:
            from apps.core.models import Branch
            self.fields['branch'].queryset = Branch.objects.filter(tenant=tenant, is_active=True)
        else:
            from apps.core.models import Branch
            self.fields['branch'].queryset = Branch.objects.none()

        # مستخدم مربوط بفرع: المخزن يُختم تلقائياً بفرعه — لا داعي لإظهار
        # حقل اختيار الفرع (راجع BRANCH_SCOPING.md §3 نقطة 4).
        if branch is not None:
            self.fields['branch'].initial = branch.pk
            del self.fields['branch']
