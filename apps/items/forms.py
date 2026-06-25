from decimal import Decimal

from django import forms

from .models import Category, Unit, Item


class CategoryForm(forms.ModelForm):
    display_order = forms.IntegerField(
        required=False, initial=0,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
        label='ترتيب العرض',
    )

    class Meta:
        model = Category
        fields = ['name', 'parent', 'icon', 'description', 'display_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: مسكنات الألم'}),
            'parent': forms.Select(attrs={'class': 'form-select'}),
            'icon': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'fa-tag'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم التصنيف',
            'parent': 'التصنيف الرئيسي',
            'icon': 'أيقونة',
            'description': 'وصف',
            'display_order': 'ترتيب العرض',
            'is_active': 'نشط',
        }
        help_texts = {
            'icon': 'رمز FontAwesome يظهر بجانب اسم التصنيف — مثال: fa-mobile أو fa-pills. اتركه فارغاً للأيقونة الافتراضية.',
            'display_order': 'رقم يحدد موضع التصنيف في القوائم — الأصغر يظهر أولاً. اتركه 0 للترتيب التلقائي.',
            'description': 'ملاحظة أو توضيح داخلي للتصنيف — لا يظهر للعملاء.',
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant:
            # عرض التصنيفات الجذرية فقط كـ parent (تجنب حلقات لانهائية)
            qs = Category.objects.filter(tenant=tenant, is_active=True, parent=None)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            self.fields['parent'].queryset = qs
        else:
            self.fields['parent'].queryset = Category.objects.none()


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ['name', 'abbreviation', 'base_unit', 'conversion_factor', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: قطعة، كرتون، كيلو'}),
            'abbreviation': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'مثال: قط، CTN، كغ'}),
            'base_unit': forms.Select(attrs={'class': 'form-select'}),
            'conversion_factor': forms.NumberInput(attrs={'class': 'form-control', 'step': '1', 'min': '1', 'placeholder': 'مثال: 12'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم الوحدة',
            'abbreviation': 'رمز مختصر',
            'base_unit': 'تُحسب بالنسبة لـ',
            'conversion_factor': 'الكمية المعادلة',
            'is_active': 'نشط',
        }
        help_texts = {
            'abbreviation': 'يظهر في الفواتير بدل الاسم الكامل — اختياري.',
            'base_unit': 'اختر الوحدة الأصغر التي تتكون منها هذه الوحدة.',
            'conversion_factor': 'كم وحدة أصغر تساوي هذه الوحدة؟ مثال: الكرتون = 12 قطعة → أدخل 12.',
        }

    def __init__(self, *args, tenant=None, **kwargs):
        super().__init__(*args, **kwargs)
        if tenant:
            qs = Unit.objects.filter(tenant=tenant, is_active=True, base_unit=None)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            self.fields['base_unit'].queryset = qs
        else:
            self.fields['base_unit'].queryset = Unit.objects.none()


class ItemForm(forms.ModelForm):
    cost_price       = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='سعر التكلفة')
    selling_price    = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='سعر البيع')
    min_selling_price= forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='الحد الأدنى للبيع')
    tax_rate         = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), label='نسبة الضريبة %')
    min_quantity     = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}), label='حد الطلب الأدنى')
    max_quantity     = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}), label='الحد الأقصى')

    cost_price_hc       = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}), label='سعر التكلفة')
    selling_price_hc    = forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}), label='سعر البيع')
    min_selling_price_hc= forms.DecimalField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.0001'}), label='الحد الأدنى للبيع')

    class Meta:
        model = Item
        fields = [
            'name', 'name_en', 'sku', 'barcode', 'item_type',
            'category',
            'cost_price', 'selling_price', 'min_selling_price', 'tax_rate',
            'cost_price_hc', 'selling_price_hc', 'min_selling_price_hc',
            'min_quantity', 'max_quantity',
            'track_expiry', 'track_batch', 'track_serial',
            'description', 'image', 'is_active', 'is_sellable', 'is_purchasable',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم المنتج'}),
            'name_en': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Product name in English'}),
            'sku': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'يُولَّد تلقائياً إن تُرك فارغاً'}),
            'barcode': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'امسح أو أدخل الباركود'}),
            'item_type': forms.Select(attrs={'class': 'form-select'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'track_expiry': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'track_batch': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'track_serial': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_sellable': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_purchasable': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم المنتج',
            'name_en': 'الاسم بالإنجليزية',
            'sku': 'رمز المنتج (SKU)',
            'barcode': 'الباركود',
            'item_type': 'نوع الصنف',
            'category': 'التصنيف',
            'track_expiry': 'تتبع تاريخ الانتهاء',
            'track_batch': 'تتبع رقم الدفعة',
            'track_serial': 'تتبع الرقم التسلسلي',
            'description': 'الوصف',
            'image': 'الصورة',
            'is_active': 'نشط',
            'is_sellable': 'قابل للبيع',
            'is_purchasable': 'قابل للشراء',
        }

    def __init__(self, *args, tenant=None, capabilities=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tenant = tenant

        # Filter item_type choices based on capabilities
        allowed_types = ['product', 'service']
        if capabilities is None or capabilities.has_manufacturing:
            allowed_types.extend(['raw_material', 'semi_finished'])
        self.fields['item_type'].choices = [
            (v, l) for v, l in Item.ITEM_TYPE_CHOICES if v in allowed_types
        ]

        if tenant:
            self.fields['category'].queryset = Category.objects.filter(
                tenant=tenant, is_active=True
            )
        else:
            self.fields['category'].queryset = Category.objects.none()

    def _zero_if_none(self, field):
        value = self.cleaned_data.get(field)
        return value if value is not None else Decimal('0')

    def clean_cost_price(self):        return self._zero_if_none('cost_price')
    def clean_selling_price(self):     return self._zero_if_none('selling_price')
    def clean_min_selling_price(self): return self._zero_if_none('min_selling_price')
    def clean_tax_rate(self):          return self._zero_if_none('tax_rate')
    def clean_min_quantity(self):      return self._zero_if_none('min_quantity')
    def clean_max_quantity(self):      return self._zero_if_none('max_quantity')
