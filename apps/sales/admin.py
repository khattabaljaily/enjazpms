from django.contrib import admin
from .models import (
    SaleInvoice, SaleInvoiceLine, SalePayment,
    SaleReturn, SaleReturnLine, StockMovement, CustomerLedger,
    SaleQuote, SaleQuoteLine,
)


class SaleInvoiceLineInline(admin.TabularInline):
    model = SaleInvoiceLine
    extra = 0
    readonly_fields = ('line_subtotal', 'tax_amount', 'line_total')
    fields = ('item', 'variant', 'quantity', 'unit_price',
              'discount_percent', 'tax_rate', 'line_subtotal', 'line_total',
              'returned_quantity')


class SalePaymentInline(admin.TabularInline):
    model = SalePayment
    extra = 0
    fields = ('payment_method', 'amount', 'payment_date', 'reference_number', 'is_reversed')


@admin.register(SaleInvoice)
class SaleInvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'invoice_date', 'customer', 'stock',
                    'payment_method', 'grand_total', 'paid_amount', 'status')
    list_filter = ('status', 'payment_method', 'tenant')
    search_fields = ('invoice_number', 'customer__name', 'reference_number')
    readonly_fields = ('invoice_number', 'subtotal', 'invoice_discount_amount',
                       'tax_amount', 'grand_total', 'paid_amount')
    inlines = [SaleInvoiceLineInline, SalePaymentInline]
    date_hierarchy = 'invoice_date'


@admin.register(SaleReturn)
class SaleReturnAdmin(admin.ModelAdmin):
    list_display = ('return_number', 'return_date', 'original_invoice', 'total_returned',
                    'refund_method', 'status')
    list_filter = ('status', 'refund_method', 'tenant')
    search_fields = ('return_number', 'original_invoice__invoice_number')


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('movement_date', 'item', 'stock', 'movement_type', 'direction',
                    'quantity', 'balance_after')
    list_filter = ('movement_type', 'direction', 'tenant', 'stock')
    search_fields = ('item__name', 'item__sku', 'reference_type')
    date_hierarchy = 'movement_date'
    readonly_fields = ('balance_after',)


@admin.register(CustomerLedger)
class CustomerLedgerAdmin(admin.ModelAdmin):
    list_display = ('entry_date', 'customer', 'entry_type', 'amount', 'running_balance')
    list_filter = ('entry_type', 'tenant')
    search_fields = ('customer__name',)
    date_hierarchy = 'entry_date'
    readonly_fields = ('running_balance',)


class SaleQuoteLineInline(admin.TabularInline):
    model = SaleQuoteLine
    extra = 0
    readonly_fields = ('line_subtotal', 'tax_amount', 'line_total')
    fields = ('item', 'variant', 'quantity', 'unit_price',
              'discount_percent', 'tax_rate', 'line_subtotal', 'line_total')


@admin.register(SaleQuote)
class SaleQuoteAdmin(admin.ModelAdmin):
    list_display = ('quote_number', 'quote_date', 'customer', 'stock',
                    'grand_total', 'status')
    list_filter = ('status', 'tenant')
    search_fields = ('quote_number', 'customer__name', 'reference_number')
    readonly_fields = ('quote_number', 'subtotal', 'quote_discount_amount',
                       'tax_amount', 'grand_total', 'converted_invoice',
                       'converted_at', 'converted_by')
    inlines = [SaleQuoteLineInline]
    date_hierarchy = 'quote_date'
