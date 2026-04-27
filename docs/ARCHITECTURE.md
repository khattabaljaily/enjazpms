# 🏗️ EnjazIMS - System Architecture

**نظام إدارة المخزون ونقاط البيع - البنية التقنية الكاملة**

---

## 📋 جدول المحتويات

1. [نظرة عامة](#نظرة-عامة)
2. [Multi-Tenant Architecture](#multi-tenant-architecture)
3. [نظام التسجيل](#نظام-التسجيل)
4. [Models Structure](#models-structure)
5. [Apps Breakdown](#apps-breakdown)
6. [API Design](#api-design)
7. [Security & Permissions](#security--permissions)

---

## 🎯 نظرة عامة

### المفهوم الأساسي
نظام **Multi-Tenant** يسمح لعدة عملاء (شركات/أنشطة تجارية) باستخدام نفس التطبيق مع عزل كامل للبيانات.

```
EnjazIMS Platform
├── Tenant 1: صيدلية النور
│   ├── Admin: أحمد (مدير النشاط)
│   ├── Employees: 5 موظفين
│   ├── Branches: 3 فروع
│   ├── Items: 1000+ منتج
│   └── Sales, Purchases, etc.
│
├── Tenant 2: سوبر ماركت الخير
│   ├── Admin: محمد
│   ├── Employees: 10 موظفين
│   └── ...
│
└── Tenant 3: مطعم الزهور
    └── ...
```

---

## 🏢 Multi-Tenant Architecture

### نوع الـ Tenancy
**Shared Database with Tenant Isolation**

### المميزات
- ✅ قاعدة بيانات واحدة
- ✅ كل جدول فيه `tenant_id`
- ✅ Middleware تفلتر البيانات تلقائياً
- ✅ سهولة الصيانة والتطوير
- ✅ Backup واحد لكل النظام

### آلية العمل
```python
# كل model يرث من TenantMixin
class Item(TenantMixin):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    # ...
    
    objects = TenantManager()  # يفلتر تلقائياً حسب الـ tenant
```

---

## 👤 نظام التسجيل

### مراحل التسجيل للعميل الجديد

#### المرحلة 1: البيانات الأساسية
```
✅ اسم المستخدم (username)
✅ البريد الإلكتروني (email)
✅ كلمة المرور (password)
✅ تأكيد كلمة المرور
```

#### المرحلة 2: بيانات النشاط التجاري
```
✅ اسم النشاط التجاري
✅ نوع النشاط (صيدلية، سوبر ماركت، مطعم، إلخ)
✅ عنوان النشاط
✅ أرقام التواصل
```

#### المرحلة 3: إعدادات النظام
```
✅ نوع النسخة:
   - محل واحد بمخزن واحد
   - محل واحد بمخازن متعددة
   - فروع متعددة (محلات ومخازن متعددة)
   
✅ عدد المخازن المسموح بها
✅ المنطقة الزمنية
```

### ما يتم إنشاؤه عند التسجيل

```python
# 1. Django User (المستخدم الرئيسي)
user = User.objects.create(
    username='ahmed',
    email='ahmed@pharmacy.com',
    is_staff=False,
    is_superuser=False
)

# 2. Tenant (النشاط التجاري)
tenant = Tenant.objects.create(
    name='صيدلية النور',
    slug='pharmacy-alnour',
    business_type='pharmacy',
    subscription_plan='basic',
    is_active=True
)

# 3. ربط المستخدم بالـ Tenant كمدير
user.tenant = tenant
user.role = 'admin'  # مدير النشاط التجاري
user.is_tenant_admin = True
user.save()

# 4. Settings (إعدادات النشاط)
Settings.objects.create(
    tenant=tenant,
    timezone='Africa/Cairo',
    max_branches=1,
    max_stocks=1,
    version_type='single_store'
)

# 5. Branch افتراضي
Branch.objects.create(
    tenant=tenant,
    name='الفرع الرئيسي',
    is_main=True
)

# 6. Stock افتراضي
Stock.objects.create(
    tenant=tenant,
    name='المخزن الرئيسي',
    branch=branch
)
```

---

## 📊 Models Structure

### Core Models

#### 1. Tenant (العميل/النشاط التجاري)
```python
class Tenant(models.Model):
    # Basic Info
    name = CharField              # اسم النشاط
    slug = SlugField              # alnour-pharmacy
    business_type = ForeignKey    # نوع النشاط
    logo = ImageField
    
    # Contact
    email = EmailField
    phone = CharField
    address = TextField
    
    # Subscription
    subscription_plan = CharField  # basic, pro, enterprise
    subscription_expires = DateField
    is_active = BooleanField
    is_demo = BooleanField
    
    # Settings
    timezone = CharField
    version_type = CharField       # single_store, multi_stock, multi_branch
    max_branches = IntegerField
    max_stocks = IntegerField
    
    # Meta
    created_at = DateTimeField
    updated_at = DateTimeField
```

#### 2. BusinessType (أنواع الأنشطة)
```python
class BusinessType(models.Model):
    name = CharField               # Pharmacy, Supermarket, Restaurant
    name_ar = CharField            # صيدلية، سوبر ماركت، مطعم
    icon = CharField               # FontAwesome icon
    features = JSONField           # مميزات خاصة بكل نوع
```

#### 3. User (المستخدم - Custom User Model)
```python
class User(AbstractUser):
    # Tenant Relation
    tenant = ForeignKey(Tenant)
    
    # User Type
    is_tenant_admin = BooleanField  # مدير النشاط
    role = CharField                # admin, manager, cashier, etc
    
    # Branch & Permissions
    branch = ForeignKey(Branch, null=True)
    permission_group = ForeignKey(PermissionGroup, null=True)
    
    # Profile
    phone = CharField
    avatar = ImageField
    
    # Status
    is_active = BooleanField
```

#### 4. TenantMixin (Base للـ Models المشتركة)
```python
class TenantMixin(models.Model):
    tenant = ForeignKey(Tenant, on_delete=models.CASCADE)
    created_at = DateTimeField(auto_now_add=True)
    updated_at = DateTimeField(auto_now=True)
    created_by = ForeignKey(User, related_name='+')
    updated_by = ForeignKey(User, related_name='+', null=True)
    
    objects = TenantManager()
    
    class Meta:
        abstract = True
```

---

## 📦 Apps Breakdown

### 1. **core** - النواة الأساسية
```
Models:
├── Tenant
├── BusinessType
├── Settings
├── TenantMixin
└── ActivityLog
```

**المسؤوليات:**
- إدارة الـ Tenants
- الإعدادات العامة
- Middleware & Context Processors
- Base Classes & Mixins

---

### 2. **accounts** - المستخدمين والصلاحيات
```
Models:
├── User (Custom User)
├── PermissionGroup
└── UserActivity
```

**المسؤوليات:**
- التسجيل والدخول
- إدارة المستخدمين والموظفين
- نظام الصلاحيات
- Dashboard

---

### 3. **branches** - الفروع
```
Models:
└── Branch
```

**المسؤوليات:**
- إدارة الفروع
- ربط المستخدمين بالفروع
- إعدادات الفروع

---

### 4. **items** - المنتجات والخدمات
```
Models:
├── Category
├── Unit
├── Item
└── ItemVariation
```

**المسؤوليات:**
- إدارة الأصناف
- التصنيفات والوحدات
- الباركود والأسعار
- الصور والتفاصيل

---

### 5. **customers** - العملاء
```
Models:
├── Customer
├── CustomerGroup
└── CustomerTransaction
```

**المسؤوليات:**
- إدارة العملاء
- مجموعات العملاء
- الديون والمعاملات

---

### 6. **suppliers** - الموردين
```
Models:
├── Supplier
└── SupplierTransaction
```

**المسؤوليات:**
- إدارة الموردين
- المستحقات والمعاملات

---

### 7. **sales** - المبيعات
```
Models:
├── Sale
├── SaleItem
└── Payment
```

**المسؤوليات:**
- نقطة البيع POS
- فواتير المبيعات
- المدفوعات والمرتجعات

---

### 8. **purchases** - المشتريات
```
Models:
├── Purchase
├── PurchaseItem
└── Payment
```

**المسؤوليات:**
- فواتير المشتريات
- استلام البضائع
- المدفوعات

---

### 9. **stocks** - المخازن والمخزون
```
Models:
├── Stock
├── StockQuantity
├── StockMovement
└── StockAdjustment
```

**المسؤوليات:**
- إدارة المخازن
- تتبع الكميات
- حركة المخزون
- الجرد

---

### 10. **accounts** - الحسابات المالية
```
Models:
├── Account
├── Transaction
├── JournalEntry
└── Payment
```

**المسؤوليات:**
- شجرة الحسابات
- القيود المحاسبية
- التقارير المالية

---

## 🔌 API Design

### مبادئ التصميم
- ✅ REST API كامل باستخدام DRF
- ✅ كل عملية CRUD عبر AJAX
- ✅ JWT Authentication للـ API
- ✅ Pagination & Filtering
- ✅ Serializers لكل Model

### مثال: Items API

```
GET    /api/items/              # List all items
POST   /api/items/              # Create item
GET    /api/items/{id}/         # Get item detail
PUT    /api/items/{id}/         # Update item
DELETE /api/items/{id}/         # Delete item
GET    /api/items/search/       # Search items
GET    /api/items/low-stock/    # Low stock items
```

### Response Format
```json
{
  "success": true,
  "data": {
    "id": 1,
    "name": "باراسيتامول",
    "code": "PARA001",
    "price": 25.00
  },
  "message": "تم إضافة المنتج بنجاح"
}
```

---

## 🔒 Security & Permissions

### Tenant Isolation
```python
# Middleware
class TenantMiddleware:
    def __call__(self, request):
        if request.user.is_authenticated:
            request.tenant = request.user.tenant

# Manager
class TenantManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(
            tenant=get_current_tenant()
        )
```

### Permission Levels
```
1. Tenant Admin (مدير النشاط)
   - كامل الصلاحيات
   - إدارة المستخدمين
   - الإعدادات

2. Manager (مدير)
   - عمليات البيع والشراء
   - إدارة المخزون
   - التقارير

3. Cashier (كاشير)
   - نقطة البيع فقط
   - عرض المنتجات

4. Custom Groups
   - صلاحيات مخصصة
```

---

## 🚀 خطة التنفيذ

### Phase 1: Core Foundation (أسبوع 1)
- [x] Core app setup
- [ ] Tenant model & BusinessType
- [ ] TenantMixin & TenantManager
- [ ] Custom User model
- [ ] Middleware & Context Processors

### Phase 2: Authentication (أسبوع 1)
- [ ] Registration system
- [ ] Login/Logout
- [ ] Dashboard
- [ ] User management

### Phase 3: Resources (أسبوع 2)
- [ ] Branches
- [ ] Items (الأهم)
- [ ] Categories & Units
- [ ] Customers & Suppliers

### Phase 4: Operations (أسبوع 3)
- [ ] Sales & POS (الأهم)
- [ ] Purchases
- [ ] Stock management
- [ ] Payments

### Phase 5: Reports & Final (أسبوع 4)
- [ ] Accounts module
- [ ] Reports
- [ ] Export/Import
- [ ] Testing & Optimization

---

## 📝 ملاحظات مهمة

### الفروقات عن النظام القديم
1. ✅ `Tenant` بدل `AppClients` (أوضح)
2. ✅ Custom User بدل User + Profile
3. ✅ TenantMixin لكل الـ models
4. ✅ AJAX 100% بدون page reload
5. ✅ REST API كامل
6. ✅ Modern UI (Bootstrap 5 + Cards)
7. ✅ Better code organization

### Best Practices
- ✅ Type hints في كل مكان
- ✅ Docstrings للدوال والـ classes
- ✅ Model validators
- ✅ Audit trail لكل عملية
- ✅ Error handling محسّن
- ✅ Tests للـ critical functions

---

**آخر تحديث:** 27 أبريل 2026
