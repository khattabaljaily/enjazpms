# EnjazIMS — نظام إدارة المخزون

نظام SaaS متعدد المستأجرين (multi-tenant) لإدارة المخزون والمبيعات، موجّه مبدئياً للسوق السوداني مع دعم كامل للغة العربية.

---

## التقنيات

| | |
|---|---|
| **Backend** | Django 4.2، Python 3.8+، MySQL |
| **Frontend** | Bootstrap 5 RTL، jQuery 3.7، DataTables 1.13.6، FontAwesome 6 |
| **الذكاء الاصطناعي** | DeepSeek API |
| **الخط** | Cairo (Google Fonts) |

---

## المميزات الرئيسية

### البنية التحتية
- عزل كامل للبيانات بين المشتركين عبر `TenantMixin`
- ثلاثة أنواع: محل واحد / مخازن متعددة / فروع متعددة
- باقات اشتراك: تجريبي / أساسي / احترافي / مؤسسات
- نظام RBAC: 23 قسم، 151 صلاحية، واجهة split-panel لإدارة المجموعات
- سجل نشاط (Activity Log) لكل العمليات

### الوحدات الأساسية
- **العملاء والموردين** — CRUD + دفتر حسابات (ledger) + دفعات
- **المنتجات** — تصنيفات هرمية، وحدات قياس مع معاملات تحويل، BOM للتصنيع
- **المخازن** — أرصدة افتتاحية، تحويلات بين المخازن، جرد مخزون
- **المبيعات** — فواتير، عروض أسعار، مرتجعات، POS، تسليم مؤجل
- **المشتريات** — فواتير، RFQ، أوامر شراء، مرتجعات
- **المصروفات** — فئات + ربط بالخزائن
- **الخزائن** — كاش وبنك، تحويلات بين الخزائن

### مميزات متقدمة
- **الذكاء الاصطناعي** — دردشة ذكية + رؤى يومية تلقائية (DeepSeek)
- **المتجر الإلكتروني** — واجهة عامة لكل مشترك (slug فريد)، سلة، checkout، جدولة ساعات العمل
- **بوابة العميل** — رابط magic link للعميل يرى فواتيره وكشف حسابه
- **الإشعارات** — تلقائية (مخزون منخفض، فاتورة متأخرة، طلب جديد...) مع تحليل ذكي
- **نسخ احتياطية** — per-tenant بدون mysqldump، الاحتفاظ بآخر 7 أيام
- **دعم فني** — نظام تذاكر مدمج (Support Tickets)

### التقارير (27+ تقرير)
مبيعات، مشتريات، مخزون، مصروفات، خزائن، قائمة الدخل (P&L)

---

## البدء السريع

```bash
# تفعيل البيئة الافتراضية
source .env/bin/activate

# تثبيت المتطلبات
pip install -r requirements.txt

# تطبيق الهجرات
python manage.py migrate

# إنشاء أنواع الأنشطة التجارية
python manage.py create_business_types

# إنشاء المستخدم الأساسي
python manage.py createsuperuser

# تشغيل الخادم
python manage.py runserver
```

---

## هيكل المشروع

```
EnjazIMS/
├── PROJECT/          # إعدادات Django
├── apps/
│   ├── core/         # Tenant، middleware، backup، support tickets
│   ├── accounts/     # User، RBAC، مجموعات الصلاحيات، activity log
│   ├── customers/    # عملاء + بوابة العميل (portal)
│   ├── suppliers/    # موردين
│   ├── items/        # منتجات، تصنيفات، وحدات، BOM
│   ├── stocks/       # مخازن، تحويلات، جرد، تصنيع
│   ├── sales/        # مبيعات، عروض أسعار، POS، مرتجعات
│   ├── purchases/    # مشتريات، RFQ، مرتجعات
│   ├── expenses/     # مصروفات
│   ├── treasury/     # خزائن
│   ├── notifications/# إشعارات ذكية
│   ├── ai/           # دردشة + رؤى (DeepSeek)
│   ├── store/        # متجر إلكتروني عام
│   └── portal/       # بوابة العميل
├── static/
│   ├── css/          # ملفات CSS (main، layout، dashboard + per-feature)
│   └── js/
├── media/
└── manage.py
```

---

## أنماط التطوير

```python
# كل model يرث TenantMixin — عزل تلقائي
class Item(TenantMixin):
    name = models.CharField(max_length=300)

# كل view حساس محمي بصلاحية
@require_permission('add_sale_invoice')
def invoice_create(request): ...

# كل طفرة مالية/مخزنية عبر services.py فقط
@transaction.atomic
def confirm_invoice(invoice): ...
```

```javascript
// AJAX فقط — ممنوع location.reload() أو alert() native
$.ajax({ url: '/sales/api/', ... success: () => showToast('success', '...') });
```

---

## لوحة تحكم المنصة (Admin)

مخصصة للـ superuser ومساعدي المنصة (Platform Staff):
- إدارة المشتركين (تفعيل / تعليق / تجديد)
- تقارير الإيرادات والاشتراكات والنشاط
- إدارة الدعم الفني
- سجل المراجعة (Audit Log)
- إدارة النسخ الاحتياطية

---

**الإصدار:** 1.0 — **الحالة:** إنتاج — **آخر تحديث:** يونيو 2026
