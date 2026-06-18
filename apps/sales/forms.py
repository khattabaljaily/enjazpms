from django import forms

from .models import SaleInvoice, SaleInvoiceLine, SaleReturn, SaleReturnLine, SalePayment


class SaleInvoiceForm(forms.ModelForm):
    class Meta:
        model = SaleInvoice
        fields = [
            'customer', 'stock', 'invoice_date', 'due_date',
            'payment_method', 'invoice_discount_type', 'invoice_discount_value',
            'cash_amount', 'bank_amount', 'bank_reference',
            'reference_number', 'notes',
        ]
        widgets = {
            'invoice_date': forms.DateInput(attrs={'type': 'date'}),
            'due_date': forms.DateInput(attrs={'type': 'date'}),
        }


class SaleInvoiceLineForm(forms.ModelForm):
    class Meta:
        model = SaleInvoiceLine
        fields = [
            'item', 'quantity', 'unit_price',
            'discount_percent', 'tax_rate',
            'batch_number', 'serial_number', 'expiry_date',
        ]
        widgets = {
            'expiry_date': forms.DateInput(attrs={'type': 'date'}),
        }


class SaleReturnForm(forms.ModelForm):
    class Meta:
        model = SaleReturn
        fields = ['return_date', 'refund_method', 'reason', 'notes']
        widgets = {
            'return_date': forms.DateInput(attrs={'type': 'date'}),
        }


class SaleReturnLineForm(forms.ModelForm):
    class Meta:
        model = SaleReturnLine
        fields = ['invoice_line', 'returned_quantity', 'unit_price']


class SalePaymentForm(forms.ModelForm):
    class Meta:
        model = SalePayment
        fields = ['payment_method', 'amount', 'payment_date', 'reference_number', 'notes']
        widgets = {
            'payment_date': forms.DateInput(attrs={'type': 'date'}),
        }
