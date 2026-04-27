# Enjaz IMS - نظام إدارة المخزون ونقاط البيع

**Multi-Tenant Inventory Management & POS System**

---

## 🎯 نظرة عامة

نظام متكامل لإدارة المخزون ونقاط البيع يدعم **عدة عملاء (Multi-Tenant)** من أنواع أعمال مختلفة:
- 💊 صيدليات
- 🛒 سوبر ماركت  
- 🍕 مطاعم
- 👔 ملابس
- 📱 إلكترونيات
- وغيرها...

---

## ✨ المميزات الرئيسية

### 🏢 Multi-Tenant Architecture
- عزل كامل للبيانات بين العملاء
- إعدادات مخصصة لكل نشاط تجاري
- دعم فروع ومخازن متعددة

### 🎨 واجهة مستخدم حديثة (Design System v2.0)
- **Tailwind-Inspired** - تصميم حديث مستوحى من Tailwind
- **Dark/Light Mode** - وضع ليلي/نهاري مع تبديل سلس
- **Minimal Animations** - حركات قليلة وسريعة (200ms)
- **Single CSS File** - ملف CSS واحد فقط (`static/css/main.css`)
- **Responsive** - يعمل على جميع الأجهزة
- **Accessible** - متوافق مع معايير WCAG 2.1 AA

**📖 للمزيد:** راجع [`DESIGN_GUIDE.md`](docs/DESIGN_GUIDE.md)

### ⚡ أداء عالي
- REST API كامل
- DataTables للجداول
- Lazy loading
- Optimized queries

---

## 🚀 البدء السريع

```bash
# 1. تفعيل البيئة
source .env/bin/activate

# 2. تطبيق الهجرات
python manage.py migrate

# 3. تشغيل السيرفر
python manage.py runserver
```

---

## 📚 الوثائق

- **[QUICK_START.md](docs/QUICK_START.md)** - ⚡ البدء السريع
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - 🏭 البنية التقنية
- **[DESIGN_GUIDE.md](docs/DESIGN_GUIDE.md)** - 🎨 نظام التصميم
- **[DEVELOPMENT_STRATEGY.md](docs/DEVELOPMENT_STRATEGY.md)** - 🏗️ استراتيجية التطوير
- **[KHATTAB_BUSINESS_SETUP.md](docs/KHATTAB_BUSINESS_SETUP.md)** - 📦 بيانات التجربة

---

## 📦 البنية الأساسية

```
EnjazIMS/
├── PROJECT/              # إعدادات Django الرئيسية
├── apps/                 # التطبيقات (modular)
│   ├── core/            # النواة (Tenant, Settings)
│   ├── accounts/        # المستخدمين والصلاحيات
│   ├── branches/        # الفروع
│   ├── items/           # المنتجات
│   ├── customers/       # العملاء
│   ├── suppliers/       # الموردين
│   ├── sales/           # المبيعات والـ POS
│   ├── purchases/       # المشتريات
│   └── stocks/          # المخازن والمخزون
├── templates/           # القوالب
├── static/              # الملفات الثابتة
└── docs/                # الوثائق

```

---

## 🛠️ التقنيات المستخدمة

### Backend
- **Django 4.2+** - Framework
- **Django REST Framework** - API
- **MySQL** - Database

### Frontend
- **Bootstrap 5** - UI Framework
- **jQuery** - AJAX & DOM
- **DataTables** - جداول تفاعلية
- **Toastr** - إشعارات
- **FontAwesome** - أيقونات

---

## 👥 نظام الـ Multi-Tenancy

### كل عميل (Tenant) له:
- ✅ مستخدمين ومدير خاص
- ✅ فروع ومخازن خاصة
- ✅ منتجات وعملاء وموردين
- ✅ فواتير ومعاملات منفصلة
- ✅ إعدادات وصلاحيات مخصصة

### آلية العمل:
```python
# كل Model مربوط بـ Tenant
class Item(TenantMixin):
    tenant = ForeignKey(Tenant)
    name = CharField()
    # ...
    
    objects = TenantManager()  # يفلتر تلقائياً
```

---

## 📊 الحالة الحالية

### ✅ منجز
- [x] البنية الأساسية
- [x] Multi-tenant setup
- [x] Custom User Model
- [x] Database schema

### 🚧 قيد التطوير
- [ ] Core app (Tenant, Settings)
- [ ] Authentication system
- [ ] Dashboard
- [ ] Apps implementation

---

## 📝 المساهمة

للمساهمة في المشروع، يرجى:
1. قراءة [ARCHITECTURE.md](docs/ARCHITECTURE.md)
2. قراءة [DESIGN_GUIDE.md](docs/DESIGN_GUIDE.md)
3. اتباع معايير الكود المتفق عليها

---

**آخر تحديث:** 27 أبريل 2026
python manage.py migrate
python manage.py createsuperuser
```

### 6. تشغيل السيرفر
```bash
python manage.py runserver
```

---

## 🎨 Tech Stack

### Backend
- **Django 4.2+**
- **Django REST Framework 3.14+**
- **PostgreSQL** (إنتاج) / **SQLite** (تطوير)

### Frontend
- **HTML5**
- **CSS3** (ملف واحد مخصص)
- **Bootstrap 5**
- **jQuery 3.7+**
- **DataTables**
- **Chart.js** (للرسوم البيانية)

---

## 📋 القواعد الأساسية

### ❌ ممنوع نهائياً
```javascript
alert('...')                    // ❌
confirm('...')                  // ❌
prompt('...')                   // ❌
location.reload()               // ❌
```

### ✅ المطلوب
```javascript
showToast('success', 'تم الحفظ')   // ✅
showConfirmModal({ ... })          // ✅
// تحديث بدون reload                 // ✅
```

---

## 📚 الوثائق المرجعية

جميع الوثائق التحليلية والتصميمية متوفرة في:
- `/Users/khattab/projects/ims/docs/`

تتضمن:
1. تحليل شامل للمشروع الحالي
2. متطلبات النظام الجديد
3. تحسينات قاعدة البيانات
4. دليل AJAX والإشعارات
5. نمط التصميم الموحّد
6. نظام Modals والاستيراد/التصدير
7. دليل البدء السريع

---

## ✅ الخطة القادمة

### المرحلة 1: Setup (يومان)
- [x] إنشاء المشروع والبيئة
- [ ] إنشاء مشروع Django
- [ ] إعداد Settings
- [ ] إنشاء التطبيقات الأساسية

### المرحلة 2: Core (أسبوع)
- [ ] نظام المستخدمين والصلاحيات
- [ ] Models الأساسية
- [ ] Base Templates
- [ ] CSS System

### المرحلة 3: Features (2-3 أسابيع)
- [ ] إدارة المنتجات
- [ ] إدارة العملاء
- [ ] إدارة الموردين
- [ ] المبيعات والمشتريات

### المرحلة 4: Advanced (2 أسبوع)
- [ ] التقارير والإحصائيات
- [ ] تصدير واستيراد
- [ ] Dashboard متقدم

---

**تاريخ الإنشاء:** 27 أبريل 2026  
**الحالة:** 🚧 قيد التطوير  
**الإصدار:** 1.0.0-alpha
