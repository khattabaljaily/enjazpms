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
    
    // Show bootstrap alert notification
    toast: function(message, type = 'success') {
        const typeMap = {
            error: 'danger',
            success: 'success',
            warning: 'warning',
            info: 'info'
        };

        const iconMap = {
            success: 'fa-check-circle',
            danger: 'fa-exclamation-circle',
            warning: 'fa-exclamation-triangle',
            info: 'fa-info-circle'
        };

        const alertType = typeMap[type] || 'info';
        const iconClass = iconMap[alertType] || 'fa-info-circle';
        const alertId = `global-alert-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
        const alertHtml = `
            <div id="${alertId}" class="alert alert-${alertType} alert-dismissible fade show" role="alert">
                <i class="fas ${iconClass} me-2"></i>
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;

        const alertContainer = $('#global-alerts');
        if (alertContainer.length) {
            alertContainer.prepend(alertHtml);

            setTimeout(() => {
                const element = document.getElementById(alertId);
                if (element) {
                    const alert = bootstrap.Alert.getOrCreateInstance(element);
                    alert.close();
                }
            }, 4000);
        }
    },

    clearFormErrors: function(form) {
        form.find('.is-invalid').removeClass('is-invalid');
        form.find('.js-field-errors').remove();
        form.find('.js-form-errors').addClass('d-none').empty();
    },

    showFormError: function(form, message) {
        const errorBox = form.find('.js-form-errors');
        if (errorBox.length) {
            errorBox.removeClass('d-none').addClass('alert-dismissible fade show').html(`
                <i class="fas fa-exclamation-circle me-2"></i>
                ${message}
                <button type="button" class="btn-close" aria-label="Close"></button>
            `);

            const closeBtn = errorBox.find('.btn-close');
            closeBtn.off('click').on('click', function() {
                errorBox.addClass('d-none').removeClass('show').empty();
            });

            setTimeout(() => {
                errorBox.addClass('d-none').removeClass('show').empty();
            }, 5000);
        }
    },

    renderFieldErrors: function(form, errors) {
        if (!errors) return;

        Object.entries(errors).forEach(([fieldName, messages]) => {
            if (!messages || !messages.length) return;

            if (fieldName === '__all__') {
                this.showFormError(form, messages.join('<br>'));
                return;
            }

            const field = form.find(`[name="${fieldName}"]`);
            if (!field.length) return;

            field.addClass('is-invalid');

            const existingError = form.find(`.js-field-errors[data-field="${fieldName}"]`).first();
            if (existingError.length) {
                existingError.html(messages.join('<br>'));
            } else {
                field.last().after(`<div class="text-danger small mt-1 js-field-errors" data-field="${fieldName}">${messages.join('<br>')}</div>`);
            }
        });
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

    // AJAX auth forms (login/register)
    $('.js-auth-ajax').on('submit', function(e) {
        e.preventDefault();

        const form = $(this);
        const submitBtn = form.find('button[type=submit]');
        const originalText = submitBtn.html();

        EnjazIMS.clearFormErrors(form);
        EnjazIMS.showLoading(submitBtn);

        $.ajax({
            url: form.attr('action') || window.location.href,
            method: form.attr('method') || 'POST',
            data: form.serialize(),
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            },
            success: function(response) {
                EnjazIMS.hideLoading(submitBtn, originalText);

                if (response.success) {
                    if (response.redirect_url) {
                        window.location.href = response.redirect_url;
                    }
                    return;
                }

                if (response.errors) {
                    EnjazIMS.renderFieldErrors(form, response.errors);

                    const hasNonFieldErrors = Array.isArray(response.errors.__all__) && response.errors.__all__.length;
                    if (hasNonFieldErrors) {
                        EnjazIMS.showFormError(form, response.message || response.errors.__all__.join('<br>'));
                    } else {
                        EnjazIMS.showFormError(form, 'يرجى مراجعة الحقول المحددة أدناه');
                    }
                    return;
                }

                EnjazIMS.showFormError(form, response.message || 'يرجى التحقق من الحقول المطلوبة');
            },
            error: function(xhr) {
                EnjazIMS.hideLoading(submitBtn, originalText);

                const response = xhr.responseJSON || {};

                if (response.redirect_url) {
                    window.location.href = response.redirect_url;
                    return;
                }

                if (response.errors) {
                    EnjazIMS.renderFieldErrors(form, response.errors);

                    const hasNonFieldErrors = Array.isArray(response.errors.__all__) && response.errors.__all__.length;
                    if (hasNonFieldErrors) {
                        EnjazIMS.showFormError(form, response.message || response.errors.__all__.join('<br>'));
                    } else {
                        EnjazIMS.showFormError(form, 'يرجى مراجعة الحقول المحددة أدناه');
                    }
                    return;
                }

                EnjazIMS.showFormError(form, response.message || 'تعذر إرسال النموذج، حاول مرة أخرى');
            }
        });
    });

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
