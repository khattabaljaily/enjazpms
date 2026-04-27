# 🎨 دليل استخدام شعارات إنجاز

## 📁 الملفات المتوفرة

```
static/img/logo/
├── logo-161616.png  → أسود/رمادي داكن (#161616)
├── logo-6547bd.png  → بنفسجي (#6547bd) - لون أساسي
├── logo-cba03e.png  → ذهبي (#cba03e) - لون أساسي
└── logo-ffffff.png  → أبيض (#ffffff)
```

---

## 🎯 أماكن الاستخدام

### 1️⃣ Favicon (أيقونة الموقع)
**الملف:** `logo-6547bd.png`  
**الموقع:** `<head>` في base.html

```html
<link rel="icon" type="image/png" href="{% static 'img/logo/logo-6547bd.png' %}">
<link rel="apple-touch-icon" href="{% static 'img/logo/logo-6547bd.png' %}">
```

**السبب:** اللون البنفسجي هو أحد ألوان الشركة الأساسية، ويظهر بوضوح في جميع المتصفحات.

---

### 2️⃣ صفحة تسجيل الدخول (Login Page)
**Light Mode:** `logo-6547bd.png` (بنفسجي)  
**Dark Mode:** `logo-ffffff.png` (أبيض)

```html
<!-- Light mode: Company color logo -->
<img src="{% static 'img/logo/logo-6547bd.png' %}" alt="إنجاز" class="logo-light">
<!-- Dark mode: White logo -->
<img src="{% static 'img/logo/logo-ffffff.png' %}" alt="إنجاز" class="logo-dark">
```

**CSS:**
```css
[data-theme="light"] .logo-dark,
[data-theme="dark"] .logo-light {
    display: none;
}
```

**السبب:** 
- في الوضع النهاري: خلفية فاتحة (#fafafa) → نستخدم شعار بلون الشركة (بنفسجي)
- في الوضع الليلي: خلفية داكنة (#0a0a0a) → نستخدم شعار أبيض للتباين

---

### 3️⃣ Navbar (شريط التنقل)
**Light Mode:** `logo-161616.png` (أسود)  
**Dark Mode:** `logo-ffffff.png` (أبيض)

```html
<a class="navbar-brand d-flex align-items-center gap-2" href="...">
    <img src="{% static 'img/logo/logo-161616.png' %}" 
         alt="إنجاز" height="32" class="navbar-logo logo-light">
    <img src="{% static 'img/logo/logo-ffffff.png' %}" 
         alt="إنجاز" height="32" class="navbar-logo logo-dark">
</a>
```

**CSS:**
```css
.navbar-logo {
    height: 32px;
    width: auto;
    display: block;
}

[data-theme="light"] .navbar-logo.logo-dark,
[data-theme="dark"] .navbar-logo.logo-light {
    display: none;
}
```

**السبب:**
- الـ Navbar خلفيته بيضاء شفافة في Light mode → شعار أسود
- الـ Navbar خلفيته داكنة شفافة في Dark mode → شعار أبيض

---

### 4️⃣ Dashboard / Welcome Section
**الملف:** `logo-6547bd.png` أو `logo-cba03e.png`

```html
<div class="welcome-card">
    <img src="{% static 'img/logo/logo-6547bd.png' %}" alt="إنجاز" height="64">
    <h1>مرحباً بك في نظام إنجاز</h1>
</div>
```

**السبب:** استخدام ألوان الشركة الأساسية لإبراز الهوية البصرية.

---

### 5️⃣ Sidebar (القائمة الجانبية)
**Light Mode:** `logo-161616.png` (أسود)  
**Dark Mode:** `logo-ffffff.png` (أبيض)

```html
<div class="sidebar-logo">
    <img src="{% static 'img/logo/logo-161616.png' %}" 
         alt="إنجاز" class="logo-light">
    <img src="{% static 'img/logo/logo-ffffff.png' %}" 
         alt="إنجاز" class="logo-dark">
</div>
```

---

### 6️⃣ Emails / PDF Reports
**الملف:** `logo-6547bd.png` أو `logo-cba03e.png`

```html
<img src="{{ STATIC_URL }}img/logo/logo-6547bd.png" alt="إنجاز" height="48">
```

**السبب:** ألوان الشركة الأساسية لعرض احترافي.

---

### 7️⃣ Loading Screen / Splash
**Light Mode:** `logo-6547bd.png`  
**Dark Mode:** `logo-ffffff.png`

---

## 📐 الأحجام المقترحة

| الموقع | الارتفاع | الملاحظات |
|--------|---------|-----------|
| Favicon | 32px | حجم أيقونة المتصفح |
| Login Page | 48-64px | كبير نسبياً |
| Navbar | 32px | متوسط ومتوازن |
| Sidebar | 28-32px | صغير نسبياً |
| Dashboard Welcome | 64-80px | كبير للترحيب |
| Footer | 24-28px | صغير |

---

## 🎨 ألوان الشركة

| اللون | Hex Code | الاستخدام |
|-------|----------|----------|
| بنفسجي | `#6547bd` | **أساسي** - الشعار الرئيسي |
| ذهبي | `#cba03e` | **أساسي** - شعار بديل |
| أسود | `#161616` | للخلفيات الفاتحة |
| أبيض | `#ffffff` | للخلفيات الداكنة |

---

## ✅ القواعد العامة

### افعل:
✅ استخدم الشعار المناسب للخلفية (تباين جيد)  
✅ غيّر الشعار تلقائياً مع تبديل الوضع (Light/Dark)  
✅ استخدم ألوان الشركة في الأماكن المهمة (Login, Welcome)  
✅ حافظ على نسبة العرض إلى الارتفاع

### لا تفعل:
❌ لا تضع شعار أبيض على خلفية فاتحة  
❌ لا تضع شعار أسود على خلفية داكنة  
❌ لا تغير حجم الشعار بشكل غير متناسب  
❌ لا تستخدم أكثر من شعار في نفس الموقع (إلا للتبديل بين الأوضاع)

---

## 🔄 Dynamic Logo Loading (JavaScript)

إذا أردت تحديث الشعار ديناميكياً:

```javascript
function updateLogos() {
    const theme = document.documentElement.getAttribute('data-theme');
    const logos = document.querySelectorAll('[data-logo-light], [data-logo-dark]');
    
    logos.forEach(logo => {
        if (theme === 'dark') {
            logo.src = logo.dataset.logoDark;
        } else {
            logo.src = logo.dataset.logoLight;
        }
    });
}
```

**HTML:**
```html
<img data-logo-light="{% static 'img/logo/logo-161616.png' %}"
     data-logo-dark="{% static 'img/logo/logo-ffffff.png' %}"
     alt="إنجاز">
```

---

## 📝 ملاحظات إضافية

### تحسين الأداء:
- جميع الشعارات PNG بأحجام صغيرة (~20KB)
- استخدم lazy loading للصور الكبيرة
- ضع الشعارات في CDN إذا كان متاحاً

### الوصولية (Accessibility):
```html
<img src="..." alt="إنجاز - نظام إدارة المخزون ونقاط البيع" role="img">
```

### SEO:
- أضف `alt` واضح لكل صورة
- استخدم أسماء ملفات واضحة
- ضع Schema.org markup للموقع

---

**آخر تحديث:** 27 أبريل 2026  
**الحالة:** ✅ جميع الشعارات مطبقة ومتكاملة مع Dark Mode
