"""
منطق FEFO (الأقرب انتهاءً أولاً) لخصم/إعادة الدفعات (ItemBatch) عند البيع.

هذا الملف — وليس apps/stocks — هو المسؤول عن تتبع أي دفعة بيعت لأي سطر
فاتورة، لأن الربط الطبيعي هو بين SaleInvoiceLine و ItemBatch (سطر الفاتورة
هو الحدث الذي يستهلك الدفعة). apps/stocks/services.py يبقى المصدر الوحيد
لتعديل StockQuantity نفسها؛ هذا الملف لا يلمسها أبداً — فقط ItemBatch.quantity_remaining
وسجلات التخصيص.

نقاط الدخول المستخدمة من apps/sales/services.py:
  consume_fefo(tenant, stock, item, line, qty)
      عند خصم فعلي من المخزون (بيع فوري أو تسليم مؤجل).
  reverse_all_for_line(line)
      عند إلغاء/تعديل فاتورة كاملة (لا مرتجعات مؤكدة عليها بحكم القيود
      الموجودة في confirm/edit/cancel في services.py).
  restore_for_return_line(return_line, qty)
      عند تأكيد مرتجع — يُعيد qty لدفعات سطر الفاتورة الأصلي (الأقدم
      استهلاكاً أولاً) ويسجّل من أي دفعة أُعيدت.
  reconsume_for_return_line(return_line)
      عند إلغاء مرتجع مؤكد — يعكس بالضبط ما فعله restore_for_return_line
      لنفس سطر المرتجع.
"""
from decimal import Decimal

from django.db.models import F


def is_batch_tracked(item):
    """هل هذا الصنف يتتبع دفعات و/أو تواريخ صلاحية؟"""
    return bool(getattr(item, 'track_batch', False) or getattr(item, 'track_expiry', False))


def consume_fefo(tenant, stock, item, line, qty):
    """
    يخصم qty من ItemBatch للصنف/المخزن، بترتيب الأقرب انتهاءً أولاً، ويسجّل
    SaleInvoiceLineBatchAllocation لكل دفعة استُهلكت.

    أي جزء من qty لا تغطيه دفعات مسجَّلة (رصيد افتتاحي / إدخال يدوي قبل
    تفعيل التتبع / شراء بلا بيانات دفعة) يُخصم كـ"غير مرتبط بدفعة"
    (batch=None) — لا تُرفض عملية البيع بسبب فجوة توثيق تاريخية، لأن
    StockQuantity (المصدر الوحيد لحقيقة الكمية الإجمالية) هو من يضمن أصلاً
    وجود كمية كافية قبل استدعاء هذه الدالة.
    """
    from apps.items.models import ItemBatch
    from .models import SaleInvoiceLineBatchAllocation

    if not is_batch_tracked(item):
        return

    remaining = Decimal(qty)
    if remaining <= 0:
        return

    batches = (
        ItemBatch.objects
        .select_for_update()
        .filter(tenant=tenant, item=item, stock=stock, quantity_remaining__gt=0)
        .order_by(F('expiry_date').asc(nulls_last=True), 'batch_number', 'id')
    )
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.quantity_remaining, remaining)
        if take <= 0:
            continue
        batch.quantity_remaining -= take
        batch.save(update_fields=['quantity_remaining'])
        SaleInvoiceLineBatchAllocation.objects.create(
            tenant=tenant, sale_line=line, batch=batch,
            batch_number_snapshot=batch.batch_number, expiry_date_snapshot=batch.expiry_date,
            quantity=take,
        )
        remaining -= take

    if remaining > 0:
        SaleInvoiceLineBatchAllocation.objects.create(
            tenant=tenant, sale_line=line, batch=None,
            batch_number_snapshot='', expiry_date_snapshot=None,
            quantity=remaining,
        )


def reverse_all_for_line(line):
    """
    يُعيد كامل الكمية القائمة (غير المُرتجَعة) لسطر فاتورة إلى دفعاتها
    الأصلية، ويحذف سجلات التخصيص. يُستخدم عند إلغاء/تعديل فاتورة مؤكدة —
    مسموح فقط عندما لا توجد مرتجعات مؤكدة على الفاتورة (يُتحقق من هذا في
    apps/sales/services.py قبل الاستدعاء)، فكل التخصيصات القائمة تمثّل
    الكمية المخصومة أصلاً بالكامل.
    """
    from apps.items.models import ItemBatch

    allocations = list(line.batch_allocations.select_for_update().select_related('batch'))
    for alloc in allocations:
        if alloc.batch_id:
            batch = ItemBatch.objects.select_for_update().get(pk=alloc.batch_id)
            batch.quantity_remaining += alloc.quantity
            batch.save(update_fields=['quantity_remaining'])
    line.batch_allocations.all().delete()


def restore_for_return_line(return_line, qty):
    """
    يُعيد qty إلى دفعات سطر الفاتورة الأصلي (invoice_line)، بترتيب
    الاستهلاك (الأقدم تخصيصاً أولاً)، ينقص/يحذف SaleInvoiceLineBatchAllocation
    المطابقة، ويسجّل SaleReturnLineBatchRestoration لكل دفعة أُعيد إليها
    شيء — لتُستخدم لاحقاً عند إلغاء هذا المرتجع تحديداً.
    """
    from apps.items.models import ItemBatch
    from .models import SaleReturnLineBatchRestoration

    invoice_line = return_line.invoice_line
    if not is_batch_tracked(invoice_line.item):
        return

    remaining = Decimal(qty)
    if remaining <= 0:
        return

    allocations = list(
        invoice_line.batch_allocations.select_for_update().select_related('batch').order_by('id')
    )
    for alloc in allocations:
        if remaining <= 0:
            break
        give_back = min(alloc.quantity, remaining)
        if give_back <= 0:
            continue

        if alloc.batch_id:
            batch = ItemBatch.objects.select_for_update().get(pk=alloc.batch_id)
            batch.quantity_remaining += give_back
            batch.save(update_fields=['quantity_remaining'])

        SaleReturnLineBatchRestoration.objects.create(
            tenant=return_line.tenant, return_line=return_line,
            batch_id=alloc.batch_id, quantity=give_back,
        )

        alloc.quantity -= give_back
        if alloc.quantity <= 0:
            alloc.delete()
        else:
            alloc.save(update_fields=['quantity'])

        remaining -= give_back


def reconsume_for_return_line(return_line):
    """
    عكس restore_for_return_line بالضبط لنفس سطر المرتجع — يُستخدم عند
    إلغاء مرتجع مؤكد (البضاعة المرتجعة تخرج من المخزون مجدداً).
    """
    from apps.items.models import ItemBatch
    from .models import SaleInvoiceLineBatchAllocation

    invoice_line = return_line.invoice_line
    restorations = list(return_line.batch_restorations.select_for_update())
    for restoration in restorations:
        if restoration.batch_id:
            batch = ItemBatch.objects.select_for_update().get(pk=restoration.batch_id)
            batch.quantity_remaining = max(Decimal('0'), batch.quantity_remaining - restoration.quantity)
            batch.save(update_fields=['quantity_remaining'])

        alloc = SaleInvoiceLineBatchAllocation.objects.filter(
            tenant=invoice_line.tenant, sale_line=invoice_line, batch_id=restoration.batch_id,
        ).select_for_update().first()
        if alloc:
            alloc.quantity += restoration.quantity
            alloc.save(update_fields=['quantity'])
        else:
            SaleInvoiceLineBatchAllocation.objects.create(
                tenant=invoice_line.tenant, sale_line=invoice_line, batch_id=restoration.batch_id,
                batch_number_snapshot=restoration.batch.batch_number if restoration.batch_id and restoration.batch else '',
                expiry_date_snapshot=restoration.batch.expiry_date if restoration.batch_id and restoration.batch else None,
                quantity=restoration.quantity,
            )

    return_line.batch_restorations.all().delete()
