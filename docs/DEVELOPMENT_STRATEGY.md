# 🏗️ استراتيجية التطوير - نظام Snake لإدارة الصيدليات

## 📋 نظرة عامة

نظام **Snake** مخصص لإدارة الصيدليات، ومصمم ليكون **مرن وقابل للتطوير** لدعم سيناريوهات العمل المختلفة: من الصيدلية المستقلة إلى سلسلة الفروع، ومن المخزن الواحد إلى شبكة مخازن موزّعة.

---

## 🎯 السيناريوهات المدعومة

### 1️⃣ محل واحد بمخزن واحد
**مثال:** محل صغير، كشك، بقالة

**الإعدادات:**
```python
version_type = 'single_store'
max_branches = 1
max_stocks = 1
```

**الميزات:**
- نقطة بيع واحدة (POS)
- مخزن واحد
- إدارة مبسطة للمخزون
- كاشير واحد أو اثنين

---

### 2️⃣ صيدلية واحدة بمخازن متعددة
**مثال:** صيدلية كبيرة بمخزن رئيسي ومخزن مبرد

**الإعدادات:**
```python
version_type = 'multi_stock'
max_branches = 1
max_stocks = 5  # أو أكثر
```

**الميزات:**
- نقطة بيع واحدة
- عدة مخازن (مخزن رئيسي + مخازن فرعية)
- تحويل البضائع بين المخازن
- تقارير لكل مخزن

**حالات الاستخدام:**
- مخزن رئيسي خارج الصيدلية + مخزن صغير داخلها
- مخزن للأدوية العادية + مخزن للأدوية المبردة

---

### 3️⃣ فروع ومخازن متعددة
**مثال:** سلسلة صيدليات، موزّع أدوية

**الإعدادات:**
```python
version_type = 'multi_branch'
max_branches = 20
max_stocks = 30
```

**الميزات:**
- عدة فروع (نقاط بيع)
- عدة مخازن (مركزية وفرعية)
- تحويل البضائع بين الفروع والمخازن
- تقارير موحدة ومنفصلة لكل فرع
- إدارة مركزية

**حالات الاستخدام:**
- سلسلة صيدليات (5 فروع + مخزن مركزي)
- موزّع أدوية بالجملة (مخزن رئيسي + 10 صيدليات عميلة)

---

## 🏢 أنماط الصيدليات المدعومة

### 1. الصيدلية المستقلة 💊
**الميزات المطلوبة:**
- تتبع تواريخ الانتهاء والدُفعات (حاسم!)
- نقطة بيع سريعة
- باركود
- تنبيهات مخزون منخفض

### 2. سلسلة صيدليات متعددة الفروع 🏥
**الميزات الإضافية:**
- فروع ومخازن متعددة
- تحويل الأصناف بين الفروع
- تقارير موحدة ومنفصلة لكل فرع
- إدارة مركزية

### 3. موزّع أدوية بالجملة 🚚
**الميزات الإضافية:**
- بيع بوحدات متعددة (شريط / علبة / جملة)
- عقود توريد مع صيدليات أخرى
- تتبع دقيق للدُفعات على نطاق واسع

---

## 🧩 البنية المعمارية

### 1. Multi-Tenant Architecture
كل نشاط تجاري (Tenant) معزول تماماً:

```python
class Tenant(models.Model):
    name = models.CharField('اسم النشاط')
    business_type = models.ForeignKey(BusinessType)
    version_type = models.CharField()  # single_store / multi_stock / multi_branch
    
    # Limits
    max_branches = models.IntegerField()
    max_stocks = models.IntegerField()
    max_users = models.IntegerField()
```

### 2. Models الأساسية

#### Branch (الفرع)
```python
class Branch(TenantMixin):
    tenant = models.ForeignKey(Tenant)
    name = models.CharField('اسم الفرع')
    code = models.CharField('الرمز')
    type = models.CharField()  # store / warehouse / office
    is_main = models.BooleanField()
    address = models.TextField()
```

#### Stock (المخزن)
```python
class Stock(TenantMixin):
    tenant = models.ForeignKey(Tenant)
    branch = models.ForeignKey(Branch, null=True)
    name = models.CharField('اسم المخزن')
    code = models.CharField('الرمز')
    type = models.CharField()  # main / branch / cold / hazardous
```

#### Product (المنتج)
```python
class Product(TenantMixin):
    tenant = models.ForeignKey(Tenant)
    name = models.CharField('اسم المنتج')
    sku = models.CharField('SKU')
    barcode = models.CharField('الباركود')
    category = models.ForeignKey(Category)
    
    # Tracking
    track_expiry = models.BooleanField()  # تاريخ انتهاء الصلاحية
    track_batch = models.BooleanField()  # رقم الدفعة
    track_serial = models.BooleanField()  # للأجهزة الطبية المُرقّمة
```

---

## 🔄 Workflow النموذجي

### سيناريو: سلسلة صيدليات الخطاب

**الإعداد:**
1. **Tenant:** صيدليات الخطاب
2. **BusinessType:** صيدلية
3. **Version:** multi_branch (فروع متعددة)
4. **الفروع:**
   - الفرع الرئيسي (الخرطوم - الرياض)
   - فرع 2 (الخرطوم - بحري)
   - فرع 3 (أم درمان)

5. **المخازن:**
   - مخزن مركزي (خارج المدينة)
   - مخزن الفرع الرئيسي
   - مخزن فرع بحري
   - مخزن فرع أم درمان

**العمليات:**
1. استلام الأدوية في المخزن المركزي
2. توزيعها على الفروع
3. البيع من كل فرع
4. تقارير موحدة

---

## 📊 Apps المقترحة (بالترتيب)

### المرحلة 1: Core Setup ✅
- [x] `core` - Multi-tenant, Settings
- [x] `accounts` - Users, Roles

### المرحلة 2: البيانات الأساسية
- [ ] `branches` - الفروع ونقاط البيع
- [ ] `stocks` - المخازن
- [ ] `categories` - فئات المنتجات
- [ ] `products` - المنتجات

### المرحلة 3: الحركات
- [ ] `purchases` - المشتريات
- [ ] `inventory` - حركة المخزون
- [ ] `sales` - المبيعات
- [ ] `transfers` - التحويلات بين المخازن

### المرحلة 4: الإدارة
- [ ] `suppliers` - الموردين
- [ ] `customers` - العملاء
- [ ] `reports` - التقارير

### المرحلة 5: متقدم
- [ ] `pos` - نقطة البيع
- [ ] `analytics` - تحليلات
- [ ] `notifications` - الإشعارات

---

## 🎨 التصميم المرن

### القواعد:

1. **كل شيء Tenant-based**
   ```python
   class MyModel(TenantMixin):
       tenant = models.ForeignKey(Tenant)
   ```

2. **Feature Flags**
   ```python
   if tenant.business_type.features.get('track_expiry'):
       # عرض حقول تاريخ الانتهاء
   ```

3. **Dynamic Forms**
   ```python
   class ProductForm(forms.ModelForm):
       def __init__(self, *args, tenant=None, **kwargs):
           super().__init__(*args, **kwargs)
           
           if tenant and tenant.business_type.slug == 'pharmacy':
               self.fields['requires_prescription'].required = True
           
           if not tenant.has_feature('track_serial'):
               self.fields.pop('serial_number', None)
   ```

4. **Conditional UI**
   ```html
   {% if tenant.version_type == 'multi_branch' %}
       <select name="branch">...</select>
   {% endif %}
   
   {% if tenant.business_type.slug == 'pharmacy' %}
       <input name="expiry_date" required>
   {% endif %}
   ```

---

## 🚀 الخطوات القادمة

### الأولوية الآن:

1. **Branches App** - إدارة الفروع
   - CRUD للفروع
   - تحديد الفرع الافتراضي
   - عرض حسب version_type

2. **Stocks App** - إدارة المخازن
   - CRUD للمخازن
   - ربط المخازن بالفروع
   - أنواع المخازن (عادي/مبرد/خطر)

3. **Products App** - الأدوية والأصناف
   - Categories هرمية
   - Products مع كل الخيارات
   - Tracking (batch/expiry)

---

## 💡 نصائح التطوير

### ✅ افعل:
- ابدأ بـ MVP لكل app
- استخدم Feature Flags
- اختبر مع tenants مختلفة
- اجعل الـ UI يتكيف حسب business_type

### ❌ لا تفعل:
- لا تكتب code خاص بنوع واحد من الأعمال
- لا تجعل كل الميزات إجبارية
- لا تنسى الـ permissions والـ roles

---

## 📌 الخلاصة

النظام **جاهز معمارياً** لدعم جميع السيناريوهات! 

**المطلوب الآن:** تطوير الـ apps بالترتيب مع مراعاة المرونة في كل خطوة.

---

**آخر تحديث:** 27 أبريل 2026  
**الحالة:** ✅ Multi-tenant جاهز، نبدأ بالـ apps الأساسية
