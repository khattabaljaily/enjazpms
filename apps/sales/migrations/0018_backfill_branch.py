from django.db import migrations


def backfill_branch(apps, schema_editor):
    SaleInvoice = apps.get_model('sales', 'SaleInvoice')
    SaleReturn = apps.get_model('sales', 'SaleReturn')

    # SaleInvoice.branch = stock.branch
    invoices = (
        SaleInvoice.objects
        .filter(branch__isnull=True, stock__branch__isnull=False)
        .select_related('stock')
        .iterator(chunk_size=500)
    )
    batch = []
    BATCH_SIZE = 500
    for inv in invoices:
        inv.branch_id = inv.stock.branch_id
        batch.append(inv)
        if len(batch) >= BATCH_SIZE:
            SaleInvoice.objects.bulk_update(batch, ['branch'])
            batch = []
    if batch:
        SaleInvoice.objects.bulk_update(batch, ['branch'])

    # SaleReturn.branch = original_invoice.stock.branch
    returns = (
        SaleReturn.objects
        .filter(branch__isnull=True, original_invoice__stock__branch__isnull=False)
        .select_related('original_invoice__stock')
        .iterator(chunk_size=500)
    )
    batch = []
    for ret in returns:
        ret.branch_id = ret.original_invoice.stock.branch_id
        batch.append(ret)
        if len(batch) >= BATCH_SIZE:
            SaleReturn.objects.bulk_update(batch, ['branch'])
            batch = []
    if batch:
        SaleReturn.objects.bulk_update(batch, ['branch'])


def noop_reverse(apps, schema_editor):
    # Intentionally not reversed: branch stays nullable and re-running the
    # forward migration is idempotent/safe, so there is nothing destructive
    # to undo here.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0017_saleinvoice_branch_salereturn_branch'),
        ('stocks', '0010_stock_branch_alter_stock_branch_name'),
    ]

    operations = [
        migrations.RunPython(backfill_branch, noop_reverse),
    ]
