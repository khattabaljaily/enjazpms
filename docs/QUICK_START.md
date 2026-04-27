# 🚀 EnjazIMS - Quick Start

**دليل البدء السريع**

---

## ⚡ التشغيل

```bash
# 1. تفعيل البيئة الافتراضية
source .env/bin/activate

# 2. تطبيق الهجرات
python manage.py migrate

# 3. إنشاء superuser
python manage.py createsuperuser

# 4. تشغيل السيرفر
python manage.py runserver
```

---

## 📚 الوثائق

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - فهم بنية النظام
- **[DESIGN_GUIDE.md](DESIGN_GUIDE.md)** - نظام التصميم

---

## 🔧 إنشاء App جديد

```bash
mkdir apps/myapp
python manage.py startapp myapp apps/myapp
```

ثم عدّل `apps/myapp/apps.py`:
```python
name = 'apps.myapp'
```

وأضف في `settings.py`:
```python
INSTALLED_APPS += ['apps.myapp']
```
