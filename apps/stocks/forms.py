from django import forms
from .models import Stock


class StockForm(forms.ModelForm):
    class Meta:
        model = Stock
        fields = [
            'name', 'code', 'branch', 'address', 'notes', 'is_active', 'is_default', 'is_central',
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
            'is_central': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم المخزن',
            'code': 'الرمز',
            'branch': 'الفرع',
            'address': 'العنوان / الموقع',
            'notes': 'ملاحظات',
            'is_active': 'نشط',
            'is_default': 'مخزن افتراضي',
            'is_central': 'مخزن مركزي للإدارة',
        }

    def __init__(self, *args, tenant=None, branch=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['branch'].required = False
        if tenant:
            from apps.core.models import Branch
            self.fields['branch'].queryset = Branch.objects.filter(tenant=tenant, is_active=True)
            # نسخة المؤسسات: كل مخزن يتبع فرعاً — لا مخازن بلا فرع.
            if tenant.is_enterprise():
                self.fields['branch'].required = True
        else:
            from apps.core.models import Branch
            self.fields['branch'].queryset = Branch.objects.none()

        self.tenant = tenant
        # المخزن المركزي: للمستخدم المركزي (مدير النشاط) فقط، في نمط المشتريات "هجين"،
        # عند الإنشاء فقط، ومخزن مركزي واحد لكل مشترك.
        can_make_central = bool(
            tenant and tenant.is_hybrid_purchasing() and branch is None
            and not (self.instance and self.instance.pk)
            and not Stock.objects.filter(tenant=tenant, is_central=True).exists()
        )
        if not can_make_central:
            del self.fields['is_central']
        elif self.data and str(self.data.get('is_central', '')).lower() in ('on', 'true', '1'):
            self.fields['branch'].required = False

        # مستخدم مربوط بفرع: المخزن يُختم تلقائياً بفرعه — لا داعي لإظهار
        # حقل اختيار الفرع (راجع BRANCH_SCOPING.md §3 نقطة 4).
        if branch is not None:
            self.fields['branch'].initial = branch.pk
            del self.fields['branch']

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('is_central'):
            # مخزن الإدارة المركزية: بلا فرع، وغير افتراضي (الافتراضي للشراء التلقائي في الفروع).
            cleaned['branch'] = None
            cleaned['is_default'] = False
            self._errors.pop('branch', None)
        return cleaned
