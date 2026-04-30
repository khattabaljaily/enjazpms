from django.contrib import admin

from .models import PurchaseInvoice, PurchaseInvoiceLine, PurchasePayment, SupplierLedger


class PurchaseInvoiceLineInline(admin.TabularInline):
    model = PurchaseInvoiceLine
    extra = 0


@admin.register(PurchaseInvoice)
class PurchaseInvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'supplier', 'stock', 'invoice_date', 'status', 'grand_total')
    list_filter = ('status', 'payment_method', 'invoice_date')
    search_fields = ('invoice_number', 'supplier__name', 'stock__name')
    inlines = [PurchaseInvoiceLineInline]


@admin.register(PurchasePayment)
class PurchasePaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'payment_method', 'amount', 'payment_date', 'is_reversed')
    list_filter = ('payment_method', 'is_reversed', 'payment_date')


@admin.register(SupplierLedger)
class SupplierLedgerAdmin(admin.ModelAdmin):
    list_display = ('supplier', 'entry_type', 'amount', 'entry_date', 'running_balance')
    list_filter = ('entry_type', 'entry_date')
