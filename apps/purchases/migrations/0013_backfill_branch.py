from django.db import migrations


def backfill_branch(apps, schema_editor):
    PurchaseInvoice = apps.get_model('purchases', 'PurchaseInvoice')
    PurchaseReturn = apps.get_model('purchases', 'PurchaseReturn')

    BATCH_SIZE = 500

    # PurchaseInvoice.branch = stock.branch
    invoices = (
        PurchaseInvoice.objects
        .filter(branch__isnull=True, stock__branch__isnull=False)
        .select_related('stock')
        .iterator(chunk_size=500)
    )
    batch = []
    for inv in invoices:
        inv.branch_id = inv.stock.branch_id
        batch.append(inv)
        if len(batch) >= BATCH_SIZE:
            PurchaseInvoice.objects.bulk_update(batch, ['branch'])
            batch = []
    if batch:
        PurchaseInvoice.objects.bulk_update(batch, ['branch'])

    # PurchaseReturn.branch = original_invoice.stock.branch
    returns = (
        PurchaseReturn.objects
        .filter(branch__isnull=True, original_invoice__stock__branch__isnull=False)
        .select_related('original_invoice__stock')
        .iterator(chunk_size=500)
    )
    batch = []
    for ret in returns:
        ret.branch_id = ret.original_invoice.stock.branch_id
        batch.append(ret)
        if len(batch) >= BATCH_SIZE:
            PurchaseReturn.objects.bulk_update(batch, ['branch'])
            batch = []
    if batch:
        PurchaseReturn.objects.bulk_update(batch, ['branch'])


def noop_reverse(apps, schema_editor):
    # Intentionally not reversed: branch stays nullable and re-running the
    # forward migration is idempotent/safe, so there is nothing destructive
    # to undo here.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('purchases', '0012_purchaseinvoice_branch_purchasereturn_branch'),
    ]

    operations = [
        migrations.RunPython(backfill_branch, noop_reverse),
    ]
