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

document.addEventListener('DOMContentLoaded', function () {
    // Highlight active nav link and open parent submenus
    const currentPath = window.location.pathname;
    const sidebar     = document.querySelector('.sidebar');
    const navLinks    = document.querySelectorAll('.sidebar a[href]');

    let activeLink = null;
    navLinks.forEach(function(link) {
        const href = link.getAttribute('href');
        if (href && href !== '#' && (currentPath === href || currentPath.startsWith(href + '/'))) {
            link.classList.add('active');
            activeLink = link;

            if (link.classList.contains('nav-sublink')) {
                let parent = link.closest('.has-submenu');
                while (parent) {
                    parent.classList.add('open');
                    const parentLink = parent.querySelector('.nav-link-toggle');
                    if (parentLink) parentLink.classList.add('submenu-active');
                    parent = parent.parentElement.closest('.has-submenu');
                }
            }
        }
    });

    // Scroll sidebar so active link is visible (keeps position after navigation)
    if (activeLink && sidebar) {
        setTimeout(function () {
            const linkOffsetTop = activeLink.offsetTop;
            const target = Math.max(0, linkOffsetTop - sidebar.clientHeight / 3);
            sidebar.scrollTo({ top: target, behavior: 'smooth' });
        }, 80);
    }
});

document.addEventListener('DOMContentLoaded', function () {
    document.body.addEventListener('click', function(event) {
        const toggle = event.target.closest('.nav-link-toggle');
        if (!toggle) return;

        const sidebar = toggle.closest('.sidebar');
        if (!sidebar) return;

        event.preventDefault();
        const parent = toggle.closest('.has-submenu');
        if (!parent) return;

        const isOpen = parent.classList.contains('open');
        document.querySelectorAll('.sidebar .has-submenu.open').forEach(function(el) {
            if (el === parent || el.contains(parent)) return;
            el.classList.remove('open');
        });
        parent.classList.toggle('open', !isOpen);
    });
});

// ─── Global Page Spinner ───────────────────────────────────────────────────
const GSpinner = (function () {
    var _el = null;
    var _count = 0;
    var _safety = null;

    function _getEl() { return _el || (_el = document.getElementById('global-spinner')); }

    function _doHide() {
        var s = _getEl();
        if (s) s.classList.remove('active');
    }

    function show() {
        _count++;
        var s = _getEl();
        if (s) s.classList.add('active');
        clearTimeout(_safety);
        _safety = setTimeout(forceHide, 15000);
    }

    function hide() {
        _count = Math.max(0, _count - 1);
        if (_count === 0) { clearTimeout(_safety); _doHide(); }
    }

    function forceHide() {
        clearTimeout(_safety);
        _count = 0;
        _doHide();
    }

    return { show: show, hide: hide, forceHide: forceHide };
})();

// pageshow fires on both normal load AND bfcache restore (back/forward).
// This is the fix for "spinner stays active after navigating back".
window.addEventListener('pageshow', function () { GSpinner.forceHide(); });
window.addEventListener('load',     function () { GSpinner.forceHide(); });

// Show on navigation link clicks — skip anchors, modals, new tabs, downloads.
document.addEventListener('click', function (e) {
    var link = e.target.closest('a[href]');
    if (!link) return;
    var href = link.getAttribute('href') || '';
    if (!href || href === '#' || /^(javascript:|mailto:|tel:|#)/i.test(href)) return;
    if (link.target === '_blank') return;
    if (link.hasAttribute('data-bs-toggle') || link.hasAttribute('data-bs-dismiss')) return;
    if (link.hasAttribute('download') || link.hasAttribute('data-no-spinner')) return;
    // Skip file-download URLs (exports, templates, backups) — page doesn't reload so spinner never hides.
    if (/\/(export|download|template|backup)[_\/]|[?&](export|download)=/i.test(href)) return;
    GSpinner.show();
}, true);

// Show on page-navigation form submissions only (not AJAX forms).
// Bubble phase runs AFTER jQuery's e.preventDefault() on AJAX forms,
// so defaultPrevented === true for those and we skip them.
document.addEventListener('submit', function (e) {
    if (!e.defaultPrevented) GSpinner.show();
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
        if (typeof message === 'object' && message !== null) {
            message = message.message || message.error || JSON.stringify(message);
        }
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
    
    // Confirm delete action — returns a Promise (use with await)
    confirmDelete: function(message = 'هل أنت متأكد من الحذف؟') {
        return EnjazIMS.confirmAction(message, 'تأكيد الحذف');
    },

    // Modal-based confirmation — returns a Promise that resolves true/false.
    // Usage: EnjazIMS.confirmAction('رسالة').then(ok => { if (ok) ... });
    confirmAction: function(message, title, confirmText) {
        return new Promise(function(resolve) {
            // Determine icon, colour and button label from title keywords
            const t = title || '';
            let iconClass, iconColor, iconBg, btnColor, btnLabel;
            if (/حذف/.test(t)) {
                iconClass = 'fa-trash-alt'; iconColor = '#ef4444'; iconBg = 'rgba(239,68,68,0.1)';
                btnColor  = '#ef4444'; btnLabel = confirmText || 'نعم، احذف';
            } else if (/إلغاء/.test(t)) {
                iconClass = 'fa-ban'; iconColor = '#f59e0b'; iconBg = 'rgba(245,158,11,0.1)';
                btnColor  = '#f59e0b'; btnLabel = confirmText || 'نعم، إلغاء';
            } else if (/تجاهل/.test(t)) {
                iconClass = 'fa-rotate-left'; iconColor = '#6b7280'; iconBg = 'rgba(107,114,128,0.1)';
                btnColor  = '#6b7280'; btnLabel = confirmText || 'نعم، تجاهل';
            } else if (/تأكيد/.test(t)) {
                iconClass = 'fa-check-circle'; iconColor = '#10b981'; iconBg = 'rgba(16,185,129,0.1)';
                btnColor  = '#10b981'; btnLabel = confirmText || 'نعم، تأكيد';
            } else {
                iconClass = 'fa-circle-question'; iconColor = '#6366f1'; iconBg = 'rgba(99,102,241,0.1)';
                btnColor  = '#6366f1'; btnLabel = confirmText || 'نعم، متابعة';
            }

            // Create a simple overlay modal that works on all devices
            const overlay = document.createElement('div');
            overlay.id = 'enjazConfirmOverlay';
            overlay.style.cssText = `
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.5);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 9999;
                padding: 1rem;
            `;

            const modal = document.createElement('div');
            modal.style.cssText = `
                background: white;
                border-radius: 24px;
                max-width: 420px;
                width: 100%;
                box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
                overflow: hidden;
            `;

            modal.innerHTML = `
                <div style="padding: 2rem 1.75rem; text-align: center;">
                    <div style="width: 64px; height: 64px; border-radius: 32px; background: ${iconBg}; display: inline-flex; align-items: center; justify-content: center; margin-bottom: 1rem;">
                        <i class="fas ${iconClass}" style="font-size: 2rem; color: ${iconColor};"></i>
                    </div>
                    <h5 style="font-size: 1.25rem; font-weight: 700; color: #1f2937; margin-bottom: 0.5rem;">${t || 'تأكيد'}</h5>
                    <p style="font-size: 0.85rem; color: #6b7280; margin: 0;">
                        ${message}
                    </p>
                </div>
                <div style="display: flex; align-items: center; justify-content: center; gap: 0.75rem; padding: 1rem 1.75rem; border-top: 1px solid #e5e7eb; background: #f9fafb;">
                    <button type="button" class="cx-btn-ghost" id="enjazConfirmNo" style="padding: 0.5rem 1rem; border: 1px solid #d1d5db; border-radius: 8px; background: white; color: #6b7280; font-size: 0.875rem; cursor: pointer;">تراجع</button>
                    <button type="button" id="enjazConfirmYes" style="padding: 0.5rem 1rem; border: none; border-radius: 8px; background: ${btnColor}; color: white; font-size: 0.875rem; cursor: pointer; font-weight: 600;">${btnLabel}</button>
                </div>
            `;

            overlay.appendChild(modal);
            document.body.appendChild(overlay);

            // Prevent body scroll
            document.body.style.overflow = 'hidden';

            function cleanup() {
                document.getElementById('enjazConfirmYes').removeEventListener('click', onYes);
                document.getElementById('enjazConfirmNo').removeEventListener('click', onNo);
                document.body.removeChild(overlay);
                document.body.style.overflow = '';
            }

            function onYes() {
                cleanup();
                resolve(true);
            }

            function onNo() {
                cleanup();
                resolve(false);
            }

            document.getElementById('enjazConfirmYes').addEventListener('click', onYes);
            document.getElementById('enjazConfirmNo').addEventListener('click', onNo);

            // Close on overlay click
            overlay.addEventListener('click', function(e) {
                if (e.target === overlay) {
                    onNo();
                }
            });
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
    formatQuantity: function(quantity, maxDecimals = 2) {
        const n = this.parseNumber(quantity);
        return n.toLocaleString('en-US', {
            minimumFractionDigits: 0,
            maximumFractionDigits: maxDecimals,
        });
    },
    
    // Format currency
    formatCurrency: function(amount, currency = 'SDG') {
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
    },

    // Wrap every number input (not already wrapped) with +/- stepper buttons
    initNumberSteppers: function() {
        document.querySelectorAll('input[type="number"]').forEach(function(input) {
            // Skip if already wrapped or opted out
            if (input.closest('.num-stepper') || input.dataset.noStepper !== undefined) return;
            // Skip hidden inputs
            if (input.offsetParent === null && input.type === 'hidden') return;

            const min  = input.hasAttribute('min')  ? parseFloat(input.min)  : null;
            const max  = input.hasAttribute('max')  ? parseFloat(input.max)  : null;

            // Build wrapper
            const wrapper = document.createElement('div');
            wrapper.className = 'input-group num-stepper';

            // Clone to preserve all attributes/listeners
            input.parentNode.insertBefore(wrapper, input);
            wrapper.appendChild(input);

            function makeBtn(label, delta) {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'btn btn-outline-secondary num-stepper-btn';
                btn.textContent = label;
                btn.addEventListener('click', function() {
                    const cur = parseFloat(input.value) || 0;
                    let next  = Math.round((cur + delta) * 1e9) / 1e9; // avoid float drift
                    if (min !== null) next = Math.max(min, next);
                    if (max !== null) next = Math.min(max, next);
                    input.value = next;
                    input.dispatchEvent(new Event('input',  { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                });
                return btn;
            }

            // In RTL layout: prepend = right side, append = left side
            // Put + on right (prepend), − on left (append)
            const btnPlus  = makeBtn('+', 1);
            const btnMinus = makeBtn('−', -1);

            wrapper.insertBefore(btnPlus, input);   // right of input in RTL
            wrapper.appendChild(btnMinus);           // left of input in RTL
        });
    }
};

window.EnjazIMS = EnjazIMS;

// jQuery AJAX hooks for GSpinner + backwards-compat alias
$(document)
    .on('ajaxStart', function () { GSpinner.show(); })
    .on('ajaxStop',  function () { GSpinner.hide(); });

window.GlobalSpinner = GSpinner;

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

    // Number steppers — initial page load
    EnjazIMS.initNumberSteppers();

    // Re-run steppers when DOM changes (dynamic invoice lines, modals, etc.)
    let _stepperTimer = null;
    new MutationObserver(function(muts) {
        if (!muts.some(m => m.addedNodes.length)) return;
        clearTimeout(_stepperTimer);
        _stepperTimer = setTimeout(function() { EnjazIMS.initNumberSteppers(); }, 120);
    }).observe(document.body, { childList: true, subtree: true });

    // Numeric-only filter for invoice line fields (.inv-num-field)
    // Allows: digits 0-9, one decimal point, backspace/delete/arrows/tab/home/end
    document.addEventListener('keydown', function(e) {
        var el = e.target;
        if (!el.classList || !el.classList.contains('inv-num-field')) return;
        var allowed = [
            'Backspace','Delete','Tab','Escape','Enter',
            'ArrowLeft','ArrowRight','ArrowUp','ArrowDown',
            'Home','End'
        ];
        if (allowed.indexOf(e.key) !== -1) return;
        if ((e.ctrlKey || e.metaKey) && ['a','c','v','x','z'].indexOf(e.key.toLowerCase()) !== -1) return;
        if (e.key === '.' || e.key === ',') {
            // allow only one decimal separator
            if (el.value.indexOf('.') !== -1 || el.value.indexOf(',') !== -1) e.preventDefault();
            return;
        }
        if (e.key < '0' || e.key > '9') e.preventDefault();
    }, true);

    document.addEventListener('paste', function(e) {
        var el = e.target;
        if (!el.classList || !el.classList.contains('inv-num-field')) return;
        var text = (e.clipboardData || window.clipboardData).getData('text');
        if (!/^\d+([.,]\d*)?$/.test(text.trim())) e.preventDefault();
    }, true);

    // Auto-focus first input in modals
    $('.modal').on('shown.bs.modal', function() {
        $(this).find('input:not([type=hidden]):first').focus();
    });
    
    // Confirm delete buttons (non-AJAX links/forms only — AJAX handlers use their own modals)
    $('.btn-delete').on('click', async function(e) {
        e.preventDefault();
        const href = $(this).attr('href') || $(this).data('href');
        const confirmed = await EnjazIMS.confirmAction('هل أنت متأكد من الحذف؟', 'تأكيد الحذف');
        if (confirmed && href) {
            window.location.href = href;
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


/* ════════════════════════════════════════════════════════════
   Training Panel — دليل الاستخدام
   ════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function () {
    var fab     = document.getElementById('training-fab');
    var panel   = document.getElementById('training-panel');
    var panelBody = panel ? panel.querySelector('.training-panel__body') : null;
    var overlay = document.getElementById('training-overlay');
    var closeBtn= document.getElementById('training-close-btn');

    if (!fab || !panel) return;

    function open() {
        panel.classList.add('is-open');
        overlay.classList.add('is-open');
        fab.classList.add('is-open');
        panel.setAttribute('aria-hidden', 'false');
        // Prevent page scroll when panel is open
        document.documentElement.style.overflow = 'hidden';
    }
    function close() {
        panel.classList.remove('is-open');
        overlay.classList.remove('is-open');
        fab.classList.remove('is-open');
        panel.setAttribute('aria-hidden', 'true');
        // Restore page scroll
        document.documentElement.style.overflow = '';
    }

    fab.addEventListener('click', function () {
        panel.classList.contains('is-open') ? close() : open();
    });
    if (closeBtn)  closeBtn.addEventListener('click', close);
    if (overlay)   overlay.addEventListener('click', close);
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') close();
    });

    /* Accordion sections inside training files */
    document.addEventListener('click', function (e) {
        var head = e.target.closest('.t-section__head');
        if (!head) return;
        var section = head.closest('.t-section');
        if (!section) return;

        section.classList.toggle('is-open');

        if (section.classList.contains('is-open') && panelBody) {
            section.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    });
});


/* ════════════════════════════════════════════════════════════
   Sidebar Scroll Fix — منع تمرير الحدث إلى الصفحة
   ════════════════════════════════════════════════════════════ */
(function fixSidebarScroll() {
    var sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    /* Handle touch scroll for mobile to prevent scroll chaining */
    var touchStartY = 0;
    sidebar.addEventListener('touchstart', function(e) {
        touchStartY = e.touches[0].clientY;
    }, { passive: true });

    sidebar.addEventListener('touchmove', function(e) {
        var touchEndY = e.touches[0].clientY;
        var scrollTop = sidebar.scrollTop;
        var scrollHeight = sidebar.scrollHeight;
        var clientHeight = sidebar.clientHeight;

        var isScrollingUp = touchEndY > touchStartY;
        var isScrollingDown = touchEndY < touchStartY;

        if (scrollHeight <= clientHeight) {
            e.preventDefault();
        } else if ((isScrollingDown && scrollTop + clientHeight >= scrollHeight) ||
                   (isScrollingUp && scrollTop <= 0)) {
            e.preventDefault();
        }
    }, { passive: false });
})();


/* ════════════════════════════════════════════════════════════
   PWA Install Prompt — تثبيت التطبيق
   ════════════════════════════════════════════════════════════ */
(function setupPWAInstallPrompt() {
    var installPrompt = null;
    var isiOS = /iphone|ipad|ipod/.test(navigator.userAgent.toLowerCase());
    var isAndroid = /android/.test(navigator.userAgent.toLowerCase());
    var isDesktop = !isiOS && !isAndroid;
    var promptShownKey = 'pwa_install_prompt_shown';
    var iOSPromptKey = 'ios_install_prompt_shown';

    /* ─── Desktop/Android Install Prompt ─── */
    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        installPrompt = e;
        showInstallBanner('desktop');
    });

    /* ─── iOS Install Prompt ─── */
    if (isiOS) {
        // Show iOS install prompt after 3 seconds
        var iosShown = sessionStorage.getItem(iOSPromptKey);
        if (!iosShown && !isAppAlreadyInstalled()) {
            setTimeout(function() {
                showInstallBanner('ios');
                sessionStorage.setItem(iOSPromptKey, 'true');
            }, 3000);
        }
    }

    function isAppAlreadyInstalled() {
        // Check if app is running in standalone mode (already installed)
        return window.navigator.standalone === true || 
               window.matchMedia('(display-mode: standalone)').matches;
    }

    function showInstallBanner(type) {
        // Don't show if already shown this session
        if (sessionStorage.getItem(promptShownKey + '_' + type)) return;

        var banner = document.createElement('div');
        banner.className = 'pwa-install-banner pwa-install-banner--' + type;
        banner.setAttribute('role', 'alert');
        banner.innerHTML = type === 'ios' ? 
            `<div class="pwa-install-banner__content">
                <div class="pwa-install-banner__icon"><i class="fas fa-download"></i></div>
                <div class="pwa-install-banner__text">
                    <div class="pwa-install-banner__title">ثبّت التطبيق</div>
                    <div class="pwa-install-banner__description">اضغط على <i class="fas fa-share-alt"></i> ثم اختر "إضافة إلى الشاشة الرئيسية"</div>
                </div>
                <button class="pwa-install-banner__close" aria-label="إغلاق">
                    <i class="fas fa-xmark"></i>
                </button>
            </div>` :
            `<div class="pwa-install-banner__content">
                <div class="pwa-install-banner__icon"><i class="fas fa-download"></i></div>
                <div class="pwa-install-banner__text">
                    <div class="pwa-install-banner__title">ثبّت التطبيق</div>
                    <div class="pwa-install-banner__description">ثبّت التطبيق على جهازك للوصول السريع</div>
                </div>
                <div class="pwa-install-banner__actions">
                    <button class="pwa-install-banner__btn pwa-install-banner__btn--primary" data-action="install">ثبّت</button>
                    <button class="pwa-install-banner__btn pwa-install-banner__btn--secondary" data-action="dismiss">رفض</button>
                </div>
            </div>`;

        document.body.insertBefore(banner, document.body.firstChild);
        sessionStorage.setItem(promptShownKey + '_' + type, 'true');

        // Close button handler (iOS)
        var closeBtn = banner.querySelector('.pwa-install-banner__close');
        if (closeBtn) {
            closeBtn.addEventListener('click', function() {
                banner.classList.add('pwa-install-banner--hidden');
                setTimeout(function() { banner.remove(); }, 300);
            });
        }

        // Action buttons handler (Desktop/Android)
        var installBtn = banner.querySelector('[data-action="install"]');
        var dismissBtn = banner.querySelector('[data-action="dismiss"]');

        if (installBtn) {
            installBtn.addEventListener('click', function() {
                if (installPrompt) {
                    installPrompt.prompt();
                    installPrompt.userChoice.then(function(choiceResult) {
                        if (choiceResult.outcome === 'accepted') {
                            banner.classList.add('pwa-install-banner--hidden');
                        }
                    });
                }
            });
        }

        if (dismissBtn) {
            dismissBtn.addEventListener('click', function() {
                banner.classList.add('pwa-install-banner--hidden');
                setTimeout(function() { banner.remove(); }, 300);
            });
        }

        // Auto-hide after 10 seconds
        setTimeout(function() {
            if (document.body.contains(banner)) {
                banner.classList.add('pwa-install-banner--hidden');
                setTimeout(function() { banner.remove(); }, 300);
            }
        }, 10000);
    }

    /* App installed event */
    window.addEventListener('appinstalled', function() {
        console.log('PWA installed successfully');
        // Clear the shown state so it doesn't show again
        sessionStorage.removeItem(promptShownKey + '_desktop');
    });
})();
