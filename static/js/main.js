// EnjazIMS - Main JavaScript
// ================================

// Sidebar toggle (mobile)
document.addEventListener('DOMContentLoaded', function () {
    const toggleBtn = document.getElementById('sidebarToggleBtn');
    const sidebar   = document.querySelector('.sidebar');
    if (!toggleBtn || !sidebar) return;

    // Create overlay
    let overlay = document.getElementById('sidebarOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'sidebarOverlay';
        overlay.className = 'sidebar-overlay';
        document.body.appendChild(overlay);
    }

    function openSidebar() {
        sidebar.classList.add('show');
        overlay.classList.add('show');
        document.body.style.overflow = 'hidden';
    }
    function closeSidebar() {
        sidebar.classList.remove('show');
        overlay.classList.remove('show');
        document.body.style.overflow = '';
    }

    toggleBtn.addEventListener('click', function () {
        sidebar.classList.contains('show') ? closeSidebar() : openSidebar();
    });
    overlay.addEventListener('click', closeSidebar);
});

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            cookie = cookie.trim();
            if (cookie.startsWith(name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

// Initialize numeric input conversion
document.addEventListener('DOMContentLoaded', function () {
    EnjazIMS.initNumericInputs();
});

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
    
    // Show app toast notification (non-Bootstrap)
    toast: function(message, type = 'success') {
        const typeMap = {
            error: 'error',
            danger: 'error',
            success: 'success',
            warning: 'warning',
            info: 'info'
        };

        const iconMap = {
            success: 'fa-check-circle',
            error: 'fa-exclamation-circle',
            warning: 'fa-exclamation-triangle',
            info: 'fa-info-circle'
        };

        const toastType = typeMap[type] || 'info';
        const iconClass = iconMap[toastType] || 'fa-info-circle';
        const toastId = `global-toast-${Date.now()}-${Math.floor(Math.random() * 1000)}`;
        const toastContainer = document.getElementById('global-alerts');

        if (!toastContainer) return;

        const toast = document.createElement('div');
        toast.id = toastId;
        toast.className = `cx-toast cx-toast--${toastType}`;
        toast.setAttribute('role', 'status');
        toast.innerHTML = `
            <div class="cx-toast__icon"><i class="fas ${iconClass}"></i></div>
            <div class="cx-toast__message">${message}</div>
            <button type="button" class="cx-toast__close" aria-label="Close">
                <i class="fas fa-times"></i>
            </button>
        `;

        toastContainer.prepend(toast);
        requestAnimationFrame(() => toast.classList.add('show'));

        const closeToast = () => {
            toast.classList.remove('show');
            toast.classList.add('hide');
            setTimeout(() => toast.remove(), 220);
        };

        toast.querySelector('.cx-toast__close')?.addEventListener('click', closeToast);
        setTimeout(closeToast, 4000);
    },

    rememberToast: function(message, type = 'success') {
        try {
            sessionStorage.setItem('__enjazFlash', JSON.stringify({ message, type }));
        } catch (e) {
            // ignore storage errors
        }
    },

    consumeRememberedToast: function() {
        try {
            const flashRaw = sessionStorage.getItem('__enjazFlash');
            if (!flashRaw) return;
            const flash = JSON.parse(flashRaw);
            if (flash?.message) {
                this.toast(flash.message, flash.type || 'success');
            }
            sessionStorage.removeItem('__enjazFlash');
        } catch (e) {
            sessionStorage.removeItem('__enjazFlash');
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

    // Modal-based confirmation — returns a Promise that resolves true/false.
    // Usage: EnjazIMS.confirmAction('رسالة').then(ok => { if (ok) ... });
    confirmAction: function(message, title) {
        return new Promise(function(resolve) {
            // Reuse or create the shared confirm modal
            let modal = document.getElementById('enjazConfirmModal');
            if (!modal) {
                modal = document.createElement('div');
                modal.id = 'enjazConfirmModal';
                modal.className = 'modal fade';
                modal.tabIndex = -1;
                modal.setAttribute('data-bs-backdrop', 'static');
                modal.innerHTML = `
                    <div class="modal-dialog modal-dialog-centered modal-sm">
                        <div class="modal-content">
                            <div class="modal-header border-0 pb-0">
                                <h6 class="modal-title" id="enjazConfirmTitle"></h6>
                                <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                            </div>
                            <div class="modal-body pt-2 pb-3" id="enjazConfirmBody" style="font-size:0.9rem"></div>
                            <div class="modal-footer border-0 pt-0">
                                <button type="button" class="btn btn-sm btn-secondary" data-bs-dismiss="modal" id="enjazConfirmNo">إلغاء</button>
                                <button type="button" class="btn btn-sm btn-primary" id="enjazConfirmYes">تأكيد</button>
                            </div>
                        </div>
                    </div>`;
                document.body.appendChild(modal);
            }

            document.getElementById('enjazConfirmTitle').textContent = title || 'تأكيد العملية';
            document.getElementById('enjazConfirmBody').textContent = message || 'هل تريد المتابعة؟';

            const bsModal = bootstrap.Modal.getOrCreate(modal);

            function cleanup() {
                document.getElementById('enjazConfirmYes').removeEventListener('click', onYes);
                modal.removeEventListener('hidden.bs.modal', onHide);
            }
            function onYes() {
                cleanup();
                bsModal.hide();
                resolve(true);
            }
            function onHide() {
                cleanup();
                resolve(false);
            }

            document.getElementById('enjazConfirmYes').addEventListener('click', onYes);
            modal.addEventListener('hidden.bs.modal', onHide, { once: true });

            bsModal.show();
        });
    },

    // Parse localized number strings into a JS number.
    // Accepts values like: 1,000.00 | 1000,00 | ١٬٠٠٠٫٠٠ | 1000
    parseNumber: function(value) {
        if (typeof value === 'number') {
            return Number.isFinite(value) ? value : 0;
        }
        if (value === null || value === undefined) {
            return 0;
        }

        let str = String(value).trim();
        if (!str) {
            return 0;
        }

        const arabicDigits = '٠١٢٣٤٥٦٧٨٩';
        const easternArabicDigits = '۰۱۲۳۴۵۶۷۸۹';

        str = str
            .replace(/[٠-٩]/g, (d) => String(arabicDigits.indexOf(d)))
            .replace(/[۰-۹]/g, (d) => String(easternArabicDigits.indexOf(d)))
            .replace(/\u066C/g, ',')
            .replace(/\u066B/g, '.')
            .replace(/\s+/g, '');

        const lastComma = str.lastIndexOf(',');
        const lastDot = str.lastIndexOf('.');

        if (lastComma > -1 && lastDot > -1) {
            if (lastComma > lastDot) {
                str = str.replace(/\./g, '').replace(',', '.');
            } else {
                str = str.replace(/,/g, '');
            }
        } else if (lastComma > -1) {
            str = str.replace(',', '.');
        }

        const n = Number(str);
        return Number.isFinite(n) ? n : 0;
    },
    
    // Format number
    formatNumber: function(num, decimals = 2) {
        const n = this.parseNumber(num);
        return n.toLocaleString('en-US', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
    },

    // Standard money format: 1,000.00
    formatMoney: function(amount) {
        return this.formatNumber(amount, 2);
    },

    // Quantity format: 3 | 3.5 | 3.125 (without forcing two money decimals)
    formatQuantity: function(quantity, maxDecimals = 3) {
        const n = this.parseNumber(quantity);
        return n.toLocaleString('en-US', {
            minimumFractionDigits: 0,
            maximumFractionDigits: maxDecimals,
        });
    },
    
    // Format currency
    formatCurrency: function(amount, currency = 'EGP') {
        const formatted = this.formatMoney(amount);
        return `${formatted} ${currency}`;
    },

    // Convert Arabic numerals to English in a string
    convertArabicNumerals: function(str) {
        const arabicDigits = '٠١٢٣٤٥٦٧٨٩';
        const englishDigits = '0123456789';
        return str.replace(/[٠-٩]/g, (d) => englishDigits[arabicDigits.indexOf(d)]);
    },

    // Apply Arabic numeral conversion to numeric inputs
    initNumericInputs: function() {
        const numericInputs = document.querySelectorAll('input[type="number"], input[inputmode="decimal"], input.numeric-input');
        
        numericInputs.forEach(input => {
            input.addEventListener('input', function(e) {
                const converted = EnjazIMS.convertArabicNumerals(e.target.value);
                if (converted !== e.target.value) {
                    e.target.value = converted;
                }
            });
            
            input.addEventListener('paste', function(e) {
                setTimeout(() => {
                    const converted = EnjazIMS.convertArabicNumerals(e.target.value);
                    if (converted !== e.target.value) {
                        e.target.value = converted;
                    }
                }, 0);
            });
        });
    }
};

// DataTable default configuration (Arabic)
$.extend(true, $.fn.dataTable.defaults, {
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

    const pendingMessages = window.__enjazPendingMessages || [];
    if (pendingMessages.length) {
        pendingMessages.forEach((entry) => {
            if (!entry || !entry.message) return;
            EnjazIMS.toast(entry.message, entry.type || 'info');
        });
        window.__enjazPendingMessages = [];
    }

    EnjazIMS.consumeRememberedToast();

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
                "X-CSRFToken": getCookie('csrftoken'),
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
