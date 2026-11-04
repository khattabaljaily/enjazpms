"""
إخفاء أسعار التكلفة والشراء والأرباح عمّن لا يملك صلاحية «view_cost_prices».

- مدير النشاط (والمشرف العام) يرى كل شيء دائماً.
- بقية المستخدمين (ومنهم مشرف الفرع) يرونها فقط إذا مُنحوا الصلاحية في مجموعة صلاحيات.
- استثناء المشتريات: من يملك صلاحيات المشتريات يرى أسعار الشراء داخل مستندات الشراء
  (فواتير/أوامر/مرتجعات الشراء وتقاريرها)، لأنه لا يستطيع إدخالها بدونها.
"""

COST_PERMISSION = 'view_cost_prices'

PURCHASE_PRICE_KEYS = (
    'add_purchases', 'change_purchases', 'view_purchases',
    'add_purchase_returns', 'view_purchase_returns',
    'add_central_purchases', 'change_central_purchases', 'view_central_purchases',
    'view_central_purchase_returns', 'add_central_purchase_returns',
)

# حقول تُحذف من ردود JSON لمن لا يرى التكلفة
COST_FIELDS = (
    'cost_price', 'cost_price_hc', 'cost_price_snapshot', 'unit_cost', 'unit_cost_snapshot',
    'cost', 'total_cost', 'cogs', 'profit', 'gross_profit', 'margin', 'margin_percent',
)


def can_view_cost(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.has_perm_key(COST_PERMISSION))


def can_view_purchase_prices(user):
    """أسعار الشراء داخل شاشات الشراء: صلاحية التكلفة أو أي صلاحية مشتريات."""
    if can_view_cost(user):
        return True
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    return any(user.has_perm_key(k) for k in PURCHASE_PRICE_KEYS)


def strip_cost(data, fields=COST_FIELDS):
    """يحذف حقول التكلفة من dict أو list of dicts (نسخة جديدة؛ لا يعدّل الأصل)."""
    if isinstance(data, list):
        return [strip_cost(row, fields) for row in data]
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k not in fields}
    return data
