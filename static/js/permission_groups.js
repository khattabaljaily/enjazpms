/**
 * EnjazIMS — Permissions Management v3.0
 * Split-panel UI: dirty bar at top, members modal
 */
(function () {
    'use strict';

    /* ── API ─────────────────────────────────────────────────── */
    const API = {
        table:  '/accounts/groups/api/table/',
        create: '/accounts/groups/api/create/',
        detail: (id) => `/accounts/groups/api/${id}/detail/`,
        update: (id) => `/accounts/groups/api/${id}/update/`,
        delete: (id) => `/accounts/groups/api/${id}/delete/`,
    };

    /* ── Section icons ───────────────────────────────────────── */
    const ICONS = {
        'لوحة التحكم':            'fa-gauge-high',
        'إعدادات النشاط التجاري': 'fa-gear',
        'المستخدمين':              'fa-user',
        'المجموعات والصلاحيات':    'fa-shield-halved',
        'العملاء':                 'fa-users',
        'الموردين':                'fa-truck',
        'المنتجات والخدمات':       'fa-box',
        'التصنيفات':               'fa-tags',
        'وحدات القياس':            'fa-weight-scale',
        'المخازن':                 'fa-warehouse',
        'المبيعات':                'fa-file-invoice-dollar',
        'المشتريات':               'fa-cart-shopping',
        'المصروفات':               'fa-money-bill-wave',
        'الخزائن':                 'fa-vault',
        'الذكاء الاصطناعي':        'fa-robot',
        'المتجر الإلكتروني':       'fa-store',
        'الإشعارات':               'fa-bell',
        'التقارير — المبيعات':     'fa-chart-line',
        'التقارير — المشتريات':    'fa-chart-bar',
        'التقارير — المخزون':      'fa-boxes-stacked',
        'التقارير — المصروفات':    'fa-receipt',
        'التقارير — الخزائن':      'fa-coins',
        'التقارير — المالية':      'fa-scale-balanced',
    };

    /* ── State ───────────────────────────────────────────────── */
    const schema   = window.PERMISSION_SCHEMA || {};
    const allUsers = window.USERS_LIST || [];
    const csrf     = getCookie('csrftoken');
    const TOTAL    = Object.values(schema).reduce((s, sec) => s + Object.keys(sec).length, 0);

    let allGroups     = [];
    let activeGroupId = null;
    let activeGroupData = null;
    let originalSnap  = null;
    let isDirty       = false;

    // Members state — array of user IDs currently selected for the group
    let selectedUserIds = [];

    /* ── DOM shortcuts ───────────────────────────────────────── */
    const q  = (sel, ctx = document) => ctx.querySelector(sel);
    const qq = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];

    const groupList      = q('#groupList');
    const sidebarSearch  = q('#sidebarSearch');
    const btnNewGroup        = q('#btnNewGroup');
    const btnNewGroupEmpty   = q('#btnNewGroupEmpty');
    const btnNewGroupSidebar = q('#btnNewGroupSidebar');

    const pmEmpty  = q('#pmEmpty');
    const pmEditor = q('#pmEditor');

    // Editor fields
    const editorAvatar        = q('#editorAvatar');
    const editorNameInput     = q('#editorNameInput');
    const editorDescInput     = q('#editorDescInput');
    const editorIsActive      = q('#editorIsActive');
    const editorIsActiveLabel = q('#editorIsActiveLabel');
    const editorMemberCount   = q('#editorMemberCount');

    // KPI / progress
    const enabledCount     = q('#enabledCount');
    const totalCount       = q('#totalCount');
    const permProgressFill = q('#permProgressFill');

    // Permissions
    const permSections = q('#permSections');
    const permSearch   = q('#permSearch');
    const btnSelectAll = q('#btnSelectAll');
    const btnClearAll  = q('#btnClearAll');
    const pmNoResults  = q('#pmNoResults');

    // Actions
    const btnDeleteGroup = q('#btnDeleteGroup');
    const btnSave        = q('#btnSave');
    const btnDiscard     = q('#btnDiscard');

    // Dirty bar
    const pmDirtyBar  = q('#pmDirtyBar');

    // Members modal
    const membersModalEl       = q('#membersModal');
    const membersModal         = new bootstrap.Modal(membersModalEl);
    const btnOpenMembers       = q('#btnOpenMembers');
    const membersList          = q('#membersList');
    const membersSearch        = q('#membersSearch');
    const membersSelectedCount = q('#membersSelectedCount');
    const btnMembersClearAll   = q('#btnMembersClearAll');
    const btnSaveMembers       = q('#btnSaveMembers');

    // Create modal
    const createGroupModal   = new bootstrap.Modal(q('#createGroupModal'));
    const createGroupForm    = q('#createGroupForm');
    const newGroupName       = q('#newGroupName');
    const newGroupDesc       = q('#newGroupDesc');
    const newGroupActive     = q('#newGroupActive');
    const createGroupSubmit  = q('#createGroupSubmit');

    /* ── Helpers ─────────────────────────────────────────────── */

    function esc(s) {
        return String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function initials(name) {
        return (name || '—').trim().split(/\s+/).slice(0, 2).map(w => w[0]).join('').toUpperCase();
    }

    function pct(n, total) {
        return total ? Math.round((n / total) * 100) : 0;
    }

    /* ── Group list ──────────────────────────────────────────── */

    function loadGroupList() {
        groupList.innerHTML = '<div class="pm-list-placeholder"><span class="pm-spinner"></span><span>جارٍ التحميل…</span></div>';
        fetch(`${API.table}?draw=1&start=0&length=200`)
            .then(r => r.json())
            .then(data => {
                allGroups = (data.data || []).sort((a, b) => a.name.localeCompare(b.name, 'ar'));
                renderGroupList();
            })
            .catch(() => { groupList.innerHTML = '<div class="pm-group-empty-msg">تعذّر تحميل المجموعات</div>'; });
    }

    function renderGroupList() {
        const qStr = sidebarSearch.value.trim().toLowerCase();
        const list = qStr ? allGroups.filter(g => g.name.toLowerCase().includes(qStr)) : allGroups;

        if (!list.length) {
            groupList.innerHTML = `<div class="pm-group-empty-msg">${qStr ? 'لا توجد نتائج مطابقة' : 'لا توجد مجموعات — أنشئ أول مجموعة الآن'}</div>`;
            return;
        }

        groupList.innerHTML = list.map(g => {
            const p = pct(g.permission_count, TOTAL);
            const active = g.id === activeGroupId;
            return `
                <div class="pm-group-item${active ? ' is-active' : ''}${!g.is_active ? ' is-inactive' : ''}"
                     data-id="${g.id}" role="button" tabindex="0">
                    <div class="pm-group-avatar">${esc(initials(g.name))}</div>
                    <div class="pm-group-body">
                        <p class="pm-group-name">${esc(g.name)}</p>
                        <p class="pm-group-meta">${g.member_count} عضو · ${g.permission_count} صلاحية</p>
                        <div class="pm-group-prog-wrap">
                            <div class="pm-group-prog-fill" style="width:${p}%"></div>
                        </div>
                    </div>
                    <span class="pm-group-pct">${p}%</span>
                </div>`;
        }).join('');

        qq('.pm-group-item', groupList).forEach(el => {
            el.addEventListener('click', () => selectGroup(+el.dataset.id));
            el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') selectGroup(+el.dataset.id); });
        });
    }

    /* ── Select / load group ─────────────────────────────────── */

    function selectGroup(id) {
        if (isDirty && activeGroupId !== id) {
            EnjazIMS.confirmAction('لديك تغييرات غير محفوظة. هل تريد الانتقال وفقدانها؟', 'تغييرات غير محفوظة')
                .then(confirmed => { if (confirmed) _doSelectGroup(id); });
            return;
        }
        _doSelectGroup(id);
    }

    function _doSelectGroup(id) {
        isDirty = false;
        activeGroupId = id;
        renderGroupList();

        pmEmpty.style.display  = 'none';
        pmEditor.style.display = 'flex';

        permSections.innerHTML = `<div class="pm-list-placeholder" style="padding:2rem"><span class="pm-spinner"></span><span>جارٍ تحميل الصلاحيات…</span></div>`;

        fetch(API.detail(id))
            .then(r => r.json())
            .then(resp => {
                if (!resp.success) { EnjazIMS.toast('تعذّر تحميل البيانات', 'error'); return; }
                activeGroupData = resp.data;
                populateEditor(resp.data);
                isDirty = false;
                updateDirty();
            })
            .catch(() => EnjazIMS.toast('خطأ في الاتصال', 'error'));
    }

    function populateEditor(group) {
        editorAvatar.textContent          = initials(group.name);
        editorNameInput.value             = group.name;
        editorDescInput.value             = group.description || '';
        editorIsActive.checked            = group.is_active;
        editorIsActiveLabel.textContent   = group.is_active ? 'نشط' : 'غير نشط';

        // Members — store as local state, show count
        selectedUserIds = [...(group.users || [])];
        editorMemberCount.textContent = selectedUserIds.length;

        // Permissions
        renderSections(group.permissions || {});

        originalSnap = snapshot();
    }

    /* ── Dirty tracking ──────────────────────────────────────── */

    function snapshot() {
        return JSON.stringify({
            name:   editorNameInput.value.trim(),
            desc:   editorDescInput.value.trim(),
            active: editorIsActive.checked,
            users:  [...selectedUserIds].sort(),
            perms:  getSelectedPerms(),
        });
    }

    function markDirty() {
        isDirty = snapshot() !== originalSnap;
        updateDirty();
    }

    function updateDirty() {
        pmDirtyBar.classList.toggle('is-visible', isDirty);
    }

    /* ── Get values ──────────────────────────────────────────── */

    function getSelectedPerms() {
        const res = {};
        qq('.pm-perm-cb', permSections).forEach(cb => { res[cb.dataset.perm] = cb.checked; });
        return res;
    }

    /* ── Permissions rendering ───────────────────────────────── */

    function renderSections(selectedPerms) {
        totalCount.textContent = TOTAL;
        permSections.innerHTML = '';

        Object.entries(schema).forEach(([secName, perms]) => {
            const entries       = Object.entries(perms);
            const enabledInSec  = entries.filter(([k]) => selectedPerms[k]).length;
            const icon          = ICONS[secName] || 'fa-circle-dot';
            const p             = pct(enabledInSec, entries.length);
            const badgeCls      = enabledInSec === entries.length ? 'is-full' : enabledInSec === 0 ? 'is-none' : '';

            const secEl = document.createElement('div');
            secEl.className = 'pm-section';
            secEl.dataset.section = secName;

            secEl.innerHTML = `
                <div class="pm-sec-header">
                    <div class="pm-sec-icon"><i class="fas ${esc(icon)}"></i></div>
                    <span class="pm-sec-title">${esc(secName)}</span>
                    <div class="pm-sec-prog">
                        <div class="pm-sec-prog-track">
                            <div class="pm-sec-prog-fill ${badgeCls}" style="width:${p}%"></div>
                        </div>
                        <span class="pm-sec-badge ${badgeCls}" data-sec-badge>${enabledInSec}/${entries.length}</span>
                    </div>
                    <button class="pm-sec-toggle-all" type="button" data-sec-toggle>
                        ${enabledInSec === entries.length ? 'مسح الكل' : 'تحديد الكل'}
                    </button>
                    <i class="fas fa-chevron-down pm-sec-chevron"></i>
                </div>
                <div class="pm-perm-grid">
                    ${entries.map(([key, label]) => {
                        const sensitive = label.includes('(حساس)');
                        const display   = label.replace(' (حساس)', '');
                        const checked   = selectedPerms[key] ? 'checked' : '';
                        return `
                            <label class="pm-perm-item" data-perm-item>
                                <input type="checkbox" class="pm-perm-cb" data-perm="${esc(key)}" ${checked}>
                                <div class="pm-perm-pill"></div>
                                <span class="pm-perm-label${sensitive ? ' is-sensitive' : ''}">${esc(display)}</span>
                            </label>`;
                    }).join('')}
                </div>`;

            // Toggle section open/close
            const header    = q('.pm-sec-header', secEl);
            const toggleBtn = q('[data-sec-toggle]', secEl);

            header.addEventListener('click', e => {
                if (e.target.closest('[data-sec-toggle]')) return;
                secEl.classList.toggle('is-open');
            });

            // Per-section select all
            toggleBtn.addEventListener('click', e => {
                e.stopPropagation();
                const cbs = qq('.pm-perm-cb', secEl);
                const allOn = cbs.every(c => c.checked);
                cbs.forEach(c => c.checked = !allOn);
                refreshSectionBadge(secEl);
                refreshTotals();
                markDirty();
                toggleBtn.textContent = allOn ? 'تحديد الكل' : 'مسح الكل';
            });

            qq('.pm-perm-cb', secEl).forEach(cb => {
                cb.addEventListener('change', () => {
                    refreshSectionBadge(secEl);
                    refreshTotals();
                    markDirty();
                });
            });

            permSections.appendChild(secEl);
        });

        refreshTotals();
    }

    function refreshSectionBadge(secEl) {
        const cbs     = qq('.pm-perm-cb', secEl);
        const enabled = cbs.filter(c => c.checked).length;
        const total   = cbs.length;
        const p       = pct(enabled, total);
        const cls     = enabled === total ? 'is-full' : enabled === 0 ? 'is-none' : '';

        const badge = q('[data-sec-badge]', secEl);
        const fill  = q('.pm-sec-prog-fill', secEl);
        const btn   = q('[data-sec-toggle]', secEl);

        badge.textContent = `${enabled}/${total}`;
        badge.className   = `pm-sec-badge${cls ? ' ' + cls : ''}`;
        fill.style.width  = `${p}%`;
        fill.className    = `pm-sec-prog-fill${cls ? ' ' + cls : ''}`;
        if (btn) btn.textContent = enabled === total ? 'مسح الكل' : 'تحديد الكل';
    }

    function refreshTotals() {
        const checked = qq('.pm-perm-cb:checked', permSections).length;
        const p = pct(checked, TOTAL);
        enabledCount.textContent      = checked;
        totalCount.textContent        = TOTAL;
        permProgressFill.style.width  = `${p}%`;
    }

    /* ── Save ────────────────────────────────────────────────── */

    function saveGroup() {
        const name = editorNameInput.value.trim();
        if (!name) { EnjazIMS.toast('يرجى إدخال اسم المجموعة', 'error'); editorNameInput.focus(); return; }

        const origHtml = btnSave.innerHTML;
        btnSave.disabled = true;
        btnSave.innerHTML = '<span class="pm-spinner" style="width:13px;height:13px;border-width:2px;display:inline-block;vertical-align:middle"></span> جارٍ الحفظ…';

        const fd = new FormData();
        fd.append('name',        name);
        fd.append('description', editorDescInput.value.trim());
        fd.append('is_active',   editorIsActive.checked ? 'on' : '');
        fd.append('permissions', JSON.stringify(getSelectedPerms()));
        selectedUserIds.forEach(uid => fd.append('users[]', uid));

        fetch(API.update(activeGroupId), {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
            body: fd,
        })
            .then(r => r.json())
            .then(resp => {
                btnSave.disabled  = false;
                btnSave.innerHTML = origHtml;
                if (resp.success) {
                    EnjazIMS.toast(resp.message || 'تم الحفظ بنجاح', 'success');
                    isDirty         = false;
                    originalSnap    = snapshot();
                    updateDirty();

                    editorAvatar.textContent        = initials(name);
                    editorIsActiveLabel.textContent = editorIsActive.checked ? 'نشط' : 'غير نشط';

                    const permCount = Object.values(getSelectedPerms()).filter(Boolean).length;
                    const idx = allGroups.findIndex(g => g.id === activeGroupId);
                    if (idx !== -1) {
                        allGroups[idx].name             = name;
                        allGroups[idx].is_active        = editorIsActive.checked;
                        allGroups[idx].permission_count = permCount;
                        allGroups[idx].member_count     = selectedUserIds.length;
                    }
                    renderGroupList();
                } else {
                    EnjazIMS.toast(resp.message || 'تعذّر الحفظ', 'error');
                }
            })
            .catch(() => { btnSave.disabled = false; btnSave.innerHTML = origHtml; EnjazIMS.toast('خطأ في الاتصال', 'error'); });
    }

    /* ── Delete ──────────────────────────────────────────────── */

    function deleteGroup() {
        const name = editorNameInput.value.trim() || 'هذه المجموعة';
        EnjazIMS.confirmAction(`هل تريد حذف المجموعة "${name}"؟ لا يمكن التراجع.`, 'حذف المجموعة')
            .then(confirmed => {
                if (!confirmed) return;
                fetch(API.delete(activeGroupId), {
                    method: 'POST',
                    headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
                })
                    .then(r => r.json())
                    .then(resp => {
                        if (resp.success) {
                            EnjazIMS.toast(resp.message || 'تم الحذف', 'success');
                            allGroups     = allGroups.filter(g => g.id !== activeGroupId);
                            activeGroupId = null;
                            isDirty       = false;
                            pmEditor.style.display = 'none';
                            pmEmpty.style.display  = '';
                            renderGroupList();
                        } else {
                            EnjazIMS.toast(resp.message || 'تعذّر الحذف', 'error');
                        }
                    })
                    .catch(() => EnjazIMS.toast('خطأ في الاتصال', 'error'));
            });
    }

    /* ── Members modal ───────────────────────────────────────── */

    // Tracks the draft selection inside the modal (before confirming)
    let modalDraftIds = [];

    function openMembersModal() {
        // Copy current state into draft
        modalDraftIds = [...selectedUserIds];
        renderMembersList('');
        membersSearch.value = '';
        membersModal.show();
        setTimeout(() => membersSearch.focus(), 300);
    }

    function renderMembersList(filterStr) {
        const q = filterStr.toLowerCase().trim();
        membersList.innerHTML = '';

        const visible = q
            ? allUsers.filter(u => {
                const full = `${u.first_name} ${u.last_name} ${u.username}`.toLowerCase();
                return full.includes(q);
            })
            : allUsers;

        if (!visible.length) {
            membersList.innerHTML = '<div style="padding:1rem;text-align:center;color:var(--text-tertiary);font-size:.82rem;">لا يوجد مستخدمون مطابقون</div>';
            return;
        }

        visible.forEach(u => {
            const checked = modalDraftIds.includes(u.id);
            const row = document.createElement('div');
            row.className = `pm-member-row${checked ? ' is-checked' : ''}`;
            row.dataset.uid = u.id;

            const displayName = (u.first_name && u.last_name)
                ? `${u.first_name} ${u.last_name}`
                : u.username;

            row.innerHTML = `
                <div class="pm-member-avatar">${esc(initials(displayName))}</div>
                <div class="pm-member-info">
                    <div class="pm-member-name">${esc(displayName)}</div>
                    <div class="pm-member-username">@${esc(u.username)}</div>
                </div>
                <div class="pm-member-check"><i class="fas fa-check"></i></div>`;

            row.addEventListener('click', () => {
                const uid = +row.dataset.uid;
                if (modalDraftIds.includes(uid)) {
                    modalDraftIds = modalDraftIds.filter(id => id !== uid);
                    row.classList.remove('is-checked');
                } else {
                    modalDraftIds.push(uid);
                    row.classList.add('is-checked');
                }
                updateMembersSelectedCount();
            });

            membersList.appendChild(row);
        });

        updateMembersSelectedCount();
    }

    function updateMembersSelectedCount() {
        membersSelectedCount.textContent = modalDraftIds.length;
    }

    // Confirm members: commit draft → selectedUserIds, update chip, mark dirty
    function confirmMembers() {
        selectedUserIds = [...modalDraftIds];
        editorMemberCount.textContent = selectedUserIds.length;
        membersModal.hide();
        markDirty();
    }

    /* ── Create group modal ──────────────────────────────────── */

    function openCreate() {
        newGroupName.value = '';
        newGroupDesc.value = '';
        newGroupActive.checked = true;
        q('.js-form-errors', createGroupForm).classList.add('d-none');
        createGroupModal.show();
        setTimeout(() => newGroupName.focus(), 300);
    }

    createGroupForm.addEventListener('submit', e => {
        e.preventDefault();
        const name = newGroupName.value.trim();
        if (!name) { newGroupName.classList.add('is-invalid'); return; }
        newGroupName.classList.remove('is-invalid');

        const origHtml = createGroupSubmit.innerHTML;
        createGroupSubmit.disabled = true;
        createGroupSubmit.innerHTML = '<span class="pm-spinner" style="width:13px;height:13px;border-width:2px;display:inline-block;vertical-align:middle;margin-left:.35rem"></span> جارٍ الإنشاء…';

        const fd = new FormData();
        fd.append('name',        name);
        fd.append('description', newGroupDesc.value.trim());
        fd.append('is_active',   newGroupActive.checked ? 'on' : '');
        fd.append('permissions', '{}');

        fetch(API.create, {
            method: 'POST',
            headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
            body: fd,
        })
            .then(r => r.json())
            .then(resp => {
                createGroupSubmit.disabled  = false;
                createGroupSubmit.innerHTML = origHtml;
                if (resp.success) {
                    EnjazIMS.toast(resp.message || 'تم الإنشاء', 'success');
                    createGroupModal.hide();
                    allGroups.push({
                        id: resp.data.id, name,
                        description: newGroupDesc.value.trim(),
                        is_active: newGroupActive.checked,
                        member_count: 0, permission_count: 0,
                    });
                    allGroups.sort((a, b) => a.name.localeCompare(b.name, 'ar'));
                    renderGroupList();
                    selectGroup(resp.data.id);
                } else {
                    const errEl = q('.js-form-errors', createGroupForm);
                    errEl.textContent = resp.message || 'تعذّر الإنشاء';
                    errEl.classList.remove('d-none');
                }
            })
            .catch(() => {
                createGroupSubmit.disabled  = false;
                createGroupSubmit.innerHTML = origHtml;
                EnjazIMS.toast('خطأ في الاتصال', 'error');
            });
    });

    /* ── Perm search ─────────────────────────────────────────── */

    function filterPerms(filterStr) {
        const lq = filterStr.toLowerCase().trim();
        let anyVisible = false;

        qq('.pm-section').forEach(sec => {
            let secVisible = false;
            qq('.pm-perm-item', sec).forEach(item => {
                const label = q('.pm-perm-label', item);
                const show  = !lq || (label && label.textContent.toLowerCase().includes(lq));
                item.classList.toggle('is-hidden', !show);
                if (show) secVisible = true;
            });
            sec.classList.toggle('is-hidden', !secVisible);
            if (secVisible) { anyVisible = true; if (lq) sec.classList.add('is-open'); }
        });

        pmNoResults.style.display = (!anyVisible && lq) ? 'flex' : 'none';
    }

    /* ── Bindings ────────────────────────────────────────────── */

    btnNewGroup.addEventListener('click', openCreate);
    if (btnNewGroupEmpty)   btnNewGroupEmpty.addEventListener('click', openCreate);
    if (btnNewGroupSidebar) btnNewGroupSidebar.addEventListener('click', openCreate);

    sidebarSearch.addEventListener('input', renderGroupList);

    editorIsActive.addEventListener('change', () => {
        editorIsActiveLabel.textContent = editorIsActive.checked ? 'نشط' : 'غير نشط';
        markDirty();
    });

    [editorNameInput, editorDescInput].forEach(el => {
        el.addEventListener('input', () => {
            if (el === editorNameInput) editorAvatar.textContent = initials(el.value) || '–';
            markDirty();
        });
    });

    btnSelectAll.addEventListener('click', () => {
        qq('.pm-perm-cb', permSections).forEach(cb => cb.checked = true);
        qq('.pm-section', permSections).forEach(sec => refreshSectionBadge(sec));
        refreshTotals();
        markDirty();
    });

    btnClearAll.addEventListener('click', () => {
        qq('.pm-perm-cb', permSections).forEach(cb => cb.checked = false);
        qq('.pm-section', permSections).forEach(sec => refreshSectionBadge(sec));
        refreshTotals();
        markDirty();
    });

    permSearch.addEventListener('input', () => filterPerms(permSearch.value));

    btnSave.addEventListener('click', saveGroup);

    btnDiscard.addEventListener('click', () => {
        if (!activeGroupData) return;
        EnjazIMS.confirmAction('هل تريد تجاهل التغييرات والعودة للبيانات الأصلية؟', 'تجاهل التغييرات')
            .then(confirmed => {
                if (!confirmed) return;
                populateEditor(activeGroupData);
                isDirty = false;
                updateDirty();
            });
    });

    btnDeleteGroup.addEventListener('click', deleteGroup);

    // Members modal
    btnOpenMembers.addEventListener('click', openMembersModal);
    btnSaveMembers.addEventListener('click', confirmMembers);

    membersSearch.addEventListener('input', () => renderMembersList(membersSearch.value));

    btnMembersClearAll.addEventListener('click', () => {
        modalDraftIds = [];
        renderMembersList(membersSearch.value);
    });

    window.addEventListener('beforeunload', e => {
        if (isDirty) { e.preventDefault(); e.returnValue = ''; }
    });

    /* ── Init ────────────────────────────────────────────────── */
    loadGroupList();

})();
