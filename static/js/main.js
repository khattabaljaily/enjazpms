// EnjazIMS - Main JavaScript
// ================================

// Utility functions
const EnjazIMS = {
    // Show loading spinner
    showLoading: function(element) {
        $(element).prop('disabled', true).html('<i class="fas fa-spinner fa-spin"></i> جاري التحميل...');
    },
    
    // Hide loading spinner
    hideLoading: function(element, originalText) {
        $(element).prop('disabled', false).html(originalText);
    },
    
    // Show toast notification
    toast: function(message, type = 'success') {
        toastr[type](message);
    },
    
    // Confirm delete action
    confirmDelete: function(message = 'هل أنت متأكد من الحذف؟') {
        return confirm(message);
    },
    
    // Format number
    formatNumber: function(num, decimals = 2) {
        return parseFloat(num).toFixed(decimals);
    },
    
    // Format currency
    formatCurrency: function(amount, currency = 'EGP') {
        const formatted = this.formatNumber(amount, 2);
        return `${formatted} ${currency}`;
    }
};

// DataTable default configuration (Arabic)
$.extend(true, $.fn.dataTable.defaults, {
    language: {
        url: '//cdn.datatables.net/plug-ins/1.13.6/i18n/ar.json'
    },
    pageLength: 25,
    ordering: true,
    searching: true,
    responsive: true
});

// Initialize all data tables
$(document).ready(function() {
    // Auto-initialize tables with .data-table class
    if ($.fn.DataTable) {
        $('.data-table').DataTable();
    }
    
    // Auto-focus first input in modals
    $('.modal').on('shown.bs.modal', function() {
        $(this).find('input:not([type=hidden]):first').focus();
    });
    
    // Confirm delete buttons
    $('.btn-delete').on('click', function(e) {
        if (!EnjazIMS.confirmDelete()) {
            e.preventDefault();
            return false;
        }
    });
    
    // Current year in footer
    $('#current-year').text(new Date().getFullYear());
});

// AJAX Form Handler
function handleAjaxForm(formId, onSuccess) {
    $(formId).on('submit', function(e) {
        e.preventDefault();
        
        const form = $(this);
        const submitBtn = form.find('button[type=submit]');
        const originalText = submitBtn.html();
        
        EnjazIMS.showLoading(submitBtn);
        
        $.ajax({
            url: form.attr('action'),
            method: form.attr('method') || 'POST',
            data: form.serialize(),
            success: function(response) {
                EnjazIMS.hideLoading(submitBtn, originalText);
                
                if (response.success) {
                    EnjazIMS.toast(response.message, 'success');
                    if (onSuccess) onSuccess(response);
                } else {
                    EnjazIMS.toast(response.message || 'حدث خطأ', 'error');
                }
            },
            error: function(xhr) {
                EnjazIMS.hideLoading(submitBtn, originalText);
                
                let message = 'حدث خطأ في الاتصال';
                if (xhr.responseJSON && xhr.responseJSON.message) {
                    message = xhr.responseJSON.message;
                }
                
                EnjazIMS.toast(message, 'error');
            }
        });
    });
}
