from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import BusinessType, Tenant, Branch
from .constants import COUNTRY_CHOICES, CURRENCY_CHOICES, COUNTRY_TIMEZONE_MAP


class TenantForm(forms.ModelForm):
    subscription_expires = forms.DateField(
        label='نهاية الاشتراك',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )
    subscription_start = forms.DateField(
        label='بداية الاشتراك',
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
    )

    class Meta:
        model = Tenant
        fields = [
            'name', 'business_type', 'email', 'phone', 'city', 'country',
            'address', 'subscription_plan', 'subscription_start',
            'subscription_expires', 'version_type', 'max_users',
            'max_stocks', 'max_branches', 'timezone', 'currency',
            'is_active', 'is_demo',
            'hard_currency_mode', 'hard_currency', 'exchange_rate',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم النشاط التجاري'}),
            'business_type': forms.Select(attrs={'class': 'form-select'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'example@domain.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '+249 ...'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'المدينة'}),
            'country': forms.Select(attrs={'class': 'form-select'}, choices=COUNTRY_CHOICES),
            'address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'العنوان التفصيلي'}),
            'subscription_plan': forms.Select(attrs={'class': 'form-select'}),
            'version_type': forms.Select(attrs={'class': 'form-select', 'disabled': True, 'tabindex': '-1'}),
            'max_users': forms.TextInput(attrs={'class': 'form-control', 'readonly': True, 'tabindex': '-1', 'inputmode': 'numeric'}),
            'max_stocks': forms.TextInput(attrs={'class': 'form-control', 'readonly': True, 'tabindex': '-1', 'inputmode': 'numeric'}),
            'max_branches': forms.TextInput(attrs={'class': 'form-control', 'readonly': True, 'tabindex': '-1', 'inputmode': 'numeric'}),
            'timezone': forms.TextInput(attrs={'class': 'form-control', 'readonly': True, 'placeholder': 'يتحدد تلقائياً من البلد'}),
            'currency': forms.HiddenInput(),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_demo': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'hard_currency_mode': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_hard_currency_mode'}),
            'hard_currency': forms.Select(attrs={'class': 'form-select'}, choices=[
                ('USD', 'دولار أمريكي (USD)'),
                ('CNY', 'يوان صيني (CNY)'),
                ('AED', 'درهم إماراتي (AED)'),
                ('SAR', 'ريال سعودي (SAR)'),
                ('EGP', 'جنيه مصري (EGP)'),
            ]),
            'exchange_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.0001', 'placeholder': 'مثال: 5500'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['business_type'].queryset = BusinessType.objects.filter(is_active=True).order_by('display_order', 'name_ar')
        self.fields['business_type'].label = 'نوع النشاط'
        self.fields['version_type'].required = False
        if not self.instance.pk:
            self.fields['subscription_start'].initial = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        plan = cleaned_data.get('subscription_plan')
        limits = Tenant.PLAN_LIMITS.get(plan, Tenant.PLAN_LIMITS['basic'])

        # تخفيض باقة مشترك موجود بالفعل: لازم يكون استخدامه الحالي (فروع/
        # مخازن/مستخدمين) ضمن حدود الباقة الجديدة قبل قبول الحفظة، وإلا
        # تبقى بيانات فروع/مستخدمين "مؤسسات" حية بينما النظام صار يعاملها
        # كنسخة أبسط — لا حذف تلقائي ولا تخمين دمج، الحسم يدوي بيد المدير
        # (يقفل/يدمج الزائد بنفسه أولاً). راجع نفس فلسفة Branch.can_add_branch
        # (apps/core/models.py) وStock.can_add_stock (apps/stocks/models.py)
        # وفحص user_create_api (apps/accounts/views.py) — نفس القيود، لكن هنا
        # مطبَّقة عكسياً وقت *تخفيض* الباقة بدل *إضافة* سجل جديد.
        if self.instance.pk:
            from apps.accounts.models import User
            from apps.stocks.models import Stock

            usage_checks = (
                (Branch.objects.filter(tenant=self.instance, is_active=True).count(), limits['max_branches'], 'الفروع'),
                (Stock.objects.filter(tenant=self.instance, is_active=True).count(), limits['max_stocks'], 'المخازن'),
                (User.objects.filter(tenant=self.instance).count(), limits['max_users'], 'المستخدمين'),
            )
            for current_count, allowed, label in usage_checks:
                if current_count > allowed:
                    plan_name = dict(Tenant.SUBSCRIPTION_PLANS).get(plan, plan)
                    raise ValidationError(
                        f'لا يمكن تخفيض الباقة إلى "{plan_name}" — عدد {label} الحالي '
                        f'({current_count}) يتجاوز الحد المسموح به لهذه الباقة ({allowed}). '
                        f'يرجى تقليل العدد أولاً ثم إعادة المحاولة.'
                    )

        # نوع النسخة والحدود الرقمية كلها إجبارية ومشتقة من الباقة مباشرة —
        # الحقل معطّل بالواجهة، وهنا يُفرض بغض النظر عمّا وصل في الطلب.
        cleaned_data['version_type'] = limits['allowed_version_types'][-1]
        cleaned_data['max_stocks'] = limits['max_stocks']
        cleaned_data['max_branches'] = limits['max_branches']
        cleaned_data['max_users'] = limits['max_users']
        return cleaned_data


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ['name', 'code', 'address', 'phone', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'يُولَّد تلقائياً إن تُرك فارغاً'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
