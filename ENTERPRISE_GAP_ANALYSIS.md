# تقرير Gap Analysis — نسخة المؤسسات (Enterprise Multi-Branch)

> مرحلة تشخيص (Audit) فقط — لا يوجد أي تعديل كود في هذا التقرير.
> المرجع: `ENTERPRISE_ARCHITECTURE_SPEC.md`.
> تاريخ الفحص: 2026-09-22.

## ملاحظة هامة قبل البدء

عند وقت الفحص، شجرة المشروع فيها تعديلات غير محفوظة (uncommitted) في: `apps/accounts/decorators.py`, `apps/accounts/forms.py`, `apps/accounts/models.py`, `apps/accounts/views.py`, `apps/core/templates/components/navbar.html`, `apps/core/templates/components/sidebar.html`, `apps/core/views.py`, `apps/store/views.py`. هذا التقرير وُصف بناءً على حالة الملفات كما هي حالياً (staged + working tree)، ولم تُلمس أي منها. يُنصح بمراجعة/commit أو تجاهل هذه التعديلات قبل بدء أي إعادة هيكلة حتى لا تختلط مع تغييرات المشروع الجديد.

كذلك، آخر commit في التاريخ هو `5f6bd5c Add enterprise multi-branch architecture spec`، ويسبقه commits بعنوان "Merge branch-scoping (multi-branch) feature into main" و"Fix missing branch scoping on child transaction tables..." — أي أن هناك عمل سابق فعلي على نفس الموضوع، وليس بداية من الصفر.

---

## 1. الموديلات الحالية — هل يوجد Branch / Store / Warehouse / Organization؟

**النتيجة: نعم، المفاهيم الأساسية موجودة فعلاً وبشكل ناضج نسبياً.**

### Tenant (`apps/core/models.py`) — يمثل "Organization"
- `version_type`: `single_store` / `multi_stock` / `multi_branch` — **تمييز صريح لنوع النسخة موجود بالفعل في الداتا** (يجيب مباشرة على السؤال رقم 2 أدناه).
- `max_branches`, `max_stocks`, `max_users`: حدود رقمية.
- `subscription_plan`: `basic` / `pro` / `enterprise` — باقة الاشتراك (منفصلة منطقياً عن `version_type`، لكن `PLAN_LIMITS` تربط كل باقة بمجموعة `allowed_version_types` محددة).
- `PLAN_LIMITS`: يقيّد أي `version_type` مسموح به لكل باقة، وأقصى عدد فروع/مخازن/مستخدمين.

### Branch (`apps/core/models.py`)
- حقول: `tenant` (FK)، `name`، `code` (auto-generate بصيغة `BR-001`)، `address`، `phone`، `is_active`، `is_default`.
- `Branch.can_add_branch(tenant)`: يتحقق أن `tenant.version_type == 'multi_branch'` وأن عدد الفروع الحالية أقل من `max_branches`.
- **ملاحظة:** لا يوجد حقل "مدير الفرع" مباشر على Branch نفسه؛ الربط معكوس — عبر `User.branch` + `User.branch_role`.

### Stock (`apps/stocks/models.py`) — يمثل "Warehouse"
- `Stock.branch` (FK اختياري لـ `core.Branch`) — **الربط بين المخزن والفرع موجود بالفعل.**
- `stock_type`: يفرّق بين "مخزن عام" و"مخزن فرع" (`'branch', 'مخزن فرع'`).
- تعليق توضيحي في الكود يذكر صراحة: "في multi_branch: كل مخزن مرتبط بفرع معين".

### User (`apps/accounts/models.py`)
- `User.tenant`: الربط بالمؤسسة.
- `User.branch` (FK اختياري لـ `core.Branch`) — **ربط المستخدم بفرع محدد موجود.**
- `User.branch_role`: `employee` أو `manager` — يحدد هل المستخدم موظف فرع عادي أم مدير فرع.
- `User.is_tenant_admin`: علم يمثل "مدير النشاط" (مدير المؤسسة/الإدارة المركزية).
- `User.is_branch_manager()`: مساعد يتحقق من `branch_id` + `branch_role == manager`.

### apps/store
- **لا علاقة بمفهوم "الفرع" إطلاقاً.** `apps/store` هنا يمثل "المتجر الإلكتروني" (الواجهة العامة online storefront) — `StoreSettings`, `OnlineOrder`, `OnlineOrderLine`. هذا اسم مضلل بالنسبة للمواصفة (المواصفة تتحدث عن "محل/فرع" فيزيائي)، لكنه موديول منفصل تماماً ولا يتقاطع مع منطق الفروع.
- `apps/store/apps.py` لا يحتوي أي منطق صلاحيات أو فروع — مجرد `AppConfig` قياسي.

### apps/core و apps/accounts و apps/employees
راجع أعلاه — كل من `Tenant`, `Branch`, `TenantCapabilities`, `PlatformSettings` في `core`; `User`, `PermissionGroup` في `accounts`; `Employee` (مع `branch` FK) في `employees`.

**الخلاصة لهذا البند:** البنية التحتية للموديلات (Tenant → Branch → Stock → User/Employee) **موجودة ومُطبَّقة فعلياً**، وليست غائبة كما قد يُفترض في مشروع "إعادة هيكلة من الصفر".

---

## 2. تمييز "نوع النسخة" في الداتا

**نعم موجود:** حقل `Tenant.version_type` بالقيم الثلاث المطابقة تماماً لأسماء النسخ الثلاث في المواصفة:
- `single_store` = محل واحد بمخزن واحد
- `multi_stock` = محل واحد بمخازن متعددة
- `multi_branch` = فروع متعددة (محلات ومخازن) — وهي نسخة "Enterprise" في لغة المواصفة.

الحقل يُستخدم فعلياً في القرارات (مثال: `apps/core/views.py:2997` و`3045`: `if tenant.version_type != 'multi_branch': ...`)، وفي `Branch.can_add_branch`، وفي `TenantForm.clean()` حيث تُشتق `version_type`/الحدود الرقمية إجبارياً من الباقة (`PLAN_LIMITS`) بغض النظر عمّا يصل من الطلب — إجراء حماية جيد.

**فجوة ملحوظة:** `version_type` يُشتق حالياً من الباقة (`subscription_plan`) وليس حقلاً مستقلاً يختاره مدير النشاط بحرية — قيمته النهائية تُفرض دائماً بآخر عنصر في `allowed_version_types` الخاص بالباقة (`limits['allowed_version_types'][-1]`). هذا يعني أن اختيار "نوع النسخة" مرتبط 100% بالباقة، ما قد يحتاج مراجعة إذا كان المطلوب مستقبلاً السماح بمزيج مختلف (مثال: باقة pro لكن enterprise-lite).

---

## 3. نظام الصلاحيات الحالي

### الآلية العامة
النظام **لا يستخدم Django Groups/Permissions القياسية** — بل نظامه الخاص المبني على JSON:

```
User → PermissionGroup (per-tenant) → permissions (JSONField: {perm_key: bool}) → Decorator على الـ View
```

- `PermissionGroup.permissions`: JSONField بصيغة `{permission_key: True/False}`.
- `User.permission_groups`: M2M مع `PermissionGroup`.
- `User.get_permission_keys()` / `User.has_perm_key(key)`: منطق التحقق.
- مصدر أسماء الصلاحيات المتاحة: `apps/accounts/permissions.py` (`get_permission_keys()`, `load_permission_schema()`) — لم يُفحص بالتفصيل في هذه الجولة، لكنه المصدر المركزي لقائمة الـ permission keys.

### التطبيق على الـ Views: Decorators (`apps/accounts/decorators.py`)
- `require_permission(key)` / `require_any_permission(*keys)` / `require_all_permissions(*keys)`: تتحقق من `is_superuser` أو `is_tenant_admin` (تجاوز كامل)، ثم `user.has_perm_key(key)`.
- `require_capability(name)`: يتحقق من `TenantCapabilities` الخاصة بالباقة (ميزة مفعّلة أم لا) — مستقل عن الصلاحيات، ولا يُستثنى منه حتى `tenant_admin`.
- `require_plan_feature(name)`: مشابه لكن لميزات الباقة (`Tenant.plan_allows`).
- **`require_company_owner`** — **هذا هو الأهم لمفهوم الفصل بين مدير النشاط ومدير الفرع**: يسمح بالوصول فقط إذا `request.branch is None` (أي مستخدم بلا فرع محدد = مالك الحساب/مركزي)، بصرف النظر عن أي صلاحية أخرى يملكها. يُستخدم فوق `require_permission` لحماية شاشات مثل: إدارة الفروع نفسها، مجموعات الصلاحيات، إعدادات النظام العامة.

### آلية `is_branch_manager()` — نقطة تصميم مهمة يجب مراجعتها
في `User.get_permission_keys()` و`has_perm_key()`: **مدير الفرع (`is_branch_manager()`) يحصل على نفس اتساع صلاحيات "مالك الحساب" الكامل** (كأنه `is_tenant_admin`)، والتعليق في الكود يوضح أن العزل الفعلي بينه وبين فروع أخرى يعتمد فقط على فلترة البيانات (`request.branch` / `for_branch()` / `filter_by_branch_via()`) — **وليس على تضييق مجموعة الصلاحيات نفسها**. هذا يعني: لو نسي مطوّر تطبيق `for_branch()` أو `filter_by_branch_via()` في view/تقرير جديد، فمدير الفرع سيرى/يُعدّل بيانات كل الفروع لأن صلاحياته "كاملة" أصلاً. هذا يستحق أن يكون على رأس قائمة الفجوات (انظر البند 6).

### فلترة البيانات حسب الفرع
- `TenantQuerySet.for_branch(branch)` (في `apps/core/models.py`): متاحة على أي Model يرث `TenantMixin` عبر `TenantManager`. إذا `branch=None` لا تُطبّق فلترة (المستخدم مركزي)، وإلا تُظهر سجلات الفرع + السجلات التاريخية بدون فرع (`branch__isnull=True`) تفادياً لإخفاء بيانات قديمة.
- `apps.core.utils.filter_by_branch_via(qs, branch, field=...)`: نسخة أعم تفلتر عبر علاقة غير مباشرة (مثال: `stock__branch`, `employee__branch`, `supplier__branch`, `agent__branch`, `original_invoice__stock__branch`).
- `request.branch` تُحقن بواسطة `apps.core.middleware.TenantMiddleware`: `request.branch = request.user.branch` (أو `None` للـ superuser/platform staff، أو `None` إن لم يكن للمستخدم فرع).
- استخدام فعلي موثق في: `apps/stocks/views.py`, `apps/agents/views.py`, `apps/employees/views.py`, `apps/suppliers/views.py`, `apps/sales/views.py`, `apps/sales/reports.py`, `apps/stocks/reports.py`.

### الخلاصة
الصلاحيات تُطبَّق عبر **(أ) decorators على الـ views** (تحكم بالإجراء: هل يستطيع تنفيذ العملية) و**(ب) فلترة queryset يدوية بـ `request.branch`** (تحكم بالبيانات: أي سجلات يرى). **لا يوجد middleware أو DB-level enforcement مركزي** يفرض الفلترة تلقائياً (مثل row-level security أو manager مُلزم) — الاعتماد كلياً على أن يستدعي كل مطوّر `for_branch()`/`filter_by_branch_via()` يدوياً في كل view وكل تقرير وكل API. هذا نمط "convention over enforcement" وهو مصدر خطر حقيقي (نقطة فشل واحدة منسية = تسريب بيانات بين الفروع).

---

## 4. هل السجلات التشغيلية مرتبطة بفرع؟

| الموديول | الموديل | مرتبط بفرع مباشرة؟ | طريقة الربط |
|---|---|---|---|
| المبيعات `apps/sales` | `SaleInvoice` | **لا (غير مباشر)** | عبر `stock.branch` (الفاتورة ترتبط بـ `stock`، والمخزن يرتبط بفرع) |
| المبيعات | `SaleReturn` | **لا (غير مباشر، سلسلة أطول)** | عبر `original_invoice.stock.branch` — تم اكتشافه وإصلاحه لاحقاً حسب سجل git (`5019073 Fix SaleReturn/PurchaseReturn branch filter`) |
| المشتريات `apps/purchases` | `PurchaseInvoice` | **لا (غير مباشر)** | عبر `stock.branch` (نفس نمط المبيعات) |
| المشتريات | `PurchaseReturn` | **لا (غير مباشر)** | مشابه لـ `SaleReturn` |
| المصروفات `apps/expenses` | `Expense` | **نعم (مباشر)** | حقل `branch` FK مباشر على الموديل نفسه |
| المخزون `apps/stocks` | `Stock` | **نعم (مباشر)** | حقل `branch` FK مباشر |
| المخزون | `StockQuantity`, `StockTransfer`, `Stocktake`, `StockDestruction` | **لا (غير مباشر)** | عبر `stock.branch` |
| الموظفون `apps/employees` | `Employee` | **نعم (مباشر)** | حقل `branch` FK مباشر |
| الموظفون | `EmployeeAdvance`, `EmployeeSalaryPayment` | **لا (غير مباشر)** | عبر `employee.branch` (مُستخدَم فعلياً بـ `filter_by_branch_via(..., field='employee__branch')`) |
| الوكلاء `apps/agents` | `Agent` | **نعم (مباشر)**، حسب grep لـ `agent__branch` | — |
| الموردون `apps/suppliers` | `Supplier` | **نعم (مباشر)**، حسب grep لـ `supplier__branch` | — |

**لا يوجد موديل تشغيلي بلا أي مسار (مباشر أو غير مباشر) للفرع** ضمن ما تم فحصه. الربط "غير المباشر" عبر `stock.branch` يعمل، لكنه **أضعف من الربط المباشر** المطلوب صراحة في المواصفة (القسم تاسعاً: "المبيعات → الفرع + المستخدم"، "المشتريات → الفرع/المخزن + المستخدم"): فاتورة بيع بلا `stock` (نظرياً) أو `stock.branch=None` تفلت من أي فلترة فرع. كما أن الاعتماد على سلسلة علاقات (`original_invoice__stock__branch`) في كل تقرير جديد يزيد احتمال النسيان مستقبلاً (نفس الخطر المذكور في البند 3).

---

## 5. Treasury (الخزائن) — هل هناك خزينة مركزية منفصلة؟

- `Treasury.branch`: FK اختياري لـ `core.Branch` — **خزينة يمكن ربطها بفرع، وخزينة بلا فرع (`branch=None`) تُعامَل ضمنياً كخزينة مركزية/tenant-wide.**
- `Treasury.is_system_default`: علم "خزينة افتراضية نظامية" — يبدو أنها الخزينة الأساسية لكل tenant (لا يمكن حذفها — محمية في `treasury/views.py`).
- **لا يوجد حقل أو مفهوم صريح باسم "خزينة مدير النشاط" (owner treasury) منفصلة عن خزائن الفروع** كما تصف المواصفة صراحة في القسم ثالثاً ("يمكن توفير خزنة مستقلة لمدير النشاط لتسجيل مصروفاته/معاملاته الشخصية"). حالياً أي خزينة بـ `branch=None` هي بحكم الأمر الواقع "مركزية"، لكن لا يوجد تمييز نوعي (مثال: `treasury_kind = owner / branch / shared`) ولا حماية تمنع مدير فرع (بصلاحياته الواسعة المذكورة في البند 3) من رؤية/التعامل مع خزينة مركزية إن لم تُطبَّق فلترة `for_branch` عليها بدقة.
- `EmployeeAdvance` و`EmployeeSalaryPayment` يرتبطان بـ `Treasury` اختيارياً (دفع نقدي) أو `BankAccount` (دفع بنكي) — أي أن **الربط التقني بين الرواتب/السلف والخزينة المركزية موجود من ناحية البيانات**، لكن لا يوجد منطق عمل صريح يفرض "الرواتب تُصرف فقط من خزينة مركزية وليس خزينة فرع" — القرار متروك حالياً لمن يُنشئ العملية.

**الخلاصة:** يوجد أساس تقني (FK اختياري) يكفي لتمثيل خزينة مركزية، لكن **لا يوجد مفهوم/تصنيف صريح ولا قواعد عمل (business rules)** تفرّق فعلياً بين "خزينة مركزية لمدير النشاط" و"خزينة فرع عادي بلا فرع محدد بالخطأ".

---

## 6. أكبر الفجوات (مرتبة حسب الخطورة/الأولوية)

1. **[حرج] اتساع صلاحيات مدير الفرع الفعلي = صلاحيات مالك الحساب.** `User.get_permission_keys()`/`has_perm_key()` يمنحان `is_branch_manager()` نفس اتساع `is_tenant_admin` بالكامل، والعزل الوحيد هو فلترة البيانات بـ `request.branch`. أي نسيان لاستدعاء `for_branch()`/`filter_by_branch_via()` في view أو تقرير جديد = مدير فرع يرى/يُعدّل بيانات فرع آخر بالكامل، أو حتى شاشات لا يفترض أن يصل إليها لولا حماية `require_company_owner` الإضافية. هذا يخالف مباشرة القسم رابعاً وخامساً من المواصفة ("لا يستطيع... الاطلاع على بيانات فروع أخرى").

2. **[عالٍ] لا يوجد إنفاذ مركزي (enforcement) لعزل الفروع — الاعتماد الكامل على انضباط كل مطوّر.** لا middleware/manager يفرض الفلترة تلقائياً على كل QuerySet؛ كل view/تقرير/API جديد يحتاج تذكّر استدعاء `for_branch()` يدوياً. سجل git (`d06f295 Fix missing branch scoping on child transaction tables and dashboard stats`, `5019073 Fix SaleReturn/PurchaseReturn branch filter`) يثبت أن هذا حدث بالفعل كأخطاء تم اكتشافها وإصلاحها لاحقاً — أي النمط نفسه سيتكرر عند التوسع.

3. **[عالٍ] الربط بين السجلات المالية/التشغيلية والفرع غير مباشر في أهم الموديلات (المبيعات والمشتريات).** `SaleInvoice`/`PurchaseInvoice` لا تحمل `branch` مباشرة بل تُشتق من `stock.branch`. هذا يخالف نص المواصفة الصريح (القسم تاسعاً) الذي يطلب "المبيعات → الفرع + المستخدم" كحقل مباشر، ويجعل أي استعلام تقارير يحتاج JOIN إضافي دائماً مع خطر نسيانه (كما حدث فعلاً مع SaleReturn/PurchaseReturn).

4. **[متوسط-عالٍ] لا يوجد مفهوم صريح لـ"خزينة مدير النشاط" المركزية المنفصلة عن خزائن الفروع.** الاعتماد على `Treasury.branch IS NULL` كاصطلاح ضمني بدون تصنيف نوعي (`treasury_kind`) أو قواعد عمل تمنع الخلط، رغم أن المواصفة تطلبها صراحة (القسم ثالثاً).

5. **[متوسط] عدم وجود ربط صريح بين مدير الفرع (User.branch + branch_role=manager) وBranch نفسها كحقل "مدير الفرع الرسمي".** التصميم الحالي معكوس (على User وليس على Branch)، وهذا يجعل عملية "تعيين/تغيير مدير الفرع" (مطلوبة صراحة في القسم ثانياً) عملية ضمنية (تغيير `branch_role` على مستخدم) بدل عملية إدارية واضحة على مستوى الفرع نفسه، وقد تسمح بوجود أكثر من مستخدم بـ`branch_role='manager'` على نفس الفرع بدون منع صريح على مستوى الموديل.

6. **[متوسط] `version_type` مربوط جبراً بالباقة (`subscription_plan`) ولا يمكن ضبطه باستقلالية.** `TenantForm.clean()` يفرض `version_type` من `PLAN_LIMITS[plan]['allowed_version_types'][-1]` دائماً. هذا يمنع مرونة مستقبلية (مثال: عميل على باقة pro لكن بحاجة فعلية لأكثر من فرع بحدود محدودة) وقد يحتاج مراجعة تصميم قبل توسيع نسخة Enterprise.

7. **[متوسط] Dashboard والتقارير الموحدة بفلتر "كل الفروع / فرع محدد" (القسم سابعاً) غير مؤكد وجودها بشكل منهجي.** الفحص الحالي أظهر استخدام `filter_by_branch_via`/`for_branch` في تقارير متفرقة (`sales/reports.py`, `stocks/reports.py`) لكن لم يُتحقق من وجود Dashboard موحّد لمدير النشاط بفلتر فرع/فترة/مخزن شامل لكل التقارير المنطقية (مبيعات، مشتريات، مصروفات، أرباح، مخزون، رواتب) كما تنص المواصفة — يحتاج فحص تفصيلي لملف/ملفات الـ dashboard قبل الجزم.

8. **[منخفض-متوسط] لا يوجد حقل "نوع خزينة" (Treasury kind) أو تصنيف مماثل يفرّق مركزي/فرع/مشترك، مما يصعّب لاحقاً تفعيل قاعدة "الرواتب تُصرف فقط من خزينة مركزية" بشكل آلي بدل الاعتماد على انضباط المستخدم عند اختيار الخزينة يدوياً.**

---

## 7. خطوات مقترحة للمرحلة القادمة (عالية المستوى فقط — بدون تفاصيل تنفيذ)

> هذه بنود تخطيط لمرحلة تالية، وليست تكليفاً بالتنفيذ الآن.

1. **حسم قرار تصميم أساسي أولاً:** هل تُفصل صلاحيات مدير الفرع فعلياً عن مدير النشاط (Permission Group مستقل بدل "نفس اتساع الصلاحيات + فلترة بيانات فقط")، أم يبقى النمط الحالي مع تشديد الإنفاذ؟ هذا القرار يحدد حجم إعادة الهيكلة بالكامل.
2. **تصميم آلية إنفاذ مركزية لعزل الفروع** بدل الاعتماد الكامل على استدعاء يدوي لكل view (مثال: Manager/QuerySet مُلزم افتراضياً، أو Middleware/Mixin على مستوى Class-Based Views لو أمكن، أو أداة فحص/lint تتحقق أن كل view على موديل يحمل فرعاً يستدعي الفلترة).
3. **تقييم إضافة حقل `branch` مباشر** على `SaleInvoice`/`PurchaseInvoice` (وربما `SaleReturn`/`PurchaseReturn`) بدل الاعتماد فقط على `stock.branch`، مع خطة migration للبيانات القديمة، مع الحفاظ الصارم على عدم التأثير على single_store/multi_stock (القسم الحادي عشر بالمواصفة).
4. **تصميم صريح لمفهوم "خزينة مدير النشاط" المركزية** (تصنيف/علم مستقل، وربما قاعدة عمل تمنع مدير الفرع من الوصول لها حتى لو كانت صلاحياته واسعة).
5. **تصميم عملية إدارية واضحة لتعيين/تغيير مدير الفرع** بدل الاعتماد فقط على تعديل `branch_role` على المستخدم، مع تحديد هل يُسمح بأكثر من مدير لكل فرع أم لا.
6. **جرد شامل لكل Views/APIs/Reports عبر كل التطبيقات (sales, purchases, expenses, stocks, employees, treasury, suppliers, customers, agents...) للتأكد من تطبيق فلترة الفرع فعلياً في كل نقطة وصول للبيانات** — وليس فقط النقاط التي ظهرت في هذا الفحص السريع بالـ grep.
7. **مراجعة/بناء Dashboard موحّد لمدير النشاط** بفلتر (كل الفروع / فرع محدد / فترة / مخزن) يغطي كل التقارير المذكورة في القسم سابعاً من المواصفة، والتأكد من تطبيق نفس فلتر الفرع في الـ API الذي يغذي هذا الـ Dashboard.
8. **تنفيذ خطة الاختبار الكاملة المذكورة في القسم الثاني عشر من المواصفة** (20 سيناريو) كجزء لاحق من عملية القبول، مع تركيز خاص على السيناريوهات 8 و9 و10 و20 (عزل البيانات، عدم تأثر النسخة الأولى/الثانية).
9. **حسم قرار حول `version_type` واستقلاليته عن الباقة** قبل أي تعديل في `TenantForm`/`PLAN_LIMITS`.
10. **التعامل أولاً مع التعديلات غير المحفوظة الحالية في الشجرة** (راجع الملاحظة أول التقرير) — commit أو مراجعة أو تراجع، قبل بدء أي فرع عمل جديد لإعادة الهيكلة، لتفادي تضارب التغييرات.

---

*نهاية التقرير — لم يُعدَّل أي ملف كود ضمن هذا الفحص.*
