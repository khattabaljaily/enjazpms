document.addEventListener('DOMContentLoaded', function () {
    const csrfToken = getCookie('csrftoken');
    const userModal = new bootstrap.Modal(document.getElementById('userModal'));
    const deleteModalEl = document.getElementById('deleteUserModal');
    const viewModalEl = document.getElementById('viewUserModal');
    const deleteModal = deleteModalEl ? new bootstrap.Modal(deleteModalEl) : null;
    const viewModal = viewModalEl ? new bootstrap.Modal(viewModalEl) : null;
    const userForm = $('#userForm');
    const userIdInput = $('#userId');
    const userModalTitle = $('#userModalTitle');
    const userSubmitBtn = $('#userSubmitBtn');
    const userSearchInput = $('#userSearchInput');
    const usersCardsMobile = $('#usersCardsMobile');
    const deleteUserIdInput = $('#deleteUserId');
    const deleteUserName = $('#deleteUserName');
    const confirmDeleteUserBtn = $('#confirmDeleteUserBtn');
    let currentStatus = '';
    const _perms = window.USER_PERMS || { edit: false, delete: false };

    const usersTable = $('#usersTable').DataTable({
        ajax: {
            url: '/accounts/users/api/table/',
            data: function (d) {
                d.status = currentStatus;
            }
        },
        columns: [
            {
                data: 'username',
                render: function (data, type, row) {
                    const name = row.full_name || data;
                    return `
                        <div class="d-flex flex-column">
                            <strong>${name}</strong>
                        </div>
                    `;
                }
            },
            { data: 'email' },
            { data: 'date_joined' },
            {
                data: 'is_active',
                render: function (value) {
                    if (value) {
                        return '<span class="badge bg-success">نشط</span>';
                    }
                    return '<span class="badge bg-secondary">غير نشط</span>';
                }
            },
            {
                data: 'id',
                orderable: false,
                searchable: false,
                className: 'text-end',
                render: function (data, type, row) {
                    const displayName = row.full_name || row.username || 'المستخدم';
                    return `
                        <button type="button" class="cx-btn-ghost btn-sm btn-view-user" data-id="${data}">
                            <i class="fas fa-eye"></i>
                        </button>
                        ${_perms.edit ? `<button type="button" class="cx-btn-ghost btn-sm btn-edit-user" data-id="${data}"><i class="fas fa-edit"></i></button>` : ''}
                        ${_perms.delete ? `<button type="button" class="cx-btn-ghost btn-sm btn-delete-user" data-id="${data}" data-name="${displayName}"><i class="fas fa-trash"></i></button>` : ''}
                    `;
                }
            }
        ],
        order: [[2, 'desc']],
        pageLength: 25,
        responsive: true,
        dom: '<"d-none"f><"d-none"l>rt<"cx-dt-bottom"ip>',
        language: {
            emptyTable: 'لا توجد بيانات للعرض',
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

    function renderMobileUserCards(rows) {
        if (!usersCardsMobile.length) return;
        if (!rows || !rows.length) {
            usersCardsMobile.html('<div class="cx-empty-state">لا توجد مستخدمين للعرض</div>');
            return;
        }

        const cardsHtml = rows.map(function (user) {
            const name = user.full_name || user.username || '—';
            const email = user.email || '—';
            const statusHtml = user.is_active
                ? '<span class="cx-status cx-status--on"><span class="cx-status-dot"></span>نشط</span>'
                : '<span class="cx-status cx-status--off"><span class="cx-status-dot"></span>غير نشط</span>';

            return `<article class="cx-customer-card">
                <div class="cx-customer-card-head">
                    <div class="cx-customer-card-title-wrap">
                        <h2 class="cx-customer-card-title">${name}</h2>
                        <p class="cx-user-card-subtitle">${email}</p>
                    </div>
                    ${statusHtml}
                </div>
                <div class="cx-customer-card-meta">
                        <span class="cx-customer-card-meta-label">انضم منذ</span>
                        <strong class="cx-customer-card-meta-value">${user.date_joined || '—'}</strong>
                    </div>
                <div class="cx-customer-card-actions">
                    <button type="button" class="cxr-act cxr-act--view btn-view-user" data-id="${user.id}" aria-label="عرض" title="عرض"><i class="fas fa-eye"></i></button>
                    ${_perms.edit ? `<button type="button" class="cxr-act btn-edit-user" data-id="${user.id}" aria-label="تعديل" title="تعديل"><i class="fas fa-edit"></i></button>` : ''}
                    ${_perms.delete ? `<button type="button" class="cxr-act cxr-act--del btn-delete-user" data-id="${user.id}" data-name="${name}" aria-label="حذف" title="حذف"><i class="fas fa-trash"></i></button>` : ''}
                </div>
            </article>`;
        }).join('');

        usersCardsMobile.html(cardsHtml);
    }

    usersTable.on('draw', function () {
        renderMobileUserCards(usersTable.rows({ page: 'current' }).data().toArray());
    });

    function setActiveTab(tab) {
        $('.cx-filter-tab').removeClass('active');
        tab.addClass('active');
        currentStatus = tab.data('status') || '';
        usersTable.ajax.reload();
    }

    $('#btnOpenCreateUser').on('click', function () {
        userForm[0].reset();
        userIdInput.val('');
        userForm.find('.is-invalid').removeClass('is-invalid');
        userForm.find('.js-field-errors').remove();
        userForm.find('.js-form-errors').addClass('d-none').empty();
        renderPermissionGroupCheckboxes([]);
        userModalTitle.text('إضافة مستخدم جديد');
        userModal.show();
    });

    function renderPermissionGroupCheckboxes(selectedGroups) {
        const groups = window.PERMISSION_GROUPS || [];
        const container = $('#permissionGroupsCheckboxes');
        container.empty();
        const normalizedSelected = Array.isArray(selectedGroups)
            ? selectedGroups.map((id) => String(id))
            : [];

        groups.forEach(function (group) {
            const isChecked = normalizedSelected.includes(String(group.id));
            const checkbox = `
                <div class="col-12 col-md-6">
                    <div class="form-check">
                        <input class="form-check-input" type="checkbox" data-group-id="${group.id}" id="permission_group_${group.id}" ${isChecked ? 'checked' : ''}>
                        <label class="form-check-label" for="permission_group_${group.id}">${group.name}</label>
                    </div>
                </div>
            `;
            container.append(checkbox);
        });
    }

    function openUserEdit(userId) {
        const detailUrl = `/accounts/users/api/${userId}/detail/`;

        $.get(detailUrl, function (response) {
            if (!response.success) {
                EnjazIMS.toast(response.message || 'تعذر جلب بيانات المستخدم', 'error');
                return;
            }

            const user = response.data;
            userForm[0].reset();
            userForm.find('.is-invalid').removeClass('is-invalid');
            userForm.find('.js-field-errors').remove();
            userForm.find('.js-form-errors').addClass('d-none').empty();
            userModalTitle.text('تعديل بيانات المستخدم');
            userIdInput.val(user.id);
            $('#id_username').val(user.username);
            $('#id_first_name').val(user.first_name);
            $('#id_last_name').val(user.last_name);
            $('#id_email').val(user.email);
            $('#id_phone').val(user.phone);
            $('#id_is_tenant_admin').prop('checked', user.is_tenant_admin);
            $('#id_is_active').prop('checked', user.is_active);
            $('#id_password').val('');
            $('#id_password_confirm').val('');
            renderPermissionGroupCheckboxes(user.permission_groups || []);

            /* ── Agent-linked user restrictions ── */
            if (user.is_agent_user) {
                $('#id_username').prop('readonly', true).attr('title', 'لا يمكن تغيير اسم مستخدم مندوب');
                $('#id_is_tenant_admin').closest('.col-md-4').hide();
                $('#permissionGroupsCheckboxes').closest('.col-md-6').hide();
                $('#agentUserNote').remove();
                userForm.find('.cx-modal-body').prepend(
                    '<div id="agentUserNote" class="alert alert-warning py-2 px-3 mb-3" style="font-size:.85rem;">' +
                    '<i class="fas fa-user-tie me-1"></i> هذا المستخدم مرتبط بمندوب — لا يمكن تغيير اسم المستخدم أو تعيينه مديراً.</div>'
                );
            } else {
                $('#id_username').prop('readonly', false).removeAttr('title');
                $('#id_is_tenant_admin').closest('.col-md-4').show();
                $('#permissionGroupsCheckboxes').closest('.col-md-6').show();
                $('#agentUserNote').remove();
            }

            userModal.show();
        }).fail(function () {
            EnjazIMS.toast('تعذر جلب بيانات المستخدم', 'error');
        });
    }

    function openUserView(userId) {
        const detailUrl = `/accounts/users/api/${userId}/detail/`;

        $.get(detailUrl, function (response) {
            if (!response.success) {
                EnjazIMS.toast(response.message || 'تعذر جلب بيانات المستخدم', 'error');
                return;
            }

            const user = response.data;
            const name = user.first_name || user.username || '—';
            const initials = (user.first_name || user.username || '—').trim().charAt(0).toUpperCase();
            const color = '#6366f1';
            $('#viewUserAvatar').text(initials).css('background', color);
            $('#viewUserName').text(name);
            $('#viewUserSubtitle').html(`<code class="cxr-code">${user.username || '—'}</code>`);
            $('#viewUserEmail').text(user.email || '—');
            $('#viewUserPhone').text(user.phone || '—');
            $('#viewUserStatus').html(user.is_active
                ? '<span class="cxr-status cxr-status--on"><span class="cxr-status-dot"></span>نشط</span>'
                : '<span class="cxr-status cxr-status--off"><span class="cxr-status-dot"></span>غير نشط</span>');
            $('#viewUserAdmin').text(user.is_tenant_admin ? 'نعم' : 'لا');
            $('#viewEditUserBtn').data('id', user.id);
            if (viewModal) {
                viewModal.show();
            }
        }).fail(function () {
            EnjazIMS.toast('تعذر جلب بيانات المستخدم', 'error');
        });
    }

    $('#usersTable tbody').on('click', '.btn-edit-user', function () {
        const userId = $(this).data('id');
        if (userId) {
            openUserEdit(userId);
        }
    });

    $('#usersTable tbody').on('click', '.btn-delete-user', function () {
        const userId = $(this).data('id');
        const userName = $(this).data('name') || 'هذا المستخدم';
        deleteUserIdInput.val(userId);
        deleteUserName.text(userName);
        if (deleteModal) {
            deleteModal.show();
        }
    });

    $('#usersTable tbody').on('click', '.btn-view-user', function () {
        const userId = $(this).data('id');
        if (userId) {
            openUserView(userId);
        }
    });

    usersCardsMobile.on('click', '.btn-edit-user', function () {
        const userId = $(this).data('id');
        if (userId) {
            openUserEdit(userId);
        }
    });

    usersCardsMobile.on('click', '.btn-delete-user', function () {
        const userId = $(this).data('id');
        const userName = $(this).data('name') || 'هذا المستخدم';
        deleteUserIdInput.val(userId);
        deleteUserName.text(userName);
        if (deleteModal) {
            deleteModal.show();
        }
    });

    usersCardsMobile.on('click', '.btn-view-user', function () {
        const userId = $(this).data('id');
        if (userId) {
            openUserView(userId);
        }
    });

    $('#viewEditUserBtn').on('click', function () {
        const userId = $(this).data('id');
        if (userId) {
            if (viewModal) {
                viewModal.hide();
            }
            openUserEdit(userId);
        }
    });

    if (confirmDeleteUserBtn.length) {
        confirmDeleteUserBtn.on('click', function () {
            const userId = deleteUserIdInput.val();
            if (!userId) return;
            $.ajax({
                url: `/accounts/users/api/${userId}/delete/`,
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken,
                    'X-Requested-With': 'XMLHttpRequest'
                },
                success: function (response) {
                    if (response.success) {
                        if (deleteModal) {
                            deleteModal.hide();
                        }
                        EnjazIMS.toast(response.message || 'تم حذف المستخدم', 'success');
                        usersTable.ajax.reload(null, false);
                        return;
                    }
                    EnjazIMS.toast(response.message || 'تعذر حذف المستخدم', 'error');
                },
                error: function () {
                    EnjazIMS.toast('تعذر حذف المستخدم', 'error');
                }
            });
        });
    }

    userForm.on('submit', function (event) {
        event.preventDefault();
        const busyText = userSubmitBtn.html();
        EnjazIMS.clearFormErrors(userForm);
        EnjazIMS.showLoading(userSubmitBtn);

        const userId = userIdInput.val();
        const url = userId ? `/accounts/users/api/${userId}/update/` : '/accounts/users/api/create/';

        // Collect checked permission group checkboxes
        const selectedGroups = [];
        $('#permissionGroupsCheckboxes input[type="checkbox"]:checked').each(function () {
            selectedGroups.push(String($(this).data('group-id')));
        });
        
        console.log('=== USER FORM SUBMIT DEBUG ===');
        console.log('Selected permission groups:', selectedGroups);
        
        // Remove any existing permission_groups inputs
        userForm.find('input[name="permission_groups"]').remove();
        
        // Create hidden inputs for each selected group
        selectedGroups.forEach(function (groupId) {
            userForm.append(`<input type="hidden" name="permission_groups" value="${groupId}">`);
        });
        
        console.log('Created hidden inputs, total:', selectedGroups.length);

        const payload = userForm.serialize();
        console.log('Serialized payload includes permission_groups:', payload.includes('permission_groups'));
        console.log('=== END SUBMIT DEBUG ===');

        $.ajax({
            url: url,
            method: 'POST',
            data: payload,
            headers: {
                'X-CSRFToken': csrfToken,
                'X-Requested-With': 'XMLHttpRequest'
            },
            success: function (response) {
                EnjazIMS.hideLoading(userSubmitBtn, busyText);
                if (response.success) {
                    userModal.hide();
                    EnjazIMS.toast(response.message, 'success');
                    usersTable.ajax.reload();
                    return;
                }
                if (response.errors) {
                    EnjazIMS.renderFieldErrors(userForm, response.errors);
                }
                EnjazIMS.showFormError(userForm, response.message || 'الرجاء التحقق من الحقول');
            },
            error: function (xhr) {
                EnjazIMS.hideLoading(userSubmitBtn, busyText);
                const response = xhr.responseJSON || {};
                if (response.errors) {
                    EnjazIMS.renderFieldErrors(userForm, response.errors);
                }
                EnjazIMS.showFormError(userForm, response.message || 'تعذر حفظ البيانات');
            }
        });
    });

    $('.cx-filter-tab').on('click', function () {
        setActiveTab($(this));
    });

    userSearchInput.on('keyup', function () {
        usersTable.search(this.value).draw();
    });
});