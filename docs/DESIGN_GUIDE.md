# 🎨 EnjazIMS - Design System

**نظام تصميم حديث | Modern, Tailwind-Inspired**

---

## 🚀 Quick Start

### الألوان (CSS Variables)
```css
/* استخدم هذه المتغيرات مباشرة */
var(--primary)          /* #6366f1 / #818cf8 */
var(--surface)          /* Card backgrounds */
var(--text-primary)     /* النص الأساسي */
var(--border-color)     /* الحدود */
```

### Components الأساسية
```html
<!-- Button -->
<button class="btn btn-primary">حفظ</button>

<!-- Card -->
<div class="card">
    <div class="card-body">المحتوى</div>
</div>

<!-- Stat Card -->
<div class="stat-card primary">
    <div class="stat-icon"><i class="fas fa-users"></i></div>
    <div class="stat-value">125</div>
    <div class="stat-label">المستخدمون</div>
</div>

<!-- Alert -->
<div class="alert alert-success">تم بنجاح!</div>

<!-- Badge -->
<span class="badge badge-primary">جديد</span>
```

### Utilities
```html
<div class="d-flex gap-3 mb-4">        <!-- Flexbox + gap + margin -->
<div class="text-center fw-bold">     <!-- Text align + font weight -->
<div class="shadow-md rounded-lg">    <!-- Shadow + border radius -->
```

---

## 🌓 Dark Mode

### كيف تستخدمه
زر التبديل في Navbar يحفظ التفضيل تلقائياً في `localStorage`.

### في الكود
```javascript
toggleTheme()  // تبديل الوضع
localStorage.getItem('theme')  // 'light' أو 'dark'
```

---

## 🎨 نظام الألوان

### Light Mode
```
Primary: #6366f1 (Indigo)
Secondary: #8b5cf6 (Purple)
Success: #10b981 (Emerald)
Warning: #f59e0b (Amber)
Danger: #ef4444 (Red)
Info: #3b82f6 (Blue)

Background: #ffffff (White)
Surface: #f9fafb (Gray-50)
Border: #e5e7eb (Gray-200)
Text Primary: #111827 (Gray-900)
Text Secondary: #6b7280 (Gray-500)
```

### Dark Mode
```
Primary: #818cf8 (Indigo-400)
Secondary: #a78bfa (Purple-400)
Success: #34d399 (Emerald-400)
Warning: #fbbf24 (Amber-400)
Danger: #f87171 (Red-400)
Info: #60a5fa (Blue-400)

Background: #0f172a (Slate-900)
Surface: #1e293b (Slate-800)
Border: #334155 (Slate-700)
Text Primary: #f1f5f9 (Slate-100)
Text Secondary: #94a3b8 (Slate-400)
```

---

## 📐 Spacing & Sizing

```css
/* Spacing (استخدم الـ utilities) */
mb-1, mb-2, mb-3, mb-4    /* margin-bottom */
gap-2, gap-3, gap-4       /* gap */
p-3, p-4                  /* padding */

/* Sizes */
xs: 4px, sm: 8px, md: 16px, lg: 24px, xl: 32px
```

---

## ✅ القواعد الأساسية

### نمط صفحات CRUD (إلزامي)
- صفحة واحدة لكل مورد (List View) تحتوي DataTable.
- زر إضافة واضح أعلى الصفحة.
- الإضافة والتعديل عبر Modal Form فقط.
- الحذف عبر Confirmation Modal فقط.
- جميع العمليات تعمل عبر AJAX بدون إعادة تحميل الصفحة:
    - إضافة
    - تعديل
    - حذف
    - بحث
    - فلترة
    - Pagination
- يمنع استخدام صفحات منفصلة للإضافة/التعديل/الحذف في الوحدات الجديدة.

### ✓ افعل
- استخدم CSS Variables دائماً
- اختبر في Light & Dark mode
- استخدم utility classes
- لا animations إلا للضرورة

### ✗ لا تفعل
- **ممنوع inline styles نهائياً**
- لا تضيف animations معقدة
- لا تستخدم ألوان خارج المتغيرات

---

## � التصميم القياسي لصفحات البيانات (Data Pages Standard)

**المرجع:** صفحة العملاء (Customers) هي النموذج الأساسي لكل صفحات البيانات.

### البنية العامة (Structure)

#### 1. Hero Header (cx-hero)
```html
<div class="cx-hero">
    <div class="cx-hero-left">
        <h4 class="cx-hero-title">العملاء</h4>
        <p class="cx-hero-desc">إدارة وتتبع سجلات زبائن النشاط التجاري</p>
    </div>
    <div class="cx-hero-right">
        <div class="cx-hero-actions">
            <button class="cx-btn-secondary" id="btnImport">
                <i class="fas fa-arrow-down"></i>
                <span>استيراد</span>
            </button>
            <button class="cx-btn-secondary" id="btnExport">
                <i class="fas fa-arrow-up"></i>
                <span>تصدير</span>
            </button>
            <button class="cx-btn-add" id="btnOpenCreate">
                <i class="fas fa-plus"></i>
                <span>إضافة</span>
            </button>
        </div>
    </div>
</div>
```

**المواصفات:**
- خلفية: `var(--surface)`
- Padding: `1rem 1.25rem 0.5rem`
- Border-radius: `14px`
- Box-shadow: `0 1px 3px rgba(0,0,0,0.02)`
- الأزرار: `gap: 0.5rem` بين الأزرار

#### 2. KPI Stats Badges (cx-stats-inline)
```html
<div class="cx-stats-inline">
    <span class="cx-stat-badge cx-stat-badge--primary">
        <i class="fas fa-users"></i>
        <strong>125</strong> إجمالي
    </span>
    <span class="cx-stat-badge cx-stat-badge--success">
        <i class="fas fa-check-circle"></i>
        <strong>98</strong> نشط
    </span>
    <span class="cx-stat-badge cx-stat-badge--secondary">
        <i class="fas fa-pause-circle"></i>
        <strong>27</strong> معطل
    </span>
</div>
```

**المواصفات:**
- Display: `inline-flex`
- Padding: `0.35rem 0.75rem`
- Border-radius: `8px`
- Font-size: `0.875rem`
- Gap: `0.4rem` بين الأيقونة والنص

#### 3. DataTable Container (cx-table-section)
```html
<div class="cx-table-section">
    <div class="cx-table-wrapper">
        <table id="customersTable" class="cx-table">
            <thead>
                <tr>
                    <th>الكود</th>
                    <th>الاسم</th>
                    <th>الهاتف</th>
                    <th>المدينة</th>
                    <th>الرصيد الافتتاحي</th>
                    <th>الحالة</th>
                    <th>الإجراءات</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>
</div>
```

**المواصفات:**
- Background: `var(--surface)`
- Border-radius: `14px`
- Padding: `1rem`
- Table padding: `td { padding: 0.5rem 1rem; }`
- Scrollbar: مخفي مع إمكانية السكرول
- Row density: كثافة مضغوطة (0.5rem padding)

#### 4. Form Modal (cx-modal)
```html
<div class="modal fade" id="customerModal">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content cx-modal">
            <div class="cx-modal-header">
                <h5 class="cx-modal-title">إضافة عميل جديد</h5>
                <button type="button" class="cx-modal-close" data-bs-dismiss="modal">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="cx-modal-body">
                <form id="customerForm">
                    <!-- Form fields -->
                </form>
            </div>
            <div class="cx-modal-footer">
                <button type="button" class="cx-btn-cancel">إلغاء</button>
                <button type="submit" class="cx-btn-save">حفظ</button>
            </div>
        </div>
    </div>
</div>
```

**المواصفات:**
- Border-radius: `20px`
- Max-width: `600px`
- Header: gradient background
- Footer: `justify-content: flex-end` (محاذاة يسار للأزرار)

#### 5. View Modal (cx-view-modal)
```html
<div class="modal fade" id="viewModal">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content cx-view-modal">
            <div class="cx-view-header">
                <h5>تفاصيل العميل</h5>
                <button type="button" data-bs-dismiss="modal">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="cx-view-body">
                <div class="cx-view-grid">
                    <div class="cx-view-item">
                        <span class="cx-view-label">الكود</span>
                        <span class="cx-view-value" id="view_code"></span>
                    </div>
                    <!-- More fields -->
                </div>
            </div>
        </div>
    </div>
</div>
```

**المواصفات:**
- Max-width: `700px`
- Border-radius: `20px`
- Grid: `grid-template-columns: repeat(2, 1fr)` for desktop
- Responsive: Single column على الموبايل

#### 6. Import Modal (cx-import-modal)
```html
<div class="modal fade" id="importModal">
    <div class="modal-dialog modal-dialog-centered">
        <div class="modal-content cx-import-modal">
            <div class="cx-import-header">
                <div>
                    <h5 class="cx-import-title">
                        <i class="fas fa-upload"></i>
                        استيراد العملاء
                    </h5>
                    <p class="cx-import-subtitle">قم برفع ملف Excel أو CSV</p>
                </div>
                <button type="button" class="cx-import-close" data-bs-dismiss="modal">
                    <i class="fas fa-times"></i>
                </button>
            </div>
            <div class="cx-import-body">
                <div class="cx-upload-area" id="uploadArea">
                    <div class="cx-upload-icon">
                        <i class="fas fa-cloud-upload-alt"></i>
                    </div>
                    <h6 class="cx-upload-title">اسحب وأفلت الملف هنا</h6>
                    <p class="cx-upload-text">أو اضغط لاختيار ملف</p>
                    <input type="file" id="importFileInput" accept=".xlsx,.xls,.csv" hidden>
                    <button type="button" class="cx-btn-upload" id="btnChooseFile">
                        <i class="fas fa-folder-open me-1"></i> اختر ملف
                    </button>
                    <p class="cx-upload-hint">الصيغ المدعومة: Excel (.xlsx, .xls) أو CSV (.csv)</p>
                </div>
            </div>
        </div>
    </div>
</div>
```

**المواصفات:**
- Max-width: `600px`
- Upload area: dashed border `2px dashed var(--border-color)`
- Drag & drop: مدعوم بالكامل
- Hover state: تغيير لون الخلفية

### الوظائف المطلوبة (Required Features)

#### Backend Requirements
```python
# Views required for each data page:
- list_view()              # Main page with template
- table_api()              # DataTables AJAX endpoint
- create_api()             # POST: Create new record
- detail_api(pk)           # GET: Get single record details
- update_api(pk)           # PUT/POST: Update existing record
- delete_api(pk)           # DELETE/POST: Delete record
- import_api()             # POST: Import from CSV/Excel
- export_api()             # GET: Export to CSV
- download_template()      # GET: Download CSV template
```

#### Frontend Requirements (JavaScript)
```javascript
// Required functions:
- DataTable initialization with Arabic language
- CRUD operations via AJAX
- Form validation and error display
- Modal open/close handlers
- File upload with drag & drop
- Import/Export handlers
- Toast notifications for all actions
```

### أيقونات الأزرار القياسية

| الوظيفة | الأيقونة | الفئة |
|---------|---------|-------|
| إضافة | `fa-plus` | `cx-btn-add` |
| استيراد | `fa-arrow-down` | `cx-btn-secondary` |
| تصدير | `fa-arrow-up` | `cx-btn-secondary` |
| عرض | `fa-eye` | `btn-action-view` |
| تعديل | `fa-edit` | `btn-action-edit` |
| حذف | `fa-trash-alt` | `btn-action-delete` |

### DataTables Configuration
```javascript
$('#customersTable').DataTable({
    processing: true,
    serverSide: true,
    ajax: {
        url: '{% url "customers:table_api" %}',
        type: 'GET'
    },
    columns: [
        { data: 'code' },
        { data: 'name' },
        // ...
    ],
    language: {
        url: '//cdn.datatables.net/plug-ins/1.13.6/i18n/ar.json',
        // Custom overrides
        emptyTable: 'لا يوجد بيانات',
        zeroRecords: 'لا يوجد نتائج مطابقة',
        // ...
    },
    pageLength: 25,
    ordering: true,
    searching: true
});
```

### CSS Classes Naming Convention

**Prefix System:**
- `cx-*` = Customer/Data page specific styles
- `dash-*` = Dashboard specific
- `btn-*` = Buttons
- `stat-*` = Statistics/KPI badges

**مثال:**
```css
.cx-hero { }            /* Hero header container */
.cx-btn-add { }         /* Add button */
.cx-stat-badge { }      /* KPI badge */
.cx-table { }           /* Main data table */
.cx-modal { }           /* Form modal */
.cx-view-modal { }      /* View details modal */
.cx-import-modal { }    /* Import modal */
```

### الملفات المطلوبة لكل تطبيق

```
apps/[app_name]/
├── models.py          # Model with tenant, created_by, updated_by
├── forms.py           # ModelForm with validation
├── views.py           # 9 views (list, table_api, CRUD, import/export)
├── urls.py            # All URL patterns
├── admin.py           # Admin registration
└── templates/
    └── [app_name]/
        └── [model]_list.html  # Single page with all modals
```

### قواعد التسمية (Naming Standards)

**Models:**
- English names: `Supplier`, `Product`, `Invoice`
- Always include: `tenant`, `created_by`, `updated_by`, `created_at`, `updated_at`

**URLs:**
- `list` → Main page
- `table_api` → DataTables endpoint
- `create_api`, `detail_api`, `update_api`, `delete_api`
- `import_api`, `export_api`, `download_template`

**Templates:**
- `[model]_list.html` only (no separate create/edit pages)

---

## 📝 ملاحظات

- **ملف CSS واحد فقط**: `static/css/main.css`
- **Font**: Cairo من `static/fonts/cairo/`
- **Animation**: `animate-fade-in` فقط (200ms)
- **Responsive**: Mobile-first design
- **CSS للصفحات**: كل تطبيق له ملف CSS خاص في `static/css/[app].css`

---

**آخر تحديث:** 28 أبريل 2026  
**الإصدار:** 3.0
