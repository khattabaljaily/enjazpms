"""
Stocks Signals - الربط التلقائي بين المنتجات والمخازن

Signal ١: بعد إنشاء Stock جديد
  → ينشئ StockQuantity (كمية=0) لكل منتجات الـ tenant الموجودة
  → السبب: المخزن الجديد يجب أن "يعرف" بكل المنتجات من البداية

Signal ٢: بعد إنشاء Item جديد
  → ينشئ StockQuantity (كمية=0) في كل مخازن الـ tenant
  → السبب: المنتج الجديد يجب أن يكون متتبَّعاً في كل المخازن فوراً

الفائدة:
  - سواء أضفت المنتجات قبل المخازن أو بعدها، الربط يحصل تلقائياً
  - ما في حاجة لأي تدخل يدوي من المستخدم
  - الكمية دايماً = 0 (تتغير فقط عبر فواتير الشراء/البيع/التحويل)
"""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


@receiver(post_save, sender='stocks.Stock')
def create_quantities_for_new_stock(sender, instance, created, **kwargs):
    """
    بعد إنشاء مخزن جديد:
    أنشئ سجل StockQuantity بكمية=0 لكل منتجات الـ tenant الموجودة.

    يستخدم bulk_create لأداء أفضل (استعلام واحد بدل N استعلام).
    ignore_conflicts=True يمنع الخطأ لو السجل موجود مسبقاً (للأمان).
    """
    if not created:
        return  # تعديل مخزن موجود - لا نفعل شيئاً

    from apps.items.models import Item
    from apps.stocks.models import StockQuantity

    try:
        with transaction.atomic():
            # جلب كل منتجات الـ tenant دفعة واحدة
            items = Item.objects.filter(
                tenant=instance.tenant,
                is_active=True
            ).exclude(
                item_type='service'
            ).only('id', 'tenant_id')

            if not items.exists():
                return  # لا توجد منتجات بعد - لا بأس، سيُنشأ الربط عند إضافة منتجات

            qty_records = [
                StockQuantity(
                    tenant=instance.tenant,
                    stock=instance,
                    item=item,
                    quantity=0,
                    reserved_quantity=0,
                    created_by=instance.created_by,
                    updated_by=instance.created_by,
                )
                for item in items
            ]

            StockQuantity.objects.bulk_create(
                qty_records,
                ignore_conflicts=True  # آمن: لو السجل موجود لا يُعيد إنشاءه
            )

            logger.info(
                f"[Stocks Signal] Created {len(qty_records)} StockQuantity records "
                f"for new stock '{instance.name}' (tenant={instance.tenant_id})"
            )

    except Exception as e:
        # لا نوقف العملية الأصلية لو فشل الـ signal - نسجّل فقط
        logger.error(
            f"[Stocks Signal] Failed to create StockQuantity for stock "
            f"'{instance.name}': {e}"
        )


@receiver(post_save, sender='items.Item')
def create_quantities_for_new_item(sender, instance, created, **kwargs):
    """
    بعد إنشاء منتج جديد:
    أنشئ سجل StockQuantity بكمية=0 في كل مخازن الـ tenant.

    - لو ما في مخازن بعد: لا يُنشأ شيء (سيُنشأ عند إضافة المخزن)
    - لو في مخازن: يُنشأ سجل في كل منها فوراً
    """
    if not created:
        return  # تعديل منتج موجود - لا نفعل شيئاً

    if instance.item_type == 'service':
        return  # الخدمة ليست صنفاً مخزنياً

    from apps.stocks.models import Stock, StockQuantity

    try:
        with transaction.atomic():
            stocks = Stock.objects.filter(
                tenant=instance.tenant,
                is_active=True
            ).only('id', 'tenant_id')

            if not stocks.exists():
                return  # لا توجد مخازن بعد - لا بأس

            qty_records = [
                StockQuantity(
                    tenant=instance.tenant,
                    stock=stock,
                    item=instance,
                    quantity=0,
                    reserved_quantity=0,
                    created_by=instance.created_by,
                    updated_by=instance.created_by,
                )
                for stock in stocks
            ]

            StockQuantity.objects.bulk_create(
                qty_records,
                ignore_conflicts=True
            )

            logger.info(
                f"[Items Signal] Created {len(qty_records)} StockQuantity records "
                f"for new item '{instance.name}' (tenant={instance.tenant_id})"
            )

    except Exception as e:
        logger.error(
            f"[Items Signal] Failed to create StockQuantity for item "
            f"'{instance.name}': {e}"
        )


from apps.core.models import tenant_deletion_in_progress


@receiver(pre_delete, sender='stocks.Stock')
def prevent_deleting_system_default_stock(sender, instance, **kwargs):
    if getattr(instance, 'is_system_default', False) and not tenant_deletion_in_progress.get():
        raise ValidationError('لا يمكن حذف المخزن الافتراضي النظامي.')
