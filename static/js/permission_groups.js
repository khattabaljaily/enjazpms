document.addEventListener('DOMContentLoaded', function () {
    const csrfToken = getCookie('csrftoken');
    const groupModalElement = document.getElementById('groupModal');
    const groupModal = new bootstrap.Modal(groupModalElement);
    const groupForm = $('#groupForm');
    const groupIdInput = $('#groupId');
    const groupModalTitle = $('#groupModalTitle');
    const groupNameInput = $('#groupName');
    const groupDescriptionInput = $('#groupDescription');
    const groupIsActiveInput = $('#groupIsActive');
    const groupSubmitBtn = $('#groupSubmitBtn');
    const groupSearchInput = $('#groupSearchInput');
    const groupsCardsMobile = $('#groupsCardsMobile');
    let activeGroupId = null;

    function renderPermissionSections(schema, selectedPermissions) {
        const sections = Object.entries(schema || {});
        if (!sections.length) {
            return '<div class="text-muted">لا توجد صلاحيات معرفة.</div>';
        }

        return sections.map(function ([sectionName, permissions], index) {
            const items = Object.entries(permissions).map(function ([key, label]) {
                const checked = selectedPermissions && selectedPermissions[key] ? 'checked' : '';
                return `
                    <div class="form-check form-switch mb-2">
                        <input class="form-check-input permission-checkbox" type="checkbox" id="perm_${key}" data-permission="${key}" ${checked}>
                        <label class="form-check-label" for="perm_${key}">${label}</label>
                    </div>
                `;
            }).join('');

            return `
                <div class="accordion-item">
                    <h2 class="accordion-header" id="heading-${index}">
                        <button class="accordion-button collapsed" type="button" data-bs-toggle="collapse" data-bs-target="#collapse-${index}" aria-expanded="false" aria-controls="collapse-${index}">
                            ${sectionName}
                        </button>
                    </h2>
                    <div id="collapse-${index}" class="accordion-collapse collapse" aria-labelledby="heading-${index}" data-bs-parent="#permissionSchemaAccordion">
                        <div class="accordion-body p-3">
                            ${items}
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    }

    function getSelectedPermissions() {
        const permissions = {};
        document.querySelectorAll('.permission-checkbox').forEach(function (input) {
            permissions[input.dataset.permission] = input.checked;
        });
        return permissions;
    }

    const groupsTable = $('#groupsTable').DataTable({
        ajax: {
            url: '/accounts/groups/api/table/',
            data: function (d) {
                d.search = groupSearchInput.val();
            }
        },
        columns: [
            { data: 'name' },
            { data: 'member_count' },
            { data: 'permission_count' },
            {
                data: 'is_active',
                render: function (value) {
                    return value
                        ? '<span class="badge bg-success">نشط</span>'
                        : '<span class="badge bg-secondary">غير نشط</span>';
                }
            },
            {
                data: 'id',
                orderable: false,
                searchable: false,
                className: 'text-end',
                render: function (data, type, row) {
                    return `
                        <button type="button" class="cx-btn-ghost btn-sm btn-edit-group" data-id="${data}">
                            <i class="fas fa-edit"></i>
                        </button>
                        <button type="button" class="cx-btn-ghost btn-sm btn-delete-group" data-id="${data}" data-name="${row.name}">
                            <i class="fas fa-trash"></i>
                        </button>
                    `;
                }
            }
        ],
        order: [[0, 'asc']],
        pageLength: 25,
        responsive: true,
        dom: '<"d-none"f><"d-none"l>rt<"cx-dt-bottom"ip>',
        language: {
            emptyTable: 'لا توجد مجموعات بعد',
            loadingRecords: 'جارٍ التحميل...',
            zeroRecords: 'لم يتم العثور على نتائج',
            paginate: {
                first: 'الأول',
                last: 'الأخير',
                next: 'التالي',
                previous: 'السابق'
            }
        }
    });

    function renderMobileGroupCards(rows) {
        if (!groupsCardsMobile.length) return;
        if (!rows || !rows.length) {
            groupsCardsMobile.html('<div class="cx-empty-state">لا توجد مجموعات للعرض</div>');
            return;
        }

        const cardsHtml = rows.map(function (group) {
            return `<article class="cx-customer-card">
                <div class="cx-customer-card-head">
                    <div class="cx-customer-card-title-wrap">
                        <h2 class="cx-customer-card-title">${group.name}</h2>
                    </div>
                    ${group.is_active
                        ? '<span class="cx-status cx-status--on"><span class="cx-status-dot"></span>نشط</span>'
                        : '<span class="cx-status cx-status--off"><span class="cx-status-dot"></span>غير نشط</span>'}
                </div>
                <div class="cx-customer-card-meta">
                    <div>
                        <span class="cx-customer-card-meta-label">عدد الأعضاء</span>
                        <strong class="cx-customer-card-meta-value">${group.member_count}</strong>
                    </div>
                    <div>
                        <span class="cx-customer-card-meta-label">عدد الصلاحيات</span>
                        <strong class="cx-customer-card-meta-value">${group.permission_count}</strong>
                    </div>
                </div>
                <div class="cx-customer-card-actions">
                    <button type="button" class="cxr-act cxr-act--view btn-edit-group" data-id="${group.id}" title="تعديل"><i class="fas fa-edit"></i></button>
                    <button type="button" class="cxr-act cxr-act--del btn-delete-group" data-id="${group.id}" data-name="${group.name}" title="حذف"><i class="fas fa-trash"></i></button>
                </div>
            </article>`;
        }).join('');

        groupsCardsMobile.html(cardsHtml);
    }

    groupsTable.on('draw', function () {
        renderMobileGroupCards(groupsTable.rows({ page: 'current' }).data().toArray());
    });

    function resetGroupForm() {
        groupIdInput.val('');
        groupNameInput.val('');
        groupDescriptionInput.val('');
        groupIsActiveInput.prop('checked', true);
        $('#groupForm .js-form-errors').addClass('d-none').empty();
        $('#groupForm .is-invalid').removeClass('is-invalid');
        $('#groupForm .js-field-errors').remove();
        $('#permissionSchemaAccordion').html(renderPermissionSections(window.PERMISSION_SCHEMA, {}));
    }

    function openGroupEdit(groupId) {
        $.get(`/accounts/groups/api/${groupId}/detail/`, function (response) {
            if (!response.success) {
                EnjazIMS.toast(response.message || 'تعذر جلب بيانات المجموعة', 'error');
                return;
            }

            const group = response.data;
            activeGroupId = group.id;
            groupModalTitle.text('تعديل مجموعة الصلاحيات');
            groupIdInput.val(group.id);
            groupNameInput.val(group.name);
            groupDescriptionInput.val(group.description || '');
            groupIsActiveInput.prop('checked', group.is_active);
            $('#permissionSchemaAccordion').html(renderPermissionSections(window.PERMISSION_SCHEMA, group.permissions || {}));
            groupModal.show();
        }).fail(function () {
            EnjazIMS.toast('تعذر جلب بيانات المجموعة', 'error');
        });
    }

    $('#btnOpenCreateGroup').on('click', function () {
        activeGroupId = null;
        groupModalTitle.text('إضافة مجموعة جديدة');
        resetGroupForm();
        groupModal.show();
    });

    $('#groupsTable tbody').on('click', '.btn-edit-group', function () {
        const groupId = $(this).data('id');
        if (groupId) {
            openGroupEdit(groupId);
        }
    });

    $('#groupsTable tbody').on('click', '.btn-delete-group', function () {
        const groupId = $(this).data('id');
        const groupName = $(this).data('name') || 'هذه المجموعة';
        if (!groupId) return;
        EnjazIMS.confirmAction(`هل تريد حذف المجموعة ${groupName}?`, 'تأكيد الحذف')
            .then(function (confirmed) {
                if (!confirmed) return;
                $.ajax({
                    url: `/accounts/groups/api/${groupId}/delete/`,
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    success: function (response) {
                        if (response.success) {
                            EnjazIMS.toast(response.message || 'تم حذف المجموعة', 'success');
                            groupsTable.ajax.reload(null, false);
                            return;
                        }
                        EnjazIMS.toast(response.message || 'تعذر حذف المجموعة', 'error');
                    },
                    error: function () {
                        EnjazIMS.toast('تعذر حذف المجموعة', 'error');
                    }
                });
            });
    });

    groupsCardsMobile.on('click', '.btn-edit-group', function () {
        const groupId = $(this).data('id');
        if (groupId) {
            openGroupEdit(groupId);
        }
    });

    groupsCardsMobile.on('click', '.btn-delete-group', function () {
        const groupId = $(this).data('id');
        const groupName = $(this).data('name') || 'هذه المجموعة';
        if (!groupId) return;
        EnjazIMS.confirmAction(`هل تريد حذف المجموعة ${groupName}?`, 'تأكيد الحذف')
            .then(function (confirmed) {
                if (!confirmed) return;
                $.ajax({
                    url: `/accounts/groups/api/${groupId}/delete/`,
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    success: function (response) {
                        if (response.success) {
                            EnjazIMS.toast(response.message || 'تم حذف المجموعة', 'success');
                            groupsTable.ajax.reload(null, false);
                            return;
                        }
                        EnjazIMS.toast(response.message || 'تعذر حذف المجموعة', 'error');
                    },
                    error: function () {
                        EnjazIMS.toast('تعذر حذف المجموعة', 'error');
                    }
                });
            });
    });

    groupForm.on('submit', function (event) {
        event.preventDefault();
        const busyText = groupSubmitBtn.html();
        EnjazIMS.clearFormErrors(groupForm);
        EnjazIMS.showLoading(groupSubmitBtn);

        const payload = {
            name: groupNameInput.val().trim(),
            description: groupDescriptionInput.val().trim(),
            is_active: groupIsActiveInput.is(':checked') ? 'on' : '',
            permissions: JSON.stringify(getSelectedPermissions()),
        };

        const groupId = groupIdInput.val();
        const url = groupId ? `/accounts/groups/api/${groupId}/update/` : '/accounts/groups/api/create/';

        $.ajax({
            url: url,
            method: 'POST',
            data: payload,
            headers: {
                'X-CSRFToken': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            success: function (response) {
                EnjazIMS.hideLoading(groupSubmitBtn, busyText);
                if (response.success) {
                    groupModal.hide();
                    EnjazIMS.toast(response.message || 'تم حفظ المجموعة', 'success');
                    groupsTable.ajax.reload();
                    return;
                }
                if (response.errors) {
                    EnjazIMS.renderFieldErrors(groupForm, response.errors);
                }
                EnjazIMS.showFormError(groupForm, response.message || 'الرجاء التحقق من الحقول');
            },
            error: function (xhr) {
                EnjazIMS.hideLoading(groupSubmitBtn, busyText);
                const response = xhr.responseJSON || {};
                if (response.errors) {
                    EnjazIMS.renderFieldErrors(groupForm, response.errors);
                }
                EnjazIMS.showFormError(groupForm, response.message || 'تعذر حفظ البيانات');
            }
        });
    });

    groupSearchInput.on('keyup', function () {
        groupsTable.search(this.value).draw();
    });

    resetGroupForm();
});
