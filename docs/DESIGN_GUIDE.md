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

## 📝 ملاحظات

- **ملف CSS واحد فقط**: `static/css/main.css`
- **Font**: Cairo من `static/fonts/cairo/`
- **Animation**: `animate-fade-in` فقط (200ms)
- **Responsive**: Mobile-first design

---

**آخر تحديث:** 27 أبريل 2026  
**الإصدار:** 2.0
