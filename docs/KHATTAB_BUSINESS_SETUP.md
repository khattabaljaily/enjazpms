# 📦 البيانات الأولية - سوبرماركت الخطاب

## ✅ تم الإنشاء بنجاح!

---

## 🏢 النشاط التجاري

**الاسم:** سوبرماركت الخطاب  
**النوع:** سوبرماركت 🛒  
**الباقة:** احترافي (Pro)  
**نوع النسخة:** فروع ومخازن متعددة

### الإعدادات:
```python
{
    "name": "سوبرماركت الخطاب",
    "slug": "khattab-supermarket",
    "business_type": "سوبر ماركت",
    
    "version_type": "multi_branch",
    "max_branches": 10,
    "max_stocks": 15,
    "max_users": 25,
    
    "subscription_plan": "pro",
    "subscription_expires": "27 أبريل 2027",
    
    "timezone": "Africa/Khartoum",
    "language": "ar",
    "currency": "SDG"
}
```

---

## 👤 المستخدم

**اسم المستخدم:** khattab  
**البريد الإلكتروني:** khattabaljaily@gmail.com  
**كلمة المرور:** `$abc@1234$`  
**الاسم الكامل:** محمد الخطاب  
**الدور:** مالك النشاط (Owner)  
**الصلاحيات:** مدير كامل للنشاط التجاري

---

## 🔐 معلومات تسجيل الدخول

```
URL: http://127.0.0.1:8000/accounts/login/
Username: khattab
Password: $abc@1234$
```

---

## 🎯 الخطوات القادمة

### 1. تسجيل الدخول
```bash
# تأكد أن السيرفر يعمل
./manage.py runserver

# افتح المتصفح على
http://127.0.0.1:8000/accounts/login/
```

### 2. إضافة الفروع
بعد تسجيل الدخول، أضف الفروع:
- **الفرع الرئيسي:** الخرطوم - الرياض
- **فرع بحري:** الخرطوم - بحري
- **فرع أم درمان:** أم درمان - السوق

### 3. إضافة المخازن
- **مخزن مركزي:** خارج المدينة (رئيسي)
- **مخزن الفرع الرئيسي:** داخل الفرع
- **مخزن فرع بحري:** داخل الفرع
- **مخزن أم درمان:** داخل الفرع

### 4. فئات المنتجات
- مواد غذائية
  - معلبات
  - أرز وبقوليات
  - زيوت
- مشروبات
  - عصائر
  - مشروبات غازية
  - مياه
- منظفات
- مواد تنظيف
- أدوات منزلية

### 5. إضافة المنتجات
ابدأ بمنتجات بسيطة لاختبار النظام.

---

## 🧪 بيانات تجريبية إضافية

### سيناريوهات أخرى:

#### صيدلية
```python
BusinessType: pharmacy
version_type: single_store
Features: track_expiry, prescriptions, insurance
```

#### شركة إلكترونيات
```python
BusinessType: electronics
version_type: multi_branch
Features: track_serial, warranty, repairs
```

#### محل ملابس
```python
BusinessType: clothing
version_type: single_store
Features: variants (size/color), seasons, returns
```

---

## 🔧 إعادة إنشاء البيانات

إذا أردت إعادة إنشاء البيانات:

```bash
# احذف قاعدة البيانات الحالية
rm db.sqlite3

# أنشئ جداول جديدة
python manage.py migrate

# شغّل السكريبت مرة أخرى
python setup_khattab_business.py
```

---

## 📝 ملاحظات

1. **كلمة المرور:** استخدمنا `$abc@1234$` كما طلبت
2. **البريد:** khattabaljaily@gmail.com
3. **الموقع:** السودان - الخرطوم
4. **العملة:** الجنيه السوداني (SDG)

---

**تاريخ الإنشاء:** 27 أبريل 2026  
**الحالة:** ✅ جاهز للاستخدام
