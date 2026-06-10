"""
Activity Log Middleware
Automatically logs every mutating request for authenticated tenant users.
Maps URL view_name → (Arabic title, action_type).
Also runs rich detail builders for key financial/stock operations.
Never raises — logging must never break the main request/response cycle.
"""

# ─── URL → (title, action_type) map ───────────────────────────────────────────

ACTIVITY_MAP = {
    # ── Sales ──────────────────────────────────────────────────────────────────
    'sales:invoice_create':     ('إنشاء فاتورة مبيعات',             'create'),
    'sales:invoice_edit':       ('تعديل فاتورة مبيعات',             'update'),
    'sales:invoice_confirm':    ('تأكيد فاتورة مبيعات',             'confirm'),
    'sales:invoice_cancel':     ('إلغاء فاتورة مبيعات',             'cancel'),
    'sales:invoice_deliver':    ('تسليم فاتورة مبيعات',             'confirm'),
    'sales:invoice_delete_draft': ('حذف مسودة فاتورة مبيعات',       'delete'),
    'sales:record_payment':     ('تسجيل دفعة على فاتورة مبيعات',    'create'),
    'sales:return_create':      ('إنشاء مرتجع مبيعات',              'create'),
    'sales:return_confirm':     ('تأكيد مرتجع مبيعات',              'confirm'),
    'sales:return_cancel':      ('إلغاء مرتجع مبيعات',              'cancel'),
    'sales:quote_create':       ('إنشاء عرض سعر',                    'create'),
    'sales:quote_edit':         ('تعديل عرض سعر',                    'update'),
    'sales:quote_send':         ('إرسال عرض سعر للعميل',             'update'),
    'sales:quote_accept':       ('قبول عرض سعر',                     'confirm'),
    'sales:quote_reject':       ('رفض عرض سعر',                      'cancel'),
    'sales:quote_cancel':       ('إلغاء عرض سعر',                    'cancel'),
    'sales:quote_convert':      ('تحويل عرض سعر إلى فاتورة',         'confirm'),
    'sales:quote_delete_draft': ('حذف مسودة عرض سعر',               'delete'),
    'sales:pos_checkout_api':   ('إتمام عملية بيع من نقطة البيع',   'create'),

    # ── Purchases ──────────────────────────────────────────────────────────────
    'purchases:order_create':      ('إنشاء أمر شراء',                 'create'),
    'purchases:order_edit':        ('تعديل أمر شراء',                 'update'),
    'purchases:order_confirm':     ('تأكيد أمر شراء',                 'confirm'),
    'purchases:order_cancel':      ('إلغاء أمر شراء',                 'cancel'),
    'purchases:return_create':     ('إنشاء مرتجع مشتريات',            'create'),
    'purchases:return_confirm':    ('تأكيد مرتجع مشتريات',            'confirm'),
    'purchases:return_cancel':     ('إلغاء مرتجع مشتريات',            'cancel'),
    'purchases:rfq_create':        ('إنشاء طلب عرض سعر (RFQ)',        'create'),
    'purchases:rfq_send':          ('إرسال طلب عرض سعر للمورد',       'update'),
    'purchases:rfq_receive':       ('استلام رد على طلب عرض سعر',      'update'),
    'purchases:rfq_accept':        ('قبول عرض سعر المورد',            'confirm'),
    'purchases:rfq_reject':        ('رفض عرض سعر المورد',             'cancel'),
    'purchases:rfq_cancel':        ('إلغاء طلب عرض سعر',             'cancel'),
    'purchases:rfq_convert':       ('تحويل طلب عرض سعر إلى أمر شراء', 'confirm'),
    'purchases:payments_create':   ('تسجيل دفعة لمورد',               'create'),
    'purchases:payment_cancel':    ('إلغاء دفعة مورد',                'cancel'),

    # ── Customers ──────────────────────────────────────────────────────────────
    'customers:create_api':       ('إضافة عميل جديد',               'create'),
    'customers:create':           ('إضافة عميل جديد',               'create'),
    'customers:update_api':       ('تعديل بيانات عميل',             'update'),
    'customers:delete_api':       ('حذف عميل',                       'delete'),
    'customers:import_api':       ('استيراد عملاء من ملف',           'create'),
    'customers:payments_create':  ('تسجيل دفعة من عميل',            'create'),
    'customers:payment_cancel':   ('إلغاء دفعة عميل',               'cancel'),

    # ── Suppliers ──────────────────────────────────────────────────────────────
    'suppliers:create_api':       ('إضافة مورد جديد',               'create'),
    'suppliers:create':           ('إضافة مورد جديد',               'create'),
    'suppliers:update_api':       ('تعديل بيانات مورد',             'update'),
    'suppliers:delete_api':       ('حذف مورد',                       'delete'),
    'suppliers:import_api':       ('استيراد موردين من ملف',          'create'),
    'suppliers:payments_create':  ('تسجيل دفعة للمورد',             'create'),
    'suppliers:payment_cancel':   ('إلغاء دفعة مورد',               'cancel'),

    # ── Items ──────────────────────────────────────────────────────────────────
    'items:create_api':            ('إضافة منتج جديد',              'create'),
    'items:update_api':            ('تعديل بيانات منتج',            'update'),
    'items:delete_api':            ('حذف منتج',                      'delete'),
    'items:import_api':            ('استيراد منتجات من ملف',        'create'),
    'items:category_create_api':   ('إضافة تصنيف جديد',            'create'),
    'items:category_update_api':   ('تعديل تصنيف',                  'update'),
    'items:category_delete_api':   ('حذف تصنيف',                    'delete'),
    'items:unit_create_api':       ('إضافة وحدة قياس',              'create'),
    'items:unit_update_api':       ('تعديل وحدة قياس',              'update'),
    'items:unit_delete_api':       ('حذف وحدة قياس',                'delete'),
    'items:bom_create_ajax':       ('إنشاء وصفة تصنيع',             'create'),
    'items:bom_delete':            ('حذف وصفة تصنيع',               'delete'),
    'items:category_import_api':   ('استيراد تصنيفات من ملف',       'create'),

    # ── Stocks ─────────────────────────────────────────────────────────────────
    'stocks:create_api':           ('إنشاء مخزن جديد',              'create'),
    'stocks:update_api':           ('تعديل مخزن',                    'update'),
    'stocks:delete_api':           ('حذف مخزن',                      'delete'),
    'stocks:set_default_api':      ('تعيين المخزن الافتراضي',        'update'),
    'stocks:opening_save_api':     ('حفظ الأرصدة الافتتاحية',        'update'),
    'stocks:transfer_create':      ('إنشاء تحويل مخزون',            'create'),
    'stocks:transfer_confirm':     ('تأكيد تحويل مخزون',            'confirm'),
    'stocks:transfer_cancel':      ('إلغاء تحويل مخزون',            'cancel'),
    'stocks:transfer_delete':      ('حذف تحويل مخزون',              'delete'),
    'stocks:stocktake_create':     ('إنشاء جرد مخزون',              'create'),
    'stocks:stocktake_save_counts': ('حفظ نتائج جرد المخزون',        'update'),
    'stocks:stocktake_confirm':    ('تأكيد جرد المخزون',            'confirm'),
    'stocks:stocktake_cancel':     ('إلغاء جرد المخزون',            'cancel'),
    'stocks:manufacturing_create': ('إنشاء أمر تصنيع',              'create'),

    # ── Expenses ───────────────────────────────────────────────────────────────
    'expenses:create_api':         ('إضافة مصروف جديد',            'create'),
    'expenses:edit_api':           ('تعديل مصروف',                  'update'),
    'expenses:confirm':            ('تأكيد مصروف',                  'confirm'),
    'expenses:cancel':             ('إلغاء مصروف',                  'cancel'),
    'expenses:category_create_api': ('إضافة فئة مصروفات',          'create'),

    # ── Treasury ───────────────────────────────────────────────────────────────
    'treasury:create_api':         ('إضافة خزينة جديدة',           'create'),
    'treasury:update_api':         ('تعديل خزينة',                  'update'),
    'treasury:delete_api':         ('حذف خزينة',                    'delete'),

    # ── Accounts ───────────────────────────────────────────────────────────────
    'accounts:user_create_api':              ('إضافة مستخدم جديد',            'create'),
    'accounts:user_update_api':              ('تعديل بيانات مستخدم',          'update'),
    'accounts:user_delete_api':              ('حذف مستخدم',                    'delete'),
    'accounts:permission_group_create_api':  ('إنشاء مجموعة صلاحيات',         'create'),
    'accounts:permission_group_update_api':  ('تعديل مجموعة صلاحيات',         'update'),
    'accounts:permission_group_delete_api':  ('حذف مجموعة صلاحيات',           'delete'),

    # ── Core / Settings ────────────────────────────────────────────────────────
    'core:tenant_settings':             ('تعديل إعدادات النشاط التجاري',   'update'),
    'core:tenant_settings_update_api':  ('تعديل إعدادات النشاط التجاري',   'update'),
    'core:tenant_support_create':       ('فتح تذكرة دعم فني جديدة',         'create'),

    # ── Store ──────────────────────────────────────────────────────────────────
    'store:manage_settings':      ('تعديل إعدادات المتجر الإلكتروني',  'update'),
    'store:manage_order_approve': ('الموافقة على طلب من المتجر',        'confirm'),
    'store:manage_order_reject':  ('رفض طلب من المتجر',                 'cancel'),
}


# ─── Rich detail builders ─────────────────────────────────────────────────────

def _sale_invoice_details(kwargs):
    pk = kwargs.get('pk') or kwargs.get('invoice_pk')
    if not pk:
        return ''
    try:
        from apps.sales.models import SaleInvoice
        inv = SaleInvoice.objects.select_related('customer', 'stock').get(pk=pk)
        customer = inv.customer.name if inv.customer else 'بدون عميل'
        return (
            f"الفاتورة: {inv.invoice_number}\n"
            f"العميل: {customer}\n"
            f"المخزن: {inv.stock.name}\n"
            f"الإجمالي: {inv.grand_total}"
        )
    except Exception:
        return f"معرف الفاتورة: {pk}"


def _sale_return_details(kwargs):
    pk = kwargs.get('pk') or kwargs.get('return_pk')
    if not pk:
        return ''
    try:
        from apps.sales.models import SaleReturn
        ret = SaleReturn.objects.select_related('customer', 'invoice').get(pk=pk)
        customer = ret.customer.name if ret.customer else '—'
        inv_num = ret.invoice.invoice_number if ret.invoice else '—'
        return (
            f"المرتجع: {ret.return_number}\n"
            f"الفاتورة الأصلية: {inv_num}\n"
            f"العميل: {customer}\n"
            f"المبلغ المسترد: {ret.total_returned}"
        )
    except Exception:
        return f"معرف المرتجع: {pk}"


def _sale_quote_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.sales.models import SaleQuote
        q = SaleQuote.objects.select_related('customer').get(pk=pk)
        customer = q.customer.name if q.customer else 'بدون عميل'
        return (
            f"عرض السعر: {q.quote_number}\n"
            f"العميل: {customer}\n"
            f"الإجمالي: {q.grand_total}"
        )
    except Exception:
        return f"معرف عرض السعر: {pk}"


def _purchase_order_details(kwargs):
    pk = kwargs.get('pk') or kwargs.get('invoice_pk')
    if not pk:
        return ''
    try:
        from apps.purchases.models import PurchaseInvoice
        inv = PurchaseInvoice.objects.select_related('supplier', 'stock').get(pk=pk)
        supplier = inv.supplier.name if inv.supplier else 'بدون مورد'
        return (
            f"أمر الشراء: {inv.invoice_number}\n"
            f"المورد: {supplier}\n"
            f"المخزن: {inv.stock.name}\n"
            f"الإجمالي: {inv.grand_total}"
        )
    except Exception:
        return f"معرف أمر الشراء: {pk}"


def _purchase_rfq_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.purchases.models import PurchaseRFQ
        rfq = PurchaseRFQ.objects.select_related('supplier').get(pk=pk)
        supplier = rfq.supplier.name if rfq.supplier else '—'
        return f"طلب عرض السعر: {rfq.rfq_number}\nالمورد: {supplier}"
    except Exception:
        return f"معرف الطلب: {pk}"


def _purchase_return_details(kwargs):
    pk = kwargs.get('pk') or kwargs.get('return_pk')
    if not pk:
        return ''
    try:
        from apps.purchases.models import PurchaseReturn
        ret = PurchaseReturn.objects.select_related('supplier', 'invoice').get(pk=pk)
        supplier = ret.supplier.name if ret.supplier else '—'
        inv_num = ret.invoice.invoice_number if ret.invoice else '—'
        return (
            f"المرتجع: {ret.return_number}\n"
            f"أمر الشراء: {inv_num}\n"
            f"المورد: {supplier}\n"
            f"المبلغ المسترد: {ret.total_returned}"
        )
    except Exception:
        return f"معرف المرتجع: {pk}"


def _customer_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.customers.models import Customer
        c = Customer.objects.get(pk=pk)
        return f"العميل: {c.name}\nرقم الهاتف: {c.phone or '—'}"
    except Exception:
        return f"معرف العميل: {pk}"


def _customer_payment_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.customers.models import CustomerPayment
        p = CustomerPayment.objects.select_related('customer').get(pk=pk)
        return f"العميل: {p.customer.name}\nالمبلغ: {p.amount}"
    except Exception:
        return f"معرف الدفعة: {pk}"


def _supplier_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.suppliers.models import Supplier
        s = Supplier.objects.get(pk=pk)
        return f"المورد: {s.name}\nرقم الهاتف: {s.phone or '—'}"
    except Exception:
        return f"معرف المورد: {pk}"


def _supplier_payment_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.suppliers.models import SupplierPayment
        p = SupplierPayment.objects.select_related('supplier').get(pk=pk)
        return f"المورد: {p.supplier.name}\nالمبلغ: {p.amount}"
    except Exception:
        return f"معرف الدفعة: {pk}"


def _item_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.items.models import Item
        item = Item.objects.get(pk=pk)
        return f"المنتج: {item.name}\nكود: {item.sku or '—'}"
    except Exception:
        return f"معرف المنتج: {pk}"


def _stock_transfer_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.stocks.models import StockTransfer
        t = StockTransfer.objects.select_related('from_stock', 'to_stock').get(pk=pk)
        return (
            f"التحويل: {t.transfer_number}\n"
            f"من: {t.from_stock.name}\n"
            f"إلى: {t.to_stock.name}"
        )
    except Exception:
        return f"معرف التحويل: {pk}"


def _stocktake_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.stocks.models import Stocktake
        s = Stocktake.objects.select_related('stock').get(pk=pk)
        return f"الجرد: {s.reference}\nالمخزن: {s.stock.name}"
    except Exception:
        return f"معرف الجرد: {pk}"


def _manufacturing_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.stocks.models import ManufacturingOrder
        mo = ManufacturingOrder.objects.select_related('item', 'stock').get(pk=pk)
        return (
            f"أمر التصنيع: {mo.order_number}\n"
            f"المنتج: {mo.item.name}\n"
            f"الكمية: {mo.quantity}"
        )
    except Exception:
        return f"معرف أمر التصنيع: {pk}"


def _expense_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.expenses.models import Expense
        e = Expense.objects.select_related('category').get(pk=pk)
        cat = e.category.name if e.category else '—'
        return f"المصروف: {e.description or '—'}\nالفئة: {cat}\nالمبلغ: {e.amount}"
    except Exception:
        return f"معرف المصروف: {pk}"


def _user_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.accounts.models import User
        u = User.objects.get(pk=pk)
        return f"المستخدم: {u.get_full_name()}\nاسم الدخول: {u.username}"
    except Exception:
        return f"معرف المستخدم: {pk}"


def _group_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.accounts.models import PermissionGroup
        g = PermissionGroup.objects.get(pk=pk)
        return f"المجموعة: {g.name}"
    except Exception:
        return f"معرف المجموعة: {pk}"


def _store_order_details(kwargs):
    pk = kwargs.get('pk')
    if not pk:
        return ''
    try:
        from apps.store.models import StoreOrder
        o = StoreOrder.objects.get(pk=pk)
        return f"طلب المتجر: #{o.order_number}\nالعميل: {o.customer_name or '—'}\nالإجمالي: {o.total}"
    except Exception:
        return f"معرف الطلب: {pk}"


# Map: url_name → detail builder callable or None
DETAIL_BUILDERS = {
    'sales:invoice_create':      None,
    'sales:invoice_edit':        _sale_invoice_details,
    'sales:invoice_confirm':     _sale_invoice_details,
    'sales:invoice_cancel':      _sale_invoice_details,
    'sales:invoice_deliver':     _sale_invoice_details,
    'sales:invoice_delete_draft': _sale_invoice_details,
    'sales:record_payment':      _sale_invoice_details,
    'sales:return_create':       None,
    'sales:return_confirm':      _sale_return_details,
    'sales:return_cancel':       _sale_return_details,
    'sales:quote_create':        None,
    'sales:quote_edit':          _sale_quote_details,
    'sales:quote_send':          _sale_quote_details,
    'sales:quote_accept':        _sale_quote_details,
    'sales:quote_reject':        _sale_quote_details,
    'sales:quote_cancel':        _sale_quote_details,
    'sales:quote_convert':       _sale_quote_details,
    'sales:quote_delete_draft':  _sale_quote_details,
    'purchases:order_create':    None,
    'purchases:order_edit':      _purchase_order_details,
    'purchases:order_confirm':   _purchase_order_details,
    'purchases:order_cancel':    _purchase_order_details,
    'purchases:return_create':   None,
    'purchases:return_confirm':  _purchase_return_details,
    'purchases:return_cancel':   _purchase_return_details,
    'purchases:rfq_create':      None,
    'purchases:rfq_send':        _purchase_rfq_details,
    'purchases:rfq_receive':     _purchase_rfq_details,
    'purchases:rfq_accept':      _purchase_rfq_details,
    'purchases:rfq_reject':      _purchase_rfq_details,
    'purchases:rfq_cancel':      _purchase_rfq_details,
    'purchases:rfq_convert':     _purchase_rfq_details,
    'purchases:payments_create': None,
    'purchases:payment_cancel':  _supplier_payment_details,
    'customers:update_api':      _customer_details,
    'customers:delete_api':      _customer_details,
    'customers:payment_cancel':  _customer_payment_details,
    'suppliers:update_api':      _supplier_details,
    'suppliers:delete_api':      _supplier_details,
    'suppliers:payment_cancel':  _supplier_payment_details,
    'items:update_api':          _item_details,
    'items:delete_api':          _item_details,
    'stocks:transfer_confirm':   _stock_transfer_details,
    'stocks:transfer_cancel':    _stock_transfer_details,
    'stocks:transfer_delete':    _stock_transfer_details,
    'stocks:stocktake_confirm':  _stocktake_details,
    'stocks:stocktake_cancel':   _stocktake_details,
    'stocks:manufacturing_create': None,
    'expenses:edit_api':         _expense_details,
    'expenses:confirm':          _expense_details,
    'expenses:cancel':           _expense_details,
    'accounts:user_update_api':             _user_details,
    'accounts:user_delete_api':             _user_details,
    'accounts:permission_group_update_api': _group_details,
    'accounts:permission_group_delete_api': _group_details,
    'store:manage_order_approve': _store_order_details,
    'store:manage_order_reject':  _store_order_details,
}


# ─── Middleware ────────────────────────────────────────────────────────────────

class ActivityLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        try:
            self._maybe_log(request, response)
        except Exception:
            pass

        return response

    def _maybe_log(self, request, response):
        if getattr(request, '_activity_logged', False):
            return  # Already logged explicitly by the view

        if request.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            return

        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return

        tenant = getattr(request, 'tenant', None)
        if not tenant:
            return

        if response.status_code >= 400:
            return

        resolver = getattr(request, 'resolver_match', None)
        if not resolver:
            return

        url_name = resolver.view_name
        entry = ACTIVITY_MAP.get(url_name)
        if not entry:
            return

        title, action_type = entry

        kwargs = resolver.kwargs or {}
        builder = DETAIL_BUILDERS.get(url_name)
        details = builder(kwargs) if builder else ''

        from .activity_service import log_activity
        log_activity(request, title, details, action_type)
