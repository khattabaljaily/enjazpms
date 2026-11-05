"""
Field schema for the product import template/importer.

Single source of truth used by BOTH `xlsx_builder` (to generate the
downloadable template) and `product_importer` (to parse an uploaded file) —
they iterate the exact same resolved list so the two can never drift apart.

Each field spec:
    field:          canonical field name (matches Item model field name,
                    or one of the synthetic fields: stock/opening_quantity/
                    batch_number/expiry_date/base_unit_name/large_unit_name/
                    large_unit_count — see the note on units below)
    header_ar:      Arabic column header shown in the sheet
    required:       bool — required at import-row level (may be stricter
                    than the model's own null/blank)
    dtype:          text | number | decimal | date | bool | choice_fixed | choice_tenant
    choices:        for choice_fixed — list of (value, label_ar) tuples
    tenant_source:  for choice_tenant — key naming which tenant list to use
                    ('category' | 'stock')
    capability:     TenantCapabilities attribute name gating inclusion, or None
    typical_width:  hint used by xlsx_builder for column width
    description:    short Arabic description fed to the AI header-mapper
    aliases:        other column titles meaning this field (other systems' exports, English,
                    abbreviations) — matched after Arabic normalisation, before asking the AI
    template:       False = accepted on import but not written to the downloadable template
"""
from apps.items.models import Item


BASIC_FIELDS = [
    {
        'field': 'name', 'header_ar': 'اسم المنتج *', 'required': True, 'dtype': 'text',
        'capability': None, 'typical_width': 30,
        'description': 'اسم المنتج أو الصنف أو السلعة',
        'aliases': ('اسم المنتج', 'الاسم', 'اسم الصنف', 'الصنف', 'المنتج', 'اسم السلعة', 'السلعة', 'البيان',
                    'اسم المادة', 'المادة', 'اسم الدواء', 'الدواء', 'الاسم التجاري', 'الاسم بالعربي', 'الاسم العربي', 'item', 'item name', 'product',
                    'product name', 'name', 'description ar', 'arabic name', 'name ar', 'trade name', 'brand name',
                    'drug name'),
    },
    {
        'field': 'name_en', 'header_ar': 'الاسم بالإنجليزية', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 26,
        'description': 'الاسم بالإنجليزية للمنتج',
        'aliases': ('الاسم بالانجليزي', 'الاسم الانجليزي', 'اسم انجليزي', 'english name', 'name en',
                    'english', 'en name', 'latin name'),
    },
    {
        'field': 'sku', 'header_ar': 'الرمز (SKU) — اتركه فارغاً للتوليد التلقائي', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 22,
        'description': 'رمز المنتج SKU أو كود الصنف الداخلي',
        'aliases': ('الرمز', 'رمز المنتج', 'رمز الصنف', 'الكود', 'كود', 'كود الصنف', 'كود المنتج', 'رقم الصنف',
                    'رقم المنتج', 'sku', 'code', 'item code', 'product code', 'item no', 'item number', 'ref'),
    },
    {
        'field': 'barcode', 'header_ar': 'الباركود', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 18,
        'description': 'الباركود أو رمز الشريط',
        'aliases': ('باركود', 'الباركود', 'بار كود', 'رمز الشريط', 'barcode', 'bar code', 'ean', 'upc', 'gtin',
                    'ean13'),
    },
    {
        'field': 'item_type', 'header_ar': 'نوع الصنف', 'required': False, 'dtype': 'choice_fixed',
        'choices': list(Item.ITEM_TYPE_CHOICES), 'capability': None, 'typical_width': 14,
        'description': 'نوع الصنف: منتج أو خدمة',
        'aliases': ('نوع الصنف', 'نوع المنتج', 'item type', 'type'),
    },
    {
        'field': 'description', 'header_ar': 'الوصف', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 40,
        'description': 'وصف المنتج',
        'aliases': ('الوصف', 'وصف', 'وصف المنتج', 'تفاصيل', 'description', 'details'),
    },
]

CLASSIFICATION_FIELDS = [
    {
        'field': 'category', 'header_ar': 'التصنيف', 'required': False, 'dtype': 'choice_tenant',
        'tenant_source': 'category', 'capability': None, 'typical_width': 22,
        'description': 'التصنيف أو القسم أو الفئة أو المجموعة',
        'aliases': ('التصنيف', 'تصنيف', 'الفئه', 'الفئة', 'القسم', 'المجموعه', 'المجموعة', 'مجموعة الاصناف',
                    'category', 'group', 'item group', 'family', 'department', 'class'),
    },
    # NOTE: units are intentionally free text, not a choice_tenant dropdown from a
    # shared Unit list. The manual "add product" form has no such shared list either —
    # every product defines its own units via ItemUnit (see apps/items/views.py
    # `_save_item_units`): an optional base/smaller unit, and an optional single
    # larger unit + how many base units it contains. product_importer.py builds an
    # ItemUnit chain from these three columns after creating the Item.
    {
        'field': 'base_unit_name', 'header_ar': 'اسم الوحدة الأساسية (الأصغر)', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 22,
        'description': 'اسم وحدة البيع الأساسية أو الأصغر، مثل حبة أو قطعة أو كيلو',
        'aliases': ('الوحدة', 'وحدة', 'الوحده', 'وحدة القياس', 'الوحدة الاساسية', 'الوحدة الصغرى', 'وحدة البيع',
                    'الحبة', 'unit', 'uom', 'base unit', 'unit of measure'),
    },
    {
        'field': 'large_unit_name', 'header_ar': 'اسم وحدة أكبر (اختياري)', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 20,
        'description': 'اسم وحدة أكبر تحتوي على عدة وحدات أساسية، مثل كرتون أو طرد',
        'aliases': ('الوحدة الكبرى', 'الوحدة الكبيرة', 'وحدة اكبر', 'وحدة الجملة', 'العبوة', 'العبوة الكبرى',
                    'large unit', 'pack unit', 'box unit'),
    },
    {
        'field': 'large_unit_count', 'header_ar': 'كم وحدة أساسية داخل الوحدة الأكبر؟', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 18,
        'description': 'عدد الوحدات الأساسية داخل الوحدة الأكبر، مطلوب فقط إذا حُددت وحدة أكبر',
        'aliases': ('عدد الوحدات', 'التعبئة', 'التعبئه', 'محتوى العبوة', 'عدد الحبات', 'عدد القطع بالكرتون',
                    'معامل التحويل', 'pack size', 'units per pack', 'conversion', 'factor', 'qty per box'),
    },
]

# عناوين الأسعار تحمل رمز العملة المحلية (تُضاف في get_product_schema): «سعر التكلفة (ج.س)».
PRICING_FIELDS_NORMAL = [
    {
        'field': 'cost_price', 'header_ar': 'سعر التكلفة', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 16, 'description': 'سعر التكلفة أو سعر الشراء',
        'aliases': ('سعر التكلفة', 'التكلفة', 'تكلفة', 'سعر الشراء', 'الشراء', 'سعر التكلفة محلي', 'cost',
                    'cost price', 'purchase price', 'buy price', 'unit cost'),
    },
    {
        'field': 'selling_price', 'header_ar': 'سعر البيع', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 16, 'description': 'سعر البيع أو السعر',
        'aliases': ('سعر البيع', 'السعر', 'سعر', 'سعر التجزئة', 'سعر القطاعي', 'سعر المستهلك', 'سعر البيع محلي',
                    'price', 'sale price', 'selling price', 'retail price', 'unit price', 'sales price'),
    },
    {
        'field': 'min_selling_price', 'header_ar': 'أدنى سعر بيع', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 16, 'description': 'أدنى سعر بيع مسموح',
        'aliases': ('ادنى سعر بيع', 'اقل سعر', 'اقل سعر بيع', 'الحد الادنى للسعر', 'ادنى سعر', 'ادنى سعر بيع محلي',
                    'min price', 'minimum price', 'min selling price'),
    },
    {
        'field': 'tax_rate', 'header_ar': 'نسبة الضريبة %', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 14, 'description': 'نسبة الضريبة',
        'aliases': ('الضريبة', 'نسبة الضريبة', 'ضريبة', 'ضريبة القيمة المضافة', 'tax', 'vat', 'tax rate'),
    },
]

# وضع العملة الصعبة: القالب يطلب الأسعار بالعملة المحلية (الجنيه) والنظام يحسب ما يعادلها بالعملة
# الصعبة بسعر الصرف الحالي. أعمدة العملة الصعبة تبقى مقبولة عند الاستيراد (تصدير النظام والقوالب
# القديمة) لكنها لا تُكتب في القالب الجديد.
PRICING_FIELDS_HC = [
    {
        'field': 'cost_price_hc', 'header_ar': 'سعر التكلفة (عملة صعبة)', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 20, 'description': 'سعر التكلفة بالعملة الصعبة (دولار)',
        'template': False,
    },
    {
        'field': 'selling_price_hc', 'header_ar': 'سعر البيع (عملة صعبة)', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 20, 'description': 'سعر البيع بالعملة الصعبة (دولار)',
        'template': False,
    },
    {
        'field': 'min_selling_price_hc', 'header_ar': 'أدنى سعر بيع (عملة صعبة)', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 22, 'description': 'أدنى سعر بيع بالعملة الصعبة (دولار)',
        'template': False,
    },
]

LOCAL_PRICE_FIELDS = ('cost_price', 'selling_price', 'min_selling_price')

# أعمدة أُزيلت من القالب لأنها غير ضرورية عند الاستيراد، وتأخذ قيمها الافتراضية من نموذج Item:
#   الشركة الموردة = لا شيء، الحد الأدنى/الأقصى للمخزون = 0، تتبع الرقم التسلسلي = لا،
#   قابل للبيع = نعم، قابل للشراء = نعم. (تُعدَّل لاحقاً من شاشة المنتج عند الحاجة.)
STOCK_TRACKING_FIELDS = [
    {
        'field': 'track_expiry', 'header_ar': 'تتبع تاريخ الانتهاء؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_expiry_dates', 'typical_width': 12, 'description': 'هل يتم تتبع تاريخ انتهاء الصلاحية',
    },
    {
        'field': 'track_batch', 'header_ar': 'تتبع رقم الدفعة؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_batch_numbers', 'typical_width': 12, 'description': 'هل يتم تتبع رقم الدفعة',
    },
]

PHARMA_FIELDS = [
    {
        'field': 'generic_name', 'header_ar': 'الاسم العلمي / المادة الفعالة', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 28, 'description': 'الاسم العلمي أو المادة الفعالة',
        'aliases': ('الاسم العلمي', 'المادة الفعالة', 'المادة الفعاله', 'التركيبة', 'generic', 'generic name',
                    'active ingredient', 'scientific name', 'inn'),
    },
    {
        'field': 'manufacturer', 'header_ar': 'الشركة المصنعة', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 22, 'description': 'الشركة المصنعة',
        'aliases': ('الشركة المصنعة', 'الشركه المصنعه', 'شركة مصنعة', 'شركه مصنعه', 'المصنع', 'مصنع', 'الشركة المنتجة',
                    'الشركه المنتجه', 'الماركة', 'manufacturer', 'manufacturing company', 'brand', 'mfr',
                    'mfg', 'producer', 'maker'),
    },
    {
        # الحقل الذي تُجمَّع به قائمة أسعار المتجر — يختلف عن الشركة المصنعة (الوكيل يستورد/يوزّع محلياً).
        'field': 'sudan_agent', 'header_ar': 'الوكيل في السودان', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 24,
        'description': 'الوكيل أو الموزع المحلي في السودان الذي يبيع المنتج (يسمّى غالباً الوكيل أو الشركة أو الموزع) — '
                       'وهو غير الشركة المصنعة الأجنبية',
        'aliases': ('الوكيل في السودان', 'الوكيل أو الموزع في السودان', 'الوكيل', 'وكيل', 'الوكيل المحلي', 'الوكيل المعتمد',
                    'الموزع', 'موزع', 'الموزع في السودان', 'الموزع المعتمد', 'الشركة', 'الشركه', 'شركة', 'شركه',
                    'الشركة الموزعة', 'الشركه الموزعه', 'الشركة الوكيلة', 'اسم الوكيل', 'اسم الموزع', 'اسم الشركة',
                    'agent', 'sudan agent', 'local agent', 'distributor', 'dist', 'importer'),
    },
    {
        'field': 'dosage_form', 'header_ar': 'الشكل الصيدلاني', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 18,
        'description': 'الشكل الصيدلاني مثل أقراص أو شراب أو حقن',
        'aliases': ('الشكل الصيدلاني', 'الشكل', 'الشكل الدوائي', 'نوع الدواء', 'dosage form', 'form', 'pharmaceutical form'),
    },
    {
        'field': 'strength', 'header_ar': 'التركيز', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 14, 'description': 'التركيز مثل 500 مجم',
        'aliases': ('التركيز', 'القوة', 'العيار', 'strength', 'dose', 'concentration'),
    },
    {
        'field': 'requires_prescription', 'header_ar': 'يُصرف بوصفة طبية؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_drug_classification', 'typical_width': 14, 'description': 'هل يُصرف بوصفة طبية',
        'aliases': ('بوصفة', 'وصفة طبية', 'يصرف بوصفة', 'prescription', 'rx', 'prescription only'),
    },
    {
        'field': 'is_controlled_substance', 'header_ar': 'خاضع للرقابة / مخدرات؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_drug_classification', 'typical_width': 16, 'description': 'هل هو خاضع للرقابة',
        'aliases': ('خاضع للرقابة', 'مخدرات', 'مراقب', 'controlled', 'controlled substance', 'narcotic'),
    },
    {
        'field': 'is_insurance_excluded', 'header_ar': 'غير مغطى بالتأمين؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_insurance_billing', 'typical_width': 14, 'description': 'هل هو غير مغطى بالتأمين',
    },
]

OPENING_STOCK_FIELDS = [
    {
        'field': 'opening_quantity', 'header_ar': 'الكمية الافتتاحية', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 16,
        'description': 'الكمية الموجودة حالياً في المخزن (الرصيد أو الكمية المتوفرة)',
        'aliases': ('الكمية الافتتاحية', 'الكمية', 'كمية', 'الرصيد', 'رصيد', 'رصيد اول المدة', 'الكمية المتوفرة',
                    'الكمية الحالية', 'المخزون', 'الموجود', 'qty', 'quantity', 'stock qty', 'on hand', 'balance',
                    'opening qty', 'opening quantity', 'stock'),
    },
    {
        'field': 'stock', 'header_ar': 'المخزن', 'required': False, 'dtype': 'choice_tenant',
        'tenant_source': 'stock', 'capability': None, 'typical_width': 18,
        'description': 'المخزن الذي تُضاف إليه الكمية الافتتاحية',
        'aliases': ('المخزن', 'مخزن', 'المستودع', 'مستودع', 'الموقع', 'warehouse', 'store', 'location'),
    },
    {
        'field': 'batch_number', 'header_ar': 'رقم الدفعة', 'required': False, 'dtype': 'text',
        'capability': 'has_batch_numbers', 'typical_width': 16, 'description': 'رقم الدفعة أو التشغيلة',
        'aliases': ('رقم الدفعة', 'الدفعة', 'رقم التشغيلة', 'التشغيلة', 'رقم الباتش', 'الباتش', 'batch', 'batch no',
                    'batch number', 'lot', 'lot no', 'lot number'),
    },
    {
        'field': 'expiry_date', 'header_ar': 'تاريخ الانتهاء (yyyy-mm-dd)', 'required': False, 'dtype': 'date',
        'capability': 'has_expiry_dates', 'typical_width': 20, 'description': 'تاريخ انتهاء الصلاحية',
        'aliases': ('تاريخ الانتهاء', 'الانتهاء', 'تاريخ الصلاحية', 'الصلاحية', 'انتهاء الصلاحية', 'ينتهي في',
                    'expiry', 'expiry date', 'exp', 'exp date', 'expiration', 'expiration date', 'best before'),
    },
]


def _local_pricing_fields(tenant):
    """حقول الأسعار بالعملة المحلية، وعناوينها تحمل رمزها: «سعر البيع (ج.س)»."""
    from apps.core.utils import currency_symbol
    symbol = currency_symbol(tenant.currency or 'SDG')
    fields = []
    for spec in PRICING_FIELDS_NORMAL:
        if spec['field'] in LOCAL_PRICE_FIELDS:
            spec = dict(spec, header_ar=f"{spec['header_ar']} ({symbol})")
            if tenant.hard_currency_mode:
                spec['description'] += ' بالعملة المحلية (الجنيه)'
        fields.append(spec)
    return fields


def template_fields(schema):
    """الحقول التي تُكتب في القالب القابل للتنزيل (بعض الحقول مقبولة عند الاستيراد فقط)."""
    return [s for s in schema if s.get('template', True)]


def get_product_schema(tenant):
    """
    Resolve the full, ordered field list for THIS tenant:
    - prices are always in the local currency; in hard-currency mode the hard-currency price
      columns are also accepted (import only — see PRICING_FIELDS_HC)
    - capability-gated fields depend on tenant.capabilities
    - the `stock` column only appears when the tenant has more than one active stock
    """
    from apps.stocks.models import Stock

    capabilities = getattr(tenant, 'capabilities', None)

    def capability_ok(cap_name):
        if not cap_name:
            return True
        return bool(getattr(capabilities, cap_name, False))

    schema = list(BASIC_FIELDS) + list(CLASSIFICATION_FIELDS)
    schema += _local_pricing_fields(tenant)
    if tenant.hard_currency_mode:
        schema += PRICING_FIELDS_HC
    schema += STOCK_TRACKING_FIELDS
    schema += PHARMA_FIELDS
    schema += OPENING_STOCK_FIELDS

    multi_stock = Stock.objects.for_tenant(tenant).filter(is_active=True).count() > 1

    resolved = []
    for spec in schema:
        if spec['field'] == 'stock' and not multi_stock:
            continue
        if not capability_ok(spec.get('capability')):
            continue
        resolved.append(spec)
    return resolved


# ============================================================
# Customers & Suppliers — much simpler than products: plain fields only,
# no tenant master-data dropdowns and no capability gating, so the schema
# is static and `get_*_schema(tenant)` is a trivial passthrough (kept for
# symmetry with `get_product_schema`, and as the extension point if a
# tenant-specific field is ever needed later).
# ============================================================

CUSTOMER_IMPORT_SCHEMA = [
    {
        'field': 'name', 'header_ar': 'اسم العميل *', 'required': True, 'dtype': 'text',
        'typical_width': 24, 'description': 'اسم العميل أو الزبون',
    },
    {
        'field': 'phone', 'header_ar': 'رقم الهاتف', 'required': False, 'dtype': 'text',
        'typical_width': 16, 'description': 'رقم الهاتف أو الجوال',
    },
    {
        'field': 'email', 'header_ar': 'البريد الإلكتروني', 'required': False, 'dtype': 'text',
        'typical_width': 24, 'description': 'البريد الإلكتروني',
    },
    {
        'field': 'city', 'header_ar': 'المدينة', 'required': False, 'dtype': 'text',
        'typical_width': 16, 'description': 'المدينة أو المنطقة',
    },
    {
        'field': 'address', 'header_ar': 'العنوان', 'required': False, 'dtype': 'text',
        'typical_width': 30, 'description': 'العنوان التفصيلي',
    },
    {
        'field': 'opening_balance', 'header_ar': 'المديونية الافتتاحية', 'required': False, 'dtype': 'decimal',
        'typical_width': 18, 'description': 'الرصيد الافتتاحي المستحق على العميل',
    },
    {
        'field': 'credit_limit', 'header_ar': 'الحد الائتماني', 'required': False, 'dtype': 'decimal',
        'typical_width': 16, 'description': 'أقصى دين مسموح به للعميل',
    },
    {
        'field': 'notes', 'header_ar': 'ملاحظات', 'required': False, 'dtype': 'text',
        'typical_width': 30, 'description': 'ملاحظات',
    },
]

SUPPLIER_IMPORT_SCHEMA = [
    {
        'field': 'name', 'header_ar': 'اسم المورد *', 'required': True, 'dtype': 'text',
        'typical_width': 24, 'description': 'اسم المورد',
    },
    {
        'field': 'phone', 'header_ar': 'رقم الهاتف', 'required': False, 'dtype': 'text',
        'typical_width': 16, 'description': 'رقم الهاتف أو الجوال',
    },
    {
        'field': 'email', 'header_ar': 'البريد الإلكتروني', 'required': False, 'dtype': 'text',
        'typical_width': 24, 'description': 'البريد الإلكتروني',
    },
    {
        'field': 'city', 'header_ar': 'المدينة', 'required': False, 'dtype': 'text',
        'typical_width': 16, 'description': 'المدينة أو المنطقة',
    },
    {
        'field': 'address', 'header_ar': 'العنوان', 'required': False, 'dtype': 'text',
        'typical_width': 30, 'description': 'العنوان التفصيلي',
    },
    {
        'field': 'currency', 'header_ar': 'عملة المورد', 'required': False, 'dtype': 'choice_fixed',
        'typical_width': 16, 'description': 'العملة التي يتعامل بها المورد',
    },
    {
        'field': 'opening_balance', 'header_ar': 'المديونية الافتتاحية', 'required': False, 'dtype': 'decimal',
        'typical_width': 18, 'description': 'الرصيد الافتتاحي المستحق للمورد',
    },
    {
        'field': 'credit_limit', 'header_ar': 'الحد الائتماني', 'required': False, 'dtype': 'decimal',
        'typical_width': 16, 'description': 'أقصى دين مسموح به مع هذا المورد',
    },
    {
        'field': 'notes', 'header_ar': 'ملاحظات', 'required': False, 'dtype': 'text',
        'typical_width': 30, 'description': 'ملاحظات',
    },
]


def get_customer_schema(tenant):
    return list(CUSTOMER_IMPORT_SCHEMA)


def get_supplier_schema(tenant):
    from apps.core.constants import CURRENCY_CHOICES
    schema = list(SUPPLIER_IMPORT_SCHEMA)
    for spec in schema:
        if spec['field'] == 'currency':
            spec['choices'] = CURRENCY_CHOICES
    return schema
