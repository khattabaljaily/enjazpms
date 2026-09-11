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
"""
from apps.items.models import Item


BASIC_FIELDS = [
    {
        'field': 'name', 'header_ar': 'اسم المنتج *', 'required': True, 'dtype': 'text',
        'capability': None, 'typical_width': 30,
        'description': 'اسم المنتج أو الصنف أو السلعة',
    },
    {
        'field': 'name_en', 'header_ar': 'الاسم بالإنجليزية', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 26,
        'description': 'الاسم بالإنجليزية للمنتج',
    },
    {
        'field': 'sku', 'header_ar': 'الرمز (SKU) — اتركه فارغاً للتوليد التلقائي', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 22,
        'description': 'رمز المنتج SKU',
    },
    {
        'field': 'barcode', 'header_ar': 'الباركود', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 18,
        'description': 'الباركود أو رمز الشريط',
    },
    {
        'field': 'item_type', 'header_ar': 'نوع الصنف', 'required': False, 'dtype': 'choice_fixed',
        'choices': list(Item.ITEM_TYPE_CHOICES), 'capability': None, 'typical_width': 14,
        'description': 'نوع الصنف: منتج، خدمة، مادة خام...',
    },
    {
        'field': 'description', 'header_ar': 'الوصف', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 40,
        'description': 'وصف المنتج',
    },
]

CLASSIFICATION_FIELDS = [
    {
        'field': 'category', 'header_ar': 'التصنيف', 'required': False, 'dtype': 'choice_tenant',
        'tenant_source': 'category', 'capability': None, 'typical_width': 22,
        'description': 'التصنيف أو القسم أو الفئة',
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
    },
    {
        'field': 'large_unit_name', 'header_ar': 'اسم وحدة أكبر (اختياري)', 'required': False, 'dtype': 'text',
        'capability': None, 'typical_width': 20,
        'description': 'اسم وحدة أكبر تحتوي على عدة وحدات أساسية، مثل كرتون أو طرد',
    },
    {
        'field': 'large_unit_count', 'header_ar': 'كم وحدة أساسية داخل الوحدة الأكبر؟', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 18,
        'description': 'عدد الوحدات الأساسية داخل الوحدة الأكبر، مطلوب فقط إذا حُددت وحدة أكبر',
    },
]

PRICING_FIELDS_NORMAL = [
    {
        'field': 'cost_price', 'header_ar': 'سعر التكلفة', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 14, 'description': 'سعر التكلفة أو سعر الشراء',
    },
    {
        'field': 'selling_price', 'header_ar': 'سعر البيع', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 14, 'description': 'سعر البيع أو السعر',
    },
    {
        'field': 'min_selling_price', 'header_ar': 'أدنى سعر بيع', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 14, 'description': 'أدنى سعر بيع مسموح',
    },
    {
        'field': 'tax_rate', 'header_ar': 'نسبة الضريبة %', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 14, 'description': 'نسبة الضريبة',
    },
]

PRICING_FIELDS_HC = [
    {
        'field': 'cost_price_hc', 'header_ar': 'سعر التكلفة (عملة صعبة)', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 20, 'description': 'سعر التكلفة بالعملة الصعبة',
    },
    {
        'field': 'selling_price_hc', 'header_ar': 'سعر البيع (عملة صعبة)', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 20, 'description': 'سعر البيع بالعملة الصعبة',
    },
    {
        'field': 'min_selling_price_hc', 'header_ar': 'أدنى سعر بيع (عملة صعبة)', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 22, 'description': 'أدنى سعر بيع بالعملة الصعبة',
    },
]

STOCK_TRACKING_FIELDS = [
    {
        'field': 'min_quantity', 'header_ar': 'الحد الأدنى للمخزون', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 16, 'description': 'الحد الأدنى للمخزون قبل التنبيه',
    },
    {
        'field': 'max_quantity', 'header_ar': 'الحد الأقصى للمخزون', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 16, 'description': 'الحد الأقصى للمخزون',
    },
    {
        'field': 'track_expiry', 'header_ar': 'تتبع تاريخ الانتهاء؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_expiry_dates', 'typical_width': 12, 'description': 'هل يتم تتبع تاريخ انتهاء الصلاحية',
    },
    {
        'field': 'track_batch', 'header_ar': 'تتبع رقم الدفعة؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_batch_numbers', 'typical_width': 12, 'description': 'هل يتم تتبع رقم الدفعة',
    },
    {
        'field': 'track_serial', 'header_ar': 'تتبع الرقم التسلسلي؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_serial_numbers', 'typical_width': 12, 'description': 'هل يتم تتبع الرقم التسلسلي',
    },
    {
        'field': 'is_sellable', 'header_ar': 'قابل للبيع؟', 'required': False, 'dtype': 'bool',
        'capability': None, 'typical_width': 12, 'description': 'هل المنتج قابل للبيع',
    },
    {
        'field': 'is_purchasable', 'header_ar': 'قابل للشراء؟', 'required': False, 'dtype': 'bool',
        'capability': None, 'typical_width': 12, 'description': 'هل المنتج قابل للشراء',
    },
]

PHARMA_FIELDS = [
    {
        'field': 'generic_name', 'header_ar': 'الاسم العلمي / المادة الفعالة', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 28, 'description': 'الاسم العلمي أو المادة الفعالة',
    },
    {
        'field': 'manufacturer', 'header_ar': 'الشركة المصنعة', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 22, 'description': 'الشركة المصنعة',
    },
    {
        'field': 'dosage_form', 'header_ar': 'الشكل الصيدلاني', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 18,
        'description': 'الشكل الصيدلاني مثل أقراص أو شراب أو حقن',
    },
    {
        'field': 'strength', 'header_ar': 'التركيز', 'required': False, 'dtype': 'text',
        'capability': 'has_drug_classification', 'typical_width': 14, 'description': 'التركيز مثل 500 مجم',
    },
    {
        'field': 'requires_prescription', 'header_ar': 'يُصرف بوصفة طبية؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_drug_classification', 'typical_width': 14, 'description': 'هل يُصرف بوصفة طبية',
    },
    {
        'field': 'is_controlled_substance', 'header_ar': 'خاضع للرقابة / مخدرات؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_drug_classification', 'typical_width': 16, 'description': 'هل هو خاضع للرقابة',
    },
    {
        'field': 'is_insurance_excluded', 'header_ar': 'غير مغطى بالتأمين؟', 'required': False, 'dtype': 'bool',
        'capability': 'has_insurance_billing', 'typical_width': 14, 'description': 'هل هو غير مغطى بالتأمين',
    },
]

OPENING_STOCK_FIELDS = [
    {
        'field': 'opening_quantity', 'header_ar': 'الكمية الافتتاحية', 'required': False, 'dtype': 'decimal',
        'capability': None, 'typical_width': 16, 'description': 'الكمية الافتتاحية عند الاستيراد',
    },
    {
        'field': 'stock', 'header_ar': 'المخزن', 'required': False, 'dtype': 'choice_tenant',
        'tenant_source': 'stock', 'capability': None, 'typical_width': 18,
        'description': 'المخزن الذي تُضاف إليه الكمية الافتتاحية',
    },
    {
        'field': 'batch_number', 'header_ar': 'رقم الدفعة', 'required': False, 'dtype': 'text',
        'capability': 'has_batch_numbers', 'typical_width': 16, 'description': 'رقم الدفعة',
    },
    {
        'field': 'expiry_date', 'header_ar': 'تاريخ الانتهاء (yyyy-mm-dd)', 'required': False, 'dtype': 'date',
        'capability': 'has_expiry_dates', 'typical_width': 20, 'description': 'تاريخ انتهاء الصلاحية',
    },
]


def get_product_schema(tenant):
    """
    Resolve the full, ordered field list for THIS tenant:
    - pricing section depends on tenant.hard_currency_mode
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
    schema += PRICING_FIELDS_HC if tenant.hard_currency_mode else PRICING_FIELDS_NORMAL
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
