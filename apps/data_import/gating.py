"""
Prerequisite checks before a tenant may import products.

The product-import xlsx template builds a real Excel dropdown for التصنيف
from the tenant's existing Category rows — an empty dropdown defeats the
whole point, so the feature is blocked until at least one exists.

Units are NOT a shared/tenant-level list — a product's units (single, or a
smaller+larger pair) are defined per-product directly in the import row,
exactly like the manual "add product" form does via ItemUnit. So there is
no equivalent prerequisite for units.
"""
from apps.items.models import Category

BLOCKED_MESSAGES = {
    'category': 'لا يمكن استيراد المنتجات قبل إضافة تصنيف واحد على الأقل، حتى تظهر التصنيفات كقائمة اختيار في ملف الاستيراد.',
}


def products_import_blocked_reason(tenant):
    """Return 'category', or None if the tenant may proceed."""
    if not Category.objects.for_tenant(tenant).filter(is_active=True).exists():
        return 'category'
    return None
