# نظام تعدد الفروع (Multi-Branch Scoping) — دليل التصميم والتطبيق

هذا الملف يوثّق تصميم وتنفيذ ميزة **تعدد الفروع** لباقة "المؤسسات" (Enterprise / multi_branch)
في نظام ENJAZ PMS، بحيث يمكن تكرار نفس النمط بالضبط في مشروع `enjazims` لاحقاً.

تم تنفيذ هذا العمل بالكامل على فرع Git محلي: `feature/branch-scoping-phase0`.

---

## 1. المبدأ الأساسي

- كل مستخدم (`accounts.User`) ينتمي لفرع واحد فقط (`user.branch`)، أو لا فرع له (`branch=None`)
  إن كان مستخدماً مركزياً/أدمن يرى كل الفروع.
- الفرع (`core.Branch`) هو نموذج موجود مسبقاً، مرتبط بـ tenant.
- الميزة **لا تؤثر إطلاقاً** على المشتركين من باقة single_store أو multi_warehouse — لأن كل الفلترة
  الجديدة تعتمد على `request.branch`، وهذا يكون `None` دائماً لأي مستخدم بلا فرع مُعيَّن، وفي هذه
  الحالة كل الفلاتر الجديدة تتحول تلقائياً إلى no-op (لا تُغيّر شيئاً في النتائج).
- أي سجل بدون فرع محدد (`branch=NULL`) يظل **ظاهراً دائماً** لكل الفروع، لتفادي إخفاء بيانات قديمة
  (سجلات أُنشئت قبل تفعيل الفروع، أو أُنشئت بواسطة مستخدم مركزي).

---

## 2. القطع الأساسية (Building Blocks)

### 2.1 `request.branch` — عبر `apps/core/middleware.py` (`TenantMiddleware`)

بالضبط مثل `request.tenant`، يُضبط `request.branch` في كل فرع من منطق الـ middleware:

```python
# /admin/ أو superuser/platform_staff أو مستخدم غير مسجّل الدخول:
request.branch = None

# مستخدم عادي مسجّل الدخول:
request.branch = request.user.branch
```

### 2.2 `TenantQuerySet.for_branch()` / `TenantManager.for_branch()` — في `apps/core/models.py`

كل نموذج يرث من `TenantMixin` يحصل تلقائياً على هذه الدالة:

```python
def for_branch(self, branch):
    """
    فلترة البيانات حسب فرع معين (للنسخة multi_branch فقط).
    branch=None لا يفلتر شيء (المستخدم المركزي/الأدمن أو tenant بدون فروع).
    سجلات بدون فرع محدد (branch=NULL) تظل ظاهرة لتفادي إخفاء بيانات قديمة.
    """
    if branch is None:
        return self
    return self.filter(models.Q(branch=branch) | models.Q(branch__isnull=True))
```

الاستخدام في أي view:

```python
qs = SomeModel.objects.for_tenant(tenant).for_branch(getattr(request, 'branch', None))
```

**شرط استخدام `.for_branch()` مباشرة:** النموذج يجب أن يكون فيه حقل `branch` مباشر (FK لـ `core.Branch`)،
ويجب أن يرث من `TenantMixin` (أي أن `objects` من نوع `TenantManager`).

### 2.3 `filter_by_branch_via()` — في `apps/core/utils.py`

للنماذج التي **لا** تملك حقل `branch` مباشر، لكنها ترتبط بنموذج آخر يملكه (مثال: فاتورة المبيعات
ترتبط بـ `stock`، والمخزن `Stock` هو من يملك `branch`):

```python
def filter_by_branch_via(qs, branch, field='stock__branch'):
    """
    فلترة queryset حسب الفرع عبر علاقة غير مباشرة (مثال: فاتورة → مخزن → فرع).
    branch=None لا يفلتر شيء. السجلات المرتبطة بمخزن بدون فرع تظل ظاهرة.
    """
    if branch is None:
        return qs
    from django.db.models import Q
    return qs.filter(Q(**{field: branch}) | Q(**{f'{field}__isnull': True}))
```

الاستخدام:

```python
qs = filter_by_branch_via(SaleInvoice.objects.for_tenant(tenant), getattr(request, 'branch', None))
```

### 2.4 فلترة يدوية لنماذج لا ترث من `TenantMixin`

بعض النماذج (مثل `accounts.UserActivity`) هي `models.Model` عادية (ليست `TenantMixin`)، فلا تملك
`.for_branch()`. في هذه الحالة الفلترة تُكتب يدوياً بنفس المنطق:

```python
branch = getattr(request, 'branch', None)
if branch is not None:
    qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True))
```

---

## 3. النمط المتبع لكل نموذج جديد يحتاج فرع (Phase 2 Pattern)

عند إضافة دعم الفروع لنموذج جديد (Customer, Supplier, Employee, Agent, Treasury, BankAccount,
Expense, UserActivity — تم تطبيقه على كل هذه):

1. **إضافة الحقل** في `models.py`:
   ```python
   branch = models.ForeignKey(
       'core.Branch',
       on_delete=models.SET_NULL,
       verbose_name='الفرع',
       related_name='<اسم_مناسب>',
       null=True,
       blank=True,
   )
   ```
2. **توليد الـ migration**: `python manage.py makemigrations <app>`
3. **فلترة القوائم/الجداول** في `views.py` — إضافة `.for_branch(getattr(request, 'branch', None))`
   على أي queryset يُعرض في list/table/dropdown.
4. **ختم الفرع تلقائياً عند الإنشاء** — وليس عبر واجهة اختيار يدوية (لأن كل مستخدم له فرع واحد فقط):
   ```python
   obj.tenant = tenant
   obj.branch = getattr(request, 'branch', None)
   ```
5. **عدم تقييد views التعديل/الحذف/الجلب الفردي بالفرع** — إذا كان المستخدم يملك صلاحية الوصول
   للسجل أصلاً (عبر tenant)، لا نمنعه من فتح سجل تابع لفرع آخر عبر رابط مباشر (نفس فلسفة النظام
   الحالي التي تعتمد على الصلاحيات وليس القيود الصارمة على مستوى البيانات).

---

## 4. النمط المتبع للنماذج التي تُشتق فرعها من علاقة أخرى (Phase 1 Pattern)

`Stock` (المخزن) كان يملك حقل `branch` مسبقاً (موجود قبل هذا العمل). المبيعات والمشتريات
(`SaleInvoice`, `SaleReturn`, `SaleQuote`, `PurchaseInvoice`, `PurchaseReturn`) **لا تملك** حقل
`branch` خاص بها، لأنها مرتبطة أصلاً بـ `stock`، والمخزن هو من يحدد الفرع.

**القرار:** لا نضيف حقل `branch` مكرر لهذه النماذج، بل:
- نفلتر القوائم عبر `filter_by_branch_via(qs, branch, field='stock__branch')`.
- نفلتر قائمة المخازن (`stocks` dropdown) المعروضة عند إنشاء فاتورة عبر `Stock.objects.for_branch(branch)`
  — بحيث لا يستطيع المستخدم صاحب الفرع اختيار مخزن تابع لفرع آخر أصلاً، فتكون الفاتورة تلقائياً
  مرتبطة بالفرع الصحيح دون الحاجة لحقل إضافي.

هذا النمط أفضل من تكرار حقل `branch` في كل نموذج مرتبط، ويجب اتباعه في `enjazims` لأي نموذج آخر
يرتبط بـ `Stock` (أو أي نموذج آخر يملك `branch` بالفعل).

---

## 5. التحويلات بين الفروع (Inter-branch Transfers) — لا تُقيَّد بالفرع

النماذج/الـ views التالية **يجب أن تبقى بلا فلترة فرع** لأنها بطبيعتها عابرة للفروع:

- `StockTransfer` (تحويل مخزون بين مخزنين، قد يكونا في فرعين مختلفين).
- `TreasuryTransfer` / تحويل بين خزينتين (`treasury_transfer_api`).
- `BankAccountTransfer` / تحويل بين حسابين بنكيين (`bank_account_transfer_api`).
- `treasury_bank_transfer_api` (تحويل بين خزينة وحساب بنكي).

تم التأكد أن الـ `get_object_or_404(Model.objects.for_tenant(tenant), pk=...)` المستخدمة في هذه
الـ views **لا** تستدعي `.for_branch()` — وهذا صحيح ومقصود.

---

## 6. التقارير (Reports)

كل `*ReportGenerator` (في `apps/{sales,purchases,stocks,treasury,bank_accounts,expenses}/reports.py`)
أصبح يقبل باراميتر اختياري `branch=None` في `__init__`، ويُخزَّن كـ `self.branch`، ويُستخدم لفلترة
الاستعلام الرئيسي للتقرير (القائمة/الملخص الأساسي) بنفس منطق `for_branch` / `filter_by_branch_via`.

في `views.py` لكل تطبيق، تم تمرير `branch=getattr(request, 'branch', None)` تلقائياً لكل استدعاء
لأي `*ReportGenerator`.

**ملاحظة صادقة (نطاق العمل):** التغطية طُبِّقت على الاستعلام الرئيسي لكل مولّد تقرير (الأكثر استخداماً:
`get_summary_report` / `get_balances_report`)، وليس على كل استعلام فرعي داخل كل دالة تقرير
(مثل: `get_by_item_report`, `get_profit_margin_report`, إلخ في `stocks`/`sales`/`purchases`). نقطة
التوسّع (`self.branch`) موجودة وجاهزة في كل مولّد؛ يمكن تعميم الفلترة على بقية دوال كل مولّد بنفس
الأسلوب متى احتاج الأمر (ابحث عن `self.tenant` داخل كل ملف `reports.py` وطبّق نفس نمط
`filter_by_branch_via` / `.for_branch(self.branch)` حسب نوع النموذج).

---

## 7. سجل النشاطات (Audit Log) — `accounts.UserActivity`

- أُضيف حقل `branch` (nullable) لنموذج `UserActivity`.
- `apps/accounts/activity_service.py::log_activity()` يختم كل نشاط بـ `branch=getattr(request, 'branch', None)`
  تلقائياً.
- صفحة سجل النشاطات (`apps/accounts/views.py::activity_log`) تفلتر حسب فرع المستخدم الحالي (فلترة
  يدوية لأن `UserActivity` ليس `TenantMixin`).

---

## 8. الترحيلات (Migrations) التي أُضيفت في هذا العمل

```
apps/accounts/migrations/0008_user_branch.py
apps/accounts/migrations/0009_useractivity_branch.py
apps/customers/migrations/0006_customer_branch.py
apps/suppliers/migrations/0005_supplier_branch.py
apps/employees/migrations/0006_employee_branch.py
apps/agents/migrations/0007_agent_branch.py
apps/treasury/migrations/0007_treasury_branch.py
apps/bank_accounts/migrations/0002_bankaccount_branch.py
apps/expenses/migrations/0005_expense_branch.py
```

كلها من نوع `AddField` بسيط، nullable، `on_delete=SET_NULL` — لا توجد أي migration هدّامة (لا حذف
بيانات، لا تغيير نوع حقل موجود).

---

## 9. النماذج التي استُبعدت عمداً

- `apps/store/models.py::StoreSettings` — سجل واحد لكل tenant (singleton)، لا معنى لربطه بفرع.
- `apps/insurance/*` — لم يُطلب صراحة، ولم يُلمَس في هذا العمل (لم يُذكر في طلب المستخدم).

---

## 10. خطوات تكرار نفس العمل في `enjazims`

1. تأكد من وجود نموذج `Branch` مشابه (tenant-scoped) في `core`، وحقل `branch` على المستخدم.
2. طبّق نفس تعديل `TenantMiddleware` لضبط `request.branch`.
3. أضف `for_branch()` لـ `TenantQuerySet`/`TenantManager` في `core/models.py` بنفس الكود بالضبط.
4. أضف `filter_by_branch_via()` في `core/utils.py` بنفس الكود بالضبط.
5. لكل تطبيق (customers, suppliers, employees, agents, treasury, bank_accounts, expenses، وأي
   تطبيق مشابه): طبّق **نمط القسم 3** أعلاه (حقل + migration + فلترة القوائم + ختم الفرع عند الإنشاء).
6. لكل تطبيق مبيعات/مشتريات يعتمد على مخزن (`stock`/`warehouse`): طبّق **نمط القسم 4** (فلترة عبر
   `stock__branch` بدلاً من تكرار الحقل).
7. لا تُقيّد أي عملية "تحويل" (transfer) بين وحدتين بالفرع — راجع **القسم 5**.
8. لكل `ReportGenerator`: أضف باراميتر `branch=None` في `__init__`، خزّنه كـ `self.branch`، وفلتر
   استعلام التقرير الرئيسي — راجع **القسم 6**.
9. لسجل النشاطات/Audit Log إن وُجد بنمط مشابه: طبّق **القسم 7**.
10. بعد كل مرحلة: شغّل `python manage.py check` و
    `python manage.py makemigrations --check --dry-run` للتأكد من عدم وجود أخطاء أو migrations
    مفقودة قبل الانتقال للمرحلة التالية.
11. اعمل commit منطقي لكل مرحلة (نفس أسلوب commits هذا الفرع)، ولا تعمل push إلا بعد اكتمال كل
    شيء والتأكد من نجاح كل الفحوصات.

---

## 11. ملخص الفحوصات النهائية التي أُجريت في `enjazpms`

- `python manage.py check` — نجح (تحذير واحد فقط غير متعلق بهذا العمل: `rest_framework.W001`).
- `python manage.py makemigrations --check --dry-run` — `No changes detected` (كل الحقول لها
  migrations مطابقة).
- تم تنفيذ الفحص بعد كل مرحلة رئيسية (Phase 0, 1, 2, 3, 4/5) وليس مرة واحدة فقط في النهاية.

## 12. الاختبار الفعلي مقابل قاعدة بيانات حقيقية (تم لاحقاً)

بعد كتابة هذا الملف بالكامل، تم تطبيق الترحيلات (`migrate`) على قاعدة بيانات التطوير المحلية
الحقيقية (MySQL — نفس بيئة الجهاز المحلي، وليست الإنتاج)، ورُفّع مشترك تجريبي موجود مسبقاً
("شركة فالوريا" — بيانات seed واقعية عبر `seed_valoria_week`) إلى باقة `enterprise` / `multi_branch`،
وأُنشئ فرعان فعليان، ووُزّعت كل السجلات القائمة (مخازن، عملاء، موردون، موظفون، وكلاء، خزائن،
حسابات بنكية، مصروفات) بينهما، وأُنشئ مستخدمان تجريبيان بصلاحيات كاملة، كل واحد مربوط بفرع مختلف:

- `cashier1` / `Test@1234` → فرع "الفرع الرئيسي - الخرطوم" (MAIN)
- `accountant1` / `Test@1234` → فرع "فرع بحري" (BAHRI)

**الاختبار تم عبر Django test client (`force_login` + طلبات فعلية لكل صفحة/API)، وليس فقط قراءة كود:**

### ما تم التأكد منه يعمل بشكل صحيح (بدون تعديل):
- كل صفحات القوائم (مبيعات، مرتجعات مبيعات، عروض أسعار، مشتريات، مرتجعات مشتريات، مخزون، عملاء،
  موردون، موظفون، وكلاء، خزائن، حسابات بنكية، مصروفات، سجل النشاطات) ترجع 200 لكلا المستخدمين.
- API الجداول (`table-api`) لـ: المخزون، العملاء، الموردين، الموظفين، الوكلاء، الخزائن، الحسابات
  البنكية، المصروفات — كل واحد يُرجع فقط سجلات فرع المستخدم (تحقّق فعلي بمقارنة الـ IDs، لا تداخل
  بين الفرعين).
- الإنشاء الفعلي (POST) لعميل جديد كمستخدم فرع BAHRI → السجل الناتج حصل تلقائياً على
  `branch_id` الصحيح (بدون أي اختيار يدوي من الواجهة) — تأكيد أن auto-stamping يعمل end-to-end.
- تقرير المبيعات الرئيسي (`sales_summary`) يختلف فعلياً بين المستخدمين (طول المحتوى مختلف) — تأكيد
  أن فلترة `self.branch` تعمل. تقارير `by-customer`/`by-item` (الاستعلامات الفرعية غير المفلترة —
  القيد الموثّق في القسم 6) لم تتأثر بالفرع كما هو متوقع ومُوثّق، وهذا سلوك مقصود وليس خطأ.
- سجل النشاطات: السجلات القديمة (`branch=NULL`) تظهر للجميع (السلوك الشامل المقصود)، والسجل الجديد
  المرتبط بفرع يظهر فقط لصاحب نفس الفرع.
- عمليات `manage.py check` و`makemigrations --check --dry-run` نظيفة بعد كل الاختبارات (لا أخطاء
  جديدة، لا migrations ناقصة).

### خطأ حقيقي تم اكتشافه وإصلاحه أثناء الاختبار:
`SaleReturn` و`PurchaseReturn` **لا يملكان حقل `stock` مباشرة** — بل يصلان للمخزن عبر
`original_invoice.stock`. كانت `filter_by_branch_via(...)` تُستدعى بالقيمة الافتراضية
(`field='stock__branch'`) في صفحات المرتجعات (`return_list`, `return_table_api` في كل من
`apps/sales/views.py` و`apps/purchases/views.py`)، فكانت تُسبب `FieldError: Cannot resolve keyword
'stock' into field` عند أي محاولة فتح صفحة المرتجعات لمستخدم مرتبط بفرع. تم الإصلاح بتمرير
`field='original_invoice__stock__branch'` صراحة في الاستدعاءات الأربعة. **درس لتطبيق `enjazims`:**
تحقّق دائماً من الحقل الفعلي الذي يربط الموديل بالمخزن قبل استخدام القيمة الافتراضية لـ
`filter_by_branch_via`، خصوصاً في نماذج المرتجعات (Returns) التي عادة ما تصل للمخزن عبر الفاتورة
الأصلية وليس حقل مباشر.

### ما لم يُختبر بعد (يحتاج اختبار المستخدم الفعلي صباحاً):
- الواجهة الأمامية (JS/Templates) بصرياً في متصفح حقيقي — الاختبار تم على مستوى HTTP response
  فقط (status code + محتوى JSON/HTML)، وليس تفاعل المستخدم الفعلي مع الفورمات.
- التحويلات الفعلية بين الفروع (إنشاء `StockTransfer`/تحويل خزينة فعلي عبر الواجهة).
- سلوك مستخدم بدون فرع (`branch=None`) على تينانت من باقة `basic`/`pro` — لم يُعَد اختباره صراحة في
  هذه الجولة، لكنه نفس المسار البرمجي الذي كان يعمل قبل هذا العمل بالكامل (`for_branch(None)` no-op).

---

## 13. مراجعة الفروع — الجولة الثانية (مشرف الفرع + إصلاحات)

هذا القسم يوثّق عمل لاحق (بعد كل ما سبق) استجابةً لملاحظات مراجعة فعلية على نظام الفروع. **ثلاث
مشاكل حقيقية** تم اكتشافها وإصلاحها، بالإضافة إلى **ميزة جديدة** (مشرف الفرع). طبّق كل بند من هذا
القسم بنفس الترتيب والمنطق في `enjazims`.

### 13.1 المشكلة: مخازن مرقمة تُنشأ تلقائياً عند الاشتراك (خطأ حقيقي)

**قبل الإصلاح:** `apps/core/signals.py::create_tenant_defaults` (يعمل على `post_save` لـ `Tenant`
عند `created=True`) كان يُنشئ تلقائياً `max_stocks` مخزناً مرقّماً (`مخزن 2`, `مخزن 3`, ... حتى
`max_stocks`) لأي مشترك جديد. يعني مشترك باقة `pro` (max_stocks=5) يحصل فوراً على 5 مخازن فارغة،
ومشترك `enterprise` (max_stocks=20) يحصل على 20 مخزناً فارغاً — كلها بدون أن يطلبها المشترك.

**لماذا هذا خطأ:** `max_stocks` هو **سقف أقصى** (حد الباقة)، وليس عدداً يجب إنشاؤه فعلياً. القرار
الصحيح: المشترك يضيف مخازنه بنفسه من واجهة "المخازن" حسب حاجته الفعلية، والنظام يمنعه فقط إذا حاول
تجاوز `max_stocks` (هذا المنع **كان موجوداً بالفعل ولم يتغيّر** — راجع
`apps/stocks/models.py::Stock.can_add_stock` و`apps/stocks/views.py::stock_create_api`).

**الإصلاح:** حذف الحلقة (`if instance.max_stocks > 1: for i in range(2, instance.max_stocks + 1): ...`)
من `create_tenant_defaults`، وإبقاء إنشاء مخزن افتراضي واحد فقط ("المخزن الرئيسي" / `WH-MAIN`
/ `is_system_default=True`) — تماماً كما كان يحدث لباقة `basic` أصلاً. نفس المبدأ ينطبق على أي حد
مشابه (`max_branches`, `max_users`, ...) — **لا تُنشئ سجلات تلقائياً لمجرد وجود حد أقصى مسموح به**؛
الحد يُستخدم فقط للمنع عند الإضافة اليدوية. (تأكدنا أن `max_branches` لا يعاني من نفس المشكلة —
لا يوجد كود مشابه يُنشئ فروعاً تلقائياً.)

**أثر جانبي على الاختبارات:** كان اختبار `apps/core/tests_full.py::ProfessionalEditionTests` يعتمد
على الاعتقاد الخاطئ بأن الاشتراك في باقة `pro` (max_stocks=5) يُنشئ تلقائياً 5 مخازن (كان يأخذ أول
مخزنين من `Stock.objects.filter(...)` مباشرة بعد إنشاء الـ tenant، ويؤكّد أن العدد = `max_stocks`
مباشرة). تم تعديل الاختبار ليُنشئ المخزن الثاني يدوياً في `setUp`، وليؤكّد أن العدد الفعلي بعد
الإنشاء هو **1** فقط (وليس `max_stocks`)، ثم يضيف مخازن حتى الحد ليتأكد من عمل `can_add_stock`
بشكل صحيح عند الوصول للحد.

### 13.2 المشكلة: إنشاء مخزن (warehouse) لسه بيطلب اختيار الفرع يدوياً

نمط "الختم التلقائي للفرع عند الإنشاء" (§3 نقطة 4 أعلاه) كان مطبّقاً على Customer/Supplier/
Employee/Agent/Treasury/BankAccount/Expense، لكن **لم يكن مطبّقاً على `Stock` نفسه** — رغم أن
`Stock.branch` هو أصل فكرة "الفرع يُشتق من المخزن" في القسم 4. مستخدم مربوط بفرع (`request.branch`
غير فارغ) كان لازال يشوف حقل "الفرع" في فورم إضافة/تعديل مخزن، ولازم يختاره يدوياً — عكس المطلوب
("كل العمليات بتكون مرتبطة تلقائياً بالفرع، ما يحتاجش يختار الفرع في أي إجراء").

**الإصلاح** (`apps/stocks/forms.py::StockForm` + `apps/stocks/views.py`):
- `StockForm.__init__` يقبل الآن باراميتر `branch=None`. إذا مُرِّر (أي المستخدم مربوط بفرع)، يُحذف
  حقل `branch` من الفورم كلياً (`del self.fields['branch']`) — فلا يظهر في الواجهة إطلاقاً.
- `stock_create_api` / `stock_update_api` / `stock_list` (السياق الذي يبني الفورم الأولي) يمرّرون
  الآن `branch=getattr(request, 'branch', None)` لـ `StockForm`.
- `stock_create_api`: بعد نجاح الفورم، إذا `branch` غير فارغ، يُختم `stock.branch = branch` صراحة
  (تجاوزاً لأي قيمة — أصلاً الحقل محذوف من الفورم فلا توجد قيمة قادمة من المستخدم).
- المستخدم المركزي/الأدمن (`request.branch is None`) يحتفظ بحقل اختيار الفرع كما كان (يحتاج فعلاً
  يختار لأي فرع يتبع المخزن الجديد، لأنه غير مربوط بفرع واحد).
- Template (`apps/stocks/templates/stocks/stock_list.html`): شرط عرض حقل الفرع أصبح
  `{% if current_tenant.version_type == 'multi_branch' and form.branch %}` بدل الاعتماد على
  `version_type` وحده (لأن `form.branch` قد يكون محذوفاً حتى لو النسخة `multi_branch`).

**نفس الفحص يلزم تكراره في `enjazims`:** ابحث عن أي فورم/view فيه حقل `branch` ظاهر للمستخدم
النهائي غير المركزي، وطبّق نفس نمط "حذف الحقل + ختم تلقائي من `request.branch`".

### 13.3 ميزة جديدة: مشرف الفرع (`is_branch_supervisor`)

**المطلوب:** كل فرع لازم يكون عنده "مشرف" — مستخدم له **كل صلاحيات الفرع** (كل العمليات التشغيلية:
مبيعات، مشتريات، عملاء، موردين، مخزون، خزائن، حسابات بنكية، موظفين، مناديب، تقارير، ...) **بدون**
أي صلاحية من صلاحيات "مدير النشاط" (مالك الاشتراك) الحصرية — تحديداً: إعدادات المنشأة (بما فيها سعر
الصرف)، إدارة المستخدمين، إدارة مجموعات الصلاحيات، وإدارة الفروع نفسها. (المتجر الإلكتروني أيضاً
اعتُبر مورداً واحداً على مستوى المنشأة كلها — نفس منطق الاستبعاد في §9 — فاستُبعد كذلك.)

**لماذا ميزة منفصلة عن `PermissionGroup` العادية:** النظام أصلاً فيه `PermissionGroup` مرن (كل
صلاحية تُمنح يدوياً)، ونظرياً يقدر مدير النشاط يبني مجموعة صلاحيات فيها كل شيء ما عدا هذه الفئات
الخمس. لكن هذا: (1) عملية يدوية عرضة للخطأ (ينسى يستثني صلاحية)، (2) لا تُحدَّث تلقائياً لو أُضيفت
صلاحية جديدة للنظام مستقبلاً (لازم تُضاف يدوياً لكل مجموعة)، (3) لا تُعبّر عن *نية* واضحة ("هذا
الشخص هو مسؤول الفرع") تُستخدم أيضاً لعرض "من هو مشرف كل فرع" في شاشة الفروع. لذلك اختير نمط علم
Boolean (`is_branch_supervisor`) بنفس فلسفة `is_tenant_admin` الموجود أصلاً (علم "bypass" وليس
مجموعة صلاحيات)، بدل حقل FK مخصص على `Branch`.

**التنفيذ (`apps/accounts/`):**

1. **`models.py`** — حقل جديد على `User`:
   ```python
   is_branch_supervisor = models.BooleanField('مشرف الفرع', default=False)
   ```
   (migration: `0010_user_is_branch_supervisor.py` — `AddField` بسيط، `default=False`، غير هدّام)

2. **`permissions.py`** — دالة جديدة `get_branch_supervisor_permission_keys()`:
   ```python
   BRANCH_SUPERVISOR_EXCLUDED_CATEGORIES = {
       'إعدادات النشاط التجاري',   # فيها أيضاً سعر الصرف (exchange_rate)
       'المستخدمين',
       'المجموعات والصلاحيات',
       'الفروع',
       'المتجر الإلكتروني',        # سجل واحد لكل tenant — راجع §9
   }

   def get_branch_supervisor_permission_keys():
       schema = load_permission_schema()
       keys = []
       for section, perms in schema.items():
           if section in BRANCH_SUPERVISOR_EXCLUDED_CATEGORIES:
               continue
           keys.extend(perms.keys())
       return keys
   ```
   القائمة المستبعدة تُطابق أسماء الفئات (keys) في `permissions_schema.json` تماماً — أي تغيير في
   أسماء الفئات هناك لازم ينعكس هنا.

3. **`models.py::User.get_permission_keys()` و `has_perm_key()`** — أضيف مسار جديد **بين**
   `is_tenant_admin` (bypass كامل) و`permission_groups` (المسار العادي):
   ```python
   def get_permission_keys(self):
       if self.is_superuser or self.is_tenant_admin:
           return set(get_permission_keys())
       if self.is_branch_supervisor and self.branch_id:
           return set(get_branch_supervisor_permission_keys())
       # ... permission_groups كما كان

   def has_perm_key(self, permission_key):
       if not permission_key:
           return False
       if self.is_superuser or self.is_tenant_admin:
           return True
       if self.is_branch_supervisor and self.branch_id:
           return permission_key in get_branch_supervisor_permission_keys()
       # ... permission_groups كما كان
   ```
   **مهم:** `is_branch_supervisor` بدون `branch_id` (فرع محدد) **لا يُفعّل شيئاً** — نفس فلسفة
   `for_branch(None)` no-op. هذا يمنع أن يتحوّل مستخدم مركزي بالغلط إلى "مشرف فرع" بلا فرع فيصبح بلا
   صلاحيات إطلاقاً بدل العكس.
   - هذا يكفي لتأمين **الـ views كلها تلقائياً** (كل view محمي بـ `@require_permission('key')` أو
     بـ `{% if user|has_perm_key:'key' %}` في القوالب) — لا حاجة لتعديل كل view على حدة.

4. **التحقق (Validation) — `apps/accounts/forms.py::UserManagementForm.clean()`:**
   - `is_branch_supervisor=True` بدون فرع مُختار → خطأ ("يجب اختيار الفرع أولاً").
   - فرع واحد لا يقبل أكثر من مشرف واحد نشط في نفس الوقت — لو فيه مشرف حالي للفرع (غير هذا
     المستخدم نفسه لو تعديل)، يُرفض الحفظ برسالة توضح اسم المشرف الحالي، ويطلب إلغاء إشرافه أولاً.
     **قرار مقصود:** لا "استبدال صامت" للمشرف القديم — قرار صريح من مدير النشاط دائماً.
   - **لم يُطبَّق:** لا يوجد قيد "كل فرع *لازم* يكون عنده مشرف" على مستوى قاعدة البيانات/الحفظ (لا
     يوجد شيء يمنع حفظ فرع بدون مشرف) — لأن الفرع يُنشأ قبل تعيين أي مستخدم له غالباً (تسلسل زمني
     طبيعي: فرع أولاً، ثم مستخدمين). بدل القيد الصارم، أُضيف مؤشر عرض بصري (بند 6 أدناه) في شاشة
     الفروع يوضح "بدون مشرف" — تنبيه وليس منع.

5. **الفورم (`UserManagementForm`)** — أُضيف `is_branch_supervisor` إلى `Meta.fields`
   والـ `widgets` (checkbox)، بنفس نمط `is_tenant_admin`.

6. **الواجهة:**
   - `apps/accounts/templates/accounts/user_list.html`: toggle-card جديد "مشرف الفرع" بجانب "مدير
     النشاط" (يظهر فقط لو فيه فروع متاحة للـ tenant، نفس شرط ظهور حقل `branch`).
   - `static/js/users.js`: `openUserEdit()` يملأ حالة الـ checkbox + حقل الفرع من الـ API؛ يُخفى
     (مثل `is_tenant_admin`) لو المستخدم مرتبط بمندوب (`is_agent_user`).
   - `apps/accounts/views.py::user_detail_api`: أُضيف `branch` و`is_branch_supervisor` للاستجابة
     (كانا ناقصين — `branch` لم يكن يُرجَع أصلاً حتى قبل هذا العمل).
   - **شاشة الفروع** (`apps/core/views.py::branch_table_api` + `apps/core/templates/core/branch_list.html`):
     عمود جديد "المشرف" — يُحسب بـ query واحد
     (`User.objects.filter(tenant=tenant, is_branch_supervisor=True, branch__in=qs)`) ويُعرض اسم
     المشرف، أو "بدون مشرف" (بلون تحذيري) لو الفرع بلا مشرف — هذا هو التذكير العملي بدل قيد صارم.

### 13.4 ثغرة موجودة مسبقاً: القائمة الجانبية ما كانتش بتُخفي روابط مدير النشاط عن أي مستخدم عادي

أثناء تنفيذ 13.3 اكتشفنا أن `apps/core/templates/components/sidebar.html` — تحديداً قسم "الإعداد
الأولي" (إعدادات المنشأة، المتجر، المستخدمين والصلاحيات، تصنيفات المنتجات، إدارة المخازن/الفروع) —
كان **يظهر لأي مستخدم تينانت عادي بدون أي فحص صلاحية** (`{% if user.is_superuser or
user.is_platform_staff %} ... {% else %} ... {% endif %}` — القسم كله داخل الـ `{% else %}` بلا
فحص إضافي)، رغم أن الـ views خلف هذه الروابط **كانت** محمية فعلياً بـ `@require_permission` الصحيح.
يعني: أي موظف عادي (مو مشرف فرع، مو مدير نشاط) كان يشوف هذه الروابط في القائمة، ولو ضغط عليها
يُرفض على مستوى الـ view — تجربة مستخدم مربكة، ويكشف عن ميزات لا يفترض يعرف بوجودها أصلاً.

**الإصلاح:** أُضيفت فحوصات `{% if user|has_perm_key:'...' %}` حول كل رابط في هذا القسم لتطابق
بالضبط صلاحية الـ view خلفه:
- `إعدادات المنشأة` ← `view_tenant_settings`
- `إعدادات المتجر` ← `plan_allows_store` **و** `view_store_settings` (كان `plan_allows_store` فقط)
- `إدارة المجموعات` ← `view_permissiongroups`
- `إدارة المستخدمين` ← `view_users`
- `تصنيفات المنتجات` ← `view_categories`
- قسم "إدارة المخازن" (بالكامل) ← `view_stocks`
- `الفروع` (داخل قسم المخازن) ← `view_branches` **و** `version_type == 'multi_branch'` (كان
  `version_type` فقط)
- `سجل النشاط` — **بلا تغيير** (تُرك بلا فحص إضافي، لأن الـ view خلفه `activity_log`
  ليس عليه `@require_permission` أصلاً — أي مستخدم مسجّل دخول يشوفه، وهذا سلوك مقصود موثّق في §7).

**نفس الفحص يلزم تكراره في `enjazims`:** افتح `sidebar.html` (أو مكافئه) وتأكد أن كل رابط يطابقه
فحص `has_perm_key` بنفس مفتاح الصلاحية المستخدم في الـ view خلفه — أي رابط بلا فحص هو ثغرة عرض
(Information Disclosure خفيفة، مو ثغرة وصول فعلي لأن الـ view يبقى محمي، لكن تجربة مستخدم سيئة
وتكشف بنية النظام لمن لا يفترض يعرفها).

### 13.5 ملخص الملفات المتأثرة في هذه الجولة

```
apps/core/signals.py                                   — حذف auto-provisioning للمخازن
apps/core/tests_full.py                                 — تصحيح اختبار كان يعتمد على السلوك الخاطئ
apps/stocks/forms.py                                     — StockForm(branch=...) + حذف الحقل
apps/stocks/views.py                                      — تمرير request.branch لـ StockForm × 3
apps/stocks/templates/stocks/stock_list.html             — شرط عرض حقل الفرع
apps/accounts/models.py                                  — is_branch_supervisor + منطق الصلاحيات
apps/accounts/migrations/0010_user_is_branch_supervisor.py
apps/accounts/permissions.py                              — get_branch_supervisor_permission_keys()
apps/accounts/forms.py                                    — حقل + validation في UserManagementForm
apps/accounts/views.py                                    — branch + is_branch_supervisor في user_detail_api
apps/accounts/templates/accounts/user_list.html           — toggle-card "مشرف الفرع"
static/js/users.js                                        — ربط الحقل الجديد بالـ JS
apps/core/views.py                                        — عمود "المشرف" في branch_table_api
apps/core/templates/core/branch_list.html                 — عمود "المشرف" في الجدول والبطاقات
apps/core/templates/components/sidebar.html               — فحوصات has_perm_key الناقصة
```

### 13.6 ما لم يُنفَّذ عمداً (نطاق العمل)

- لا قيد صارم يمنع حفظ/إنشاء فرع بدون مشرف (راجع التبرير في 13.3 بند 4) — تنبيه بصري فقط.
- لا حد أقصى على عدد "مشرفي الفرع" الكلي (بخلاف: فرع واحد ↔ مشرف واحد كحد أقصى).
- لم تُراجَع صلاحيات "الموظفين/رواتب الموظفين/سلف الموظفين/حوافز الموظفين" تحديداً ضمن نقاش هذه
  الجولة — هي مشمولة تلقائياً في `get_branch_supervisor_permission_keys()` (غير مستبعدة) لأنها
  عمليات تشغيلية داخل الفرع، لا صلاحيات "مدير نشاط" حصرية.
- `apps/accounts/views.py::activity_log` بقي بلا `@require_permission` (سلوك موثّق سابقاً في §7،
  لم يُطلب تغييره).

### 13.7 إزالة خيار "مدير النشاط" من شاشة إدارة المستخدمين (كل نشاط له مدير واحد فقط)

طلب لاحق: شاشة إدارة المستخدمين التي يستخدمها مدير النشاط نفسه (`accounts:user_list` —
`UserManagementForm`) كانت تعرض checkbox "مدير النشاط" (`is_tenant_admin`)، مما يسمح لمدير النشاط
بترقية أي مستخدم آخر ليصبح مديراً أيضاً. **القرار:** كل نشاط تجاري له مدير واحد فقط (يُعيَّن تلقائياً
عند التسجيل — `apps/accounts/views.py` حول `is_tenant_admin=True` في تدفّق التسجيل)، فلا يجوز تعيين
مدير إضافي من هذه الشاشة إطلاقاً.

**التنفيذ:** `is_tenant_admin` حُذف بالكامل من `UserManagementForm.Meta.fields` (وليس فقط إخفاء
الـ checkbox في الواجهة) — هذا مهم أمنياً: لو تُرك الحقل في `Meta.fields` مع إخفاء الـ widget فقط في
القالب، كان يبقى ممكناً تفعيله عبر POST مباشر لـ `user_create_api`/`user_update_api` (كلاهما يستخدم
نفس الفورم). بحذف الحقل من `Meta.fields`، `form.save()` لا يلمس `is_tenant_admin` إطلاقاً — قيمته
تبقى كما هي (لن تُصفَّر عن طريق الخطأ لمدير النشاط الحالي عند تعديل بياناته، ولن يقدر أي أحد آخر
يُفعّلها).
- القالب (`user_list.html`): حُذف toggle-card "مدير النشاط" بالكامل من فورم الإضافة/التعديل.
- **لم يُحذف:** عرض حالة "مدير النظام" (نعم/لا) في مودال "عرض المستخدم" (`viewUserAdmin`) — بقي كما
  هو، لأنه عرض للمعلومة فقط (read-only) وليس جزءاً من "الفورم" القابل للتعديل الذي طُلب حذف الخيار
  منه.
- `static/js/users.js`: حُذفت كل الأسطر التي تتعامل مع `#id_is_tenant_admin` (لم يعد للعنصر وجود في
  الـ DOM) من `openUserEdit()` ومن منطق إخفاء/إظهار الحقول لمستخدم مرتبط بمندوب. نص تنبيه "مستخدم
  مرتبط بمندوب" عُدِّل ليحذف الإشارة لـ "تعيينه مديراً" (لم يعد ذلك ممكناً أصلاً من هذه الشاشة لأي
  مستخدم).
- **لم يتأثر:** شاشة إدارة المستخدمين الخاصة بموظفي المنصة (platform staff/superuser —
  `apps/core/views.py` حول `has_platform_perm('manage_users')`) — تلك شاشة منفصلة تماماً (فورم/API
  مختلف)، ويصح بقاء قدرتها على تعيين `is_tenant_admin` عند إنشاء أول مستخدم لمشترك جديد يدوياً؛
  الطلب كان عن "شاشة مدير النشاط" تحديداً وليس شاشة موظفي المنصة.

نفس التعديل طُبِّق بالضبط في `enjazims` (نفس الملفات والمنطق).

---

## 14. العملاء/الموردون/المناديب فرعيون حصرياً، والمنتج مورد مركزي للعرض فقط

طلب لاحق ذو مبدأين منفصلين:

### 14.1 العملاء والموردون والمناديب: يجب أن يتبعوا فرعاً محدداً دائماً (لا سجلات "مركزية")

**المشكلة:** رغم أن `Customer`/`Supplier`/`Agent` مطبّق عليها فعلاً Phase 2 (ختم تلقائي لفرع المستخدم
عند الإنشاء، فلترة القوائم بـ `.for_branch()`) — كان هذا يعمل فقط للمستخدم **المربوط بفرع**. أما
المستخدم **المركزي** (مدير النشاط، `request.branch is None`) فكان عندما ينشئ عميلاً/مورداً/مندوباً
يحصل تلقائياً على `branch=None` بلا أي اختيار (لا يوجد حقل `branch` في الفورم أصلاً) — يعني يبقى
سجلاً "مركزياً" ظاهراً لكل الفروع للأبد (بحكم قاعدة `for_branch()`: `Q(branch=branch) |
Q(branch__isnull=True)`). هذا يخالف المطلوب: "العملاء والموردون والمناديب لازم يكونوا تبع كل فرع
مش مركزيين".

**القرار:** لا نغيّر قاعدة `for_branch()` نفسها (تبقى `branch=NULL` ظاهرة للجميع — ضرورية لتوافق
البيانات القديمة، راجع المبدأ الأساسي في القسم 1). بدلاً من ذلك **نمنع إنشاء سجلات `branch=NULL`
جديدة من الآن فصاعداً** لهذه الثلاثة تحديداً، عبر إلزام المستخدم المركزي باختيار فرع صراحةً عند
الإنشاء (على عكس المستخدم المربوط بفرع الذي يبقى الختم عنده تلقائياً بلا اختيار كما كان).

**التنفيذ:**

1. **دالة مساعدة جديدة** `apps/core/utils.py::setup_branch_field(form, tenant, branch, field_name='branch')`:
   - مستخدم مربوط بفرع (`branch` غير فارغ) → يُحذف حقل `branch` من الفورم كلياً (يُختم تلقائياً في
     الـ view، بدون اختيار — نفس نمط `StockForm` في §13.2).
   - مستخدم مركزي (`branch=None`) على tenant من نسخة `multi_branch` → الحقل يبقى لكن `required=True`
     + `queryset` مقصور على فروع الـ tenant النشطة + `empty_label='اختر الفرع'` (لا اختيار افتراضي
     ضمني).
   - غير ذلك (نسخة `single_store`/`multi_stock` بلا فروع أصلاً) → يُحذف الحقل (لا معنى له).
   - هذه الدالة **عامة** — تصلح لأي فورم فيه حقل `branch` مباشر يحتاج نفس المنطق؛ استُخدمت هنا لثلاثة
     فورمات، ويمكن إعادة استخدامها لاحقاً لأي نموذج جديد بنفس الاحتياج.

2. **لكل من `CustomerForm` / `SupplierForm` / `AgentForm`** (`apps/{customers,suppliers,agents}/forms.py`):
   - أُضيف `'branch'` إلى `Meta.fields` + widget `Select`.
   - أُضيف `__init__(self, *args, tenant=None, branch=None, **kwargs)` يستدعي `setup_branch_field(self, tenant, branch)`.
   - **لم يتغيّر** أي شيء في الموديل نفسه (`branch` كان `null=True, blank=True` أصلاً منذ Phase 2) —
     لا migration جديدة، القيد على مستوى الفورم/الـ view فقط، وليس قيداً صارماً على DB.

3. **لكل view من الثلاثة** (`{list,create_api,update_api}` في `apps/{customers,suppliers,agents}/views.py`):
   - `list`: يمرّر `tenant=tenant, branch=getattr(request, 'branch', None)` للفورم الأولي.
   - `create_api`: نفس التمرير، وبعد `form.save(commit=False)` — **لم يعد** يختم `obj.branch =
     getattr(request, 'branch', None)` دون شرط (كان هذا يطبّق فوق أي قيمة اختارها المستخدم المركزي
     من الفورم!) — الآن يُطبَّق **فقط إذا `branch is not None`** (أي مستخدم مربوط بفرع)؛ للمستخدم
     المركزي، `form.save(commit=False)` نفسه يكون قد ضبط `obj.branch` من القيمة الإلزامية المُختارة.
   - `update_api`: نفس تمرير `tenant`/`branch` للفورم (يسمح لمدير النشاط المركزي بإعادة تعيين فرع
     سجل موجود عند التعديل؛ المستخدم المربوط بفرع لا يقدر تغييره أصلاً لأن الحقل محذوف من فورمه).
   - `detail_api` لكل من الثلاثة: أُضيف `'branch': obj.branch_id` للاستجابة (لتعبئة قيمة الفرع الحالية
     عند فتح فورم التعديل).
   - القوالب (`{customer,supplier,agent}_list.html`) و JS المضمّن فيها: أُضيف عمود/حقل `{% if
     form.branch %}...{% endif %}` بجانب "المدينة"، وسطر `if (d.branch) { $('#id_branch').val(d.branch); }`
     عند فتح فورم التعديل.

**ما لم يتغيّر عمداً:** السجلات القديمة (`branch=NULL`) التي أُنشئت قبل هذا العمل تبقى ظاهرة للجميع
كما هي — لا إعادة توزيع رجعي (retroactive) لها؛ هذا فرق **مستقبلي** فقط (كل سجل جديد من الآن
فصاعداً لازم فرع). لا قيد DB (`null=False`) لأن تينانتات `single_store`/`multi_stock` (وكل البيانات
التاريخية) تعتمد على `branch=NULL` كسلوك طبيعي.

### 14.2 المنتج/الصنف: مورد مركزي — الفرع يشوف فقط (تفاصيل + كشف حركة)، وكشف الحركة مقيّد بمخازن فرعه

**المبدأ:** خلافاً للعملاء/الموردين/المناديب (فرعيون حصرياً)، المنتج (`items.Item`) هو **كتالوج مركزي
واحد** يشترك فيه كل الفروع (نفس الاسم/السعر/الفئة...)، ولا يملك حقل `branch` أصلاً (ولم يُضَف له —
راجع الفلسفة في §4: الكمية فقط تختلف لكل فرع عبر `StockQuantity → Stock.branch`). القرار: **لا فرع
يقدر يضيف/يعدّل/يحذف منتجاً إطلاقاً** — فقط يشوف تفاصيله (`view_items`)، وكشف حركته (الكميات
والحركات) يكون مقصوراً على مخازن فرعه هو فقط.

**التنفيذ — قيد مطلق (Hard Rule) وليس افتراضاً قابلاً للتعديل:**

هذا يختلف جوهرياً عن `BRANCH_SUPERVISOR_EXCLUDED_CATEGORIES` (§13.3) التي تتحكم فقط في المفاتيح
التي يحصل عليها "مشرف الفرع" **تلقائياً** — مدير النشاط يقدر نظرياً يبني `PermissionGroup` يدوياً
فيها `add_items`/`change_items`/`delete_items` ويسندها لموظف فرع عادي (غير مشرف)، فيتحايل بذلك على
القيد. لمنع هذا التحايل، أُضيف قيد **مطلق** في طبقة الصلاحيات نفسها:

1. **`apps/accounts/permissions.py`**: ثابت جديد
   ```python
   BRANCH_BLOCKED_KEYS = {'add_items', 'change_items', 'delete_items', 'import_items'}
   ```
   (`import_items` مضاف لأنه فعلياً "إضافة" منتجات بالجملة — نفس منطق `add_items`). **لم يُحظر**
   `export_items` (عملية قراءة/تصدير فقط، لا تُعدّل الكتالوج) ولا صلاحيات "التصنيفات" (لم يُطلب
   صراحة — راجع "ما لم يُنفَّذ" أدناه).

2. **`apps/accounts/models.py::User.has_perm_key()`**: فحص جديد **قبل** فحص `is_branch_supervisor`
   وقبل حلقة `permission_groups`:
   ```python
   if self.branch_id and permission_key in BRANCH_BLOCKED_KEYS:
       return False
   ```
   يعني: حتى لو مجموعة الصلاحيات المسندة لموظف الفرع تحتوي `add_items=True` صراحة، `has_perm_key('add_items')`
   يرجع `False` طالما عنده `branch_id`. **الاستثناء الوحيد**: `is_superuser`/`is_tenant_admin` (يُفحصان
   قبل هذا الشرط، فيتجاوزانه دائماً — القيد يخص "الفرع" لا "المنشأة").

3. **`User.get_permission_keys()`**: نفس المنطق — `keys -= BRANCH_BLOCKED_KEYS` إذا `self.branch_id`،
   بعد حساب `keys` من أي من المسارين (branch-supervisor أو permission_groups). هذا يضمن أن القائمة
   الجانبية وأي مكان آخر يعتمد `get_permission_keys()`/`has_perm_key()` (وليس فقط `@require_permission`
   على الـ views) يعكس القيد تلقائياً بلا أي تعديل إضافي في الـ templates.

4. **كشف حركة الصنف مقيّد بمخازن الفرع** (`apps/items/views.py::item_transactions_api` — شاشة "تفاصيل
   المنتج" تعرض هذا الكشف، وكان **غير مفلتر بالفرع إطلاقاً** قبل هذا العمل، خلافاً لكل تقارير
   `*ReportGenerator` الأخرى الموثّقة في §6):
   - استُورِدت `filter_by_branch_via` من `apps.core.utils`.
   - `current_qty` (إجمالي الكمية المعروض) و`movements` (آخر 200 حركة) — كلاهما يُفلتَران الآن بـ
     `filter_by_branch_via(qs, branch, field='stock__branch')` قبل الـ aggregate/slice.
   - **لم يتأثر** `item_detail_api` (معلومات الكتالوج الأساسية — اسم، سعر، تصنيف... — لا علاقة لها
     بمخزون/فرع، تبقى نفسها للجميع، هذا هو "عرض التفاصيل" المسموح به دائماً).
   - **لم يتأثر** (نطاق العمل — راجع "ما لم يُنفَّذ" أدناه) تقرير "حركة الأصناف" العام في وحدة
     المخازن (`StocksReportGenerator` — كان مستثنى أصلاً من فلترة الفرع الكاملة حسب §6، والطلب هنا
     كان عن شاشة "تفاصيل المنتج" في وحدة المنتجات تحديداً).

**ما لم يُنفَّذ عمداً (نطاق العمل — قرارات تحتاج تأكيد لاحق لو الفرضية غلط):**
- `export_items` **لم يُحظر** (اعتُبر قراءة/تصدير غير ضار، وليس "تعديلاً أو حذفاً").
- التصنيفات (`add_categories`/`change_categories`/`delete_categories`) **لم تُحظر** — الطلب كان عن
  "المنتج" تحديداً، لا التصنيفات.
- تقرير "حركة الأصناف" العام (`StocksReportGenerator.get_by_item_report` وما شابه في وحدة المخازن/
  التقارير) لم يُفلتر — القيد الموثّق في §6 (استعلامات فرعية غير الملخص الرئيسي) لا يزال قائماً؛ تم
  فقط فلترة `item_transactions_api` تحديداً (شاشة كشف حركة الصنف من داخل وحدة المنتجات).

### 14.3 ملخص الملفات المتأثرة

```
apps/core/utils.py                    — setup_branch_field() (جديدة، عامة)
apps/customers/forms.py               — حقل branch + __init__
apps/customers/views.py               — list/create_api/update_api/detail_api
apps/customers/templates/.../customer_list.html — حقل + JS
apps/suppliers/forms.py               — نفس نمط customers
apps/suppliers/views.py               — نفس نمط customers
apps/suppliers/templates/.../supplier_list.html — نفس نمط customers
apps/agents/forms.py                  — نفس نمط customers
apps/agents/views.py                  — نفس نمط customers
apps/agents/templates/.../agent_list.html — نفس نمط customers
apps/accounts/permissions.py          — BRANCH_BLOCKED_KEYS
apps/accounts/models.py               — القيد المطلق في has_perm_key/get_permission_keys
apps/items/views.py                   — فلترة الفرع في item_transactions_api
```

لا migrations جديدة في هذه الجولة (كل التعديلات على مستوى الفورم/الـ view/الصلاحيات، لا الموديل).

نفس التعديل طُبِّق بالضبط في `enjazims` (نفس الملفات والمنطق، بنفس أسماء الحقول والدوال).

---

## 15. لوحة التحكم، التحليلات المتقدمة، الإشعارات، وكل التقارير — تقييد كامل بالفرع

طلب لاحق: "كل إحصائيات الصفحة الرئيسية والإشعارات وكل التقارير يجب أن تكون لفرع المستخدم الحالي، مش
لكل المنشأة". هذا امتداد مباشر لـ §6 (التقارير) التي كانت مقتصرة على **الاستعلام الرئيسي فقط** لكل
`*ReportGenerator`؛ الآن تم تعميم الفلترة على كل استعلام فرعي في كل مولّد تقارير تقريباً، بالإضافة
لفحص لوحة التحكم الرئيسية والتحليلات المتقدمة والإشعارات من الصفر.

### 15.1 لوحة التحكم (`core:dashboard`)

**اكتُشِف أنها كانت مفلترة بالفعل بالكامل تقريباً** (عمل سابق خارج نطاق التوثيق الرسمي لهذا الملف) —
كل استعلام في `apps/core/views.py::dashboard()` (مبيعات اليوم/الشهر، فواتير معلّقة، نسبة السداد، أعلى
التصنيفات/المنتجات مبيعاً، مخزون منخفض، بيانات الرسم الأسبوعي، حالة المخزون) يستخدم بالفعل
`filter_by_branch_via(..., branch)` أو `.for_branch(branch)`. **الاستثناء الوحيد الذي تم إصلاحه:**
`stats['total_users']` كان يحسب كل مستخدمي الـ tenant بلا فلترة فرع — أُضيف `.for_branch(branch)`.
`stats['total_products']` **بقي بلا فلترة عمداً** — المنتج كتالوج مركزي (راجع §14.2)، فعدد المنتجات
الكلي في الكتالوج رقم واحد صحيح لكل المستخدمين بغض النظر عن فرعهم.

### 15.2 التحليلات المتقدمة (`core:analytics`) — ثغرة حقيقية، أُصلحت بالكامل

خلافاً للوحة التحكم، `apps/core/views.py::analytics()` **لم يكن فيه أي فلترة فرع إطلاقاً** (صفر
استخدام لـ `filter_by_branch_via`/`for_branch` في الدالة كاملة قبل هذا العمل). أُصلحت كل الاستعلامات:
- `sales_total()`/`purchase_total()` (دوال مساعدة داخلية تُستخدم لـ KPIs الشهر الحالي/السابق وترند
  12 شهر): `filter_by_branch_via(..., branch)`.
- `expense_total()`: `Expense.objects.for_tenant(tenant).for_branch(branch)` (حقل branch مباشر).
- أعلى 10 عملاء بالإيراد (`top_customers`، عبر `SaleInvoiceLine`): `field='invoice__stock__branch'`.
- أرصدة الخزائن (`treasuries`, `treasury_total`): `Treasury...for_branch(branch)`.
- مديونية العملاء (`customer_debt`، عبر `SaleInvoice`): `filter_by_branch_via`.
- مديونية الموردين (`supplier_debt`، تُحسب مورد-مورد): `SupplierModel.objects.for_tenant(tenant).for_branch(branch)`
  بدل `.filter(tenant=tenant)` — يقتصر الآن على موردي الفرع (الموردون فرعيون حصرياً من §14.1).

### 15.3 الإشعارات (`apps.notifications`) — ثغرتان حقيقيتان، أُصلحتا

1. **الموديل `Notification` لم يكن فيه حقل `branch` إطلاقاً.** أُضيف (`null=True, blank=True`, نفس نمط
   Phase 2 — migration `apps/notifications/migrations/0005_notification_branch.py` في enjazpms،
   `0002_notification_branch.py` في enjazims — إضافة بسيطة غير هدّامة).
2. **مولّدات الإشعارات التلقائية** (`apps/notifications/services.py`) كانت تُنشئ كل الإشعارات بـ
   `tenant=tenant` فقط بلا `branch` — يعني كل تنبيه (مخزون منخفض، فاتورة متأخرة، قرب انتهاء صلاحية،
   RFQ قارب على الانتهاء) كان يظهر لكل مستخدمي الـ tenant بغض النظر عن فرعهم. أُضيف `branch=` عند
   الإنشاء، مُشتقّاً من المصدر:
   - `generate_low_stock_notifications`: `sq.stock.branch`.
   - `generate_overdue_invoice_notifications`: `inv.stock.branch` (بحارس `if inv.stock_id else None`).
   - `generate_expiry_notifications` (موجودة في enjazpms فقط — لا يوجد تتبع صلاحية في enjazims):
     `batch.stock.branch` (+ أُضيف `'stock'` لـ `select_related` تجنّباً لـ N+1).
   - `generate_rfq_expiry_notifications`: `rfq.stock.branch` (+ `select_related('stock')`، بحارس
     `if rfq.stock_id else None`).
3. **`apps/notifications/views.py`**: `notification_list`, `notification_api` — أُضيف
   `.for_branch(branch)` على استعلامات العرض (القائمة الكاملة + العداد غير المقروء + آخر 8 للجرس).
   `mark_all_read_ajax` — أُضيف `.for_branch(branch)` أيضاً؛ **ثغرة سلوكية حقيقية كانت موجودة:** بدون
   هذا الإصلاح، ضغط مستخدم فرع على "تعليم الكل كمقروء" كان سيُعلّم إشعارات فروع أخرى كمقروءة دون أن
   يراها أصلاً (تأثير جانبي صامت على بيانات فرع آخر). **لم يتغيّر عمداً:** `notification_detail` و
   `mark_read_ajax` (تعديل سجل واحد بمعرفه) — بقيا بلا تقييد فرع، اتساقاً مع فلسفة §3 نقطة 5 (شاشات
   التفاصيل/الإجراء الفردي محكومة بالصلاحيات لا بقيود بيانات صارمة، خلاف العمليات الجماعية/القوائم).

### 15.4 التقارير — تعميم الفلترة على كل استعلام فرعي (وليس فقط الملخص الرئيسي)

**السياق المهم:** كل `*ReportGenerator` في `apps/{sales,purchases,stocks,treasury,bank_accounts,expenses}/reports.py`
يستقبل بالفعل `branch=` من الـ view (`views.py` في كل تطبيق يمرر `branch=getattr(request, 'branch',
None)` لكل استدعاء لأي Generator، لكل دالة تقرير — تم التحقق آلياً بعدّ كل استدعاء `XxxReportGenerator(`
في كل ملف `views.py` مقابل عدد الاستدعاءات التي فيها `branch=getattr...`؛ كانا متطابقين تماماً في
الحالتين enjazpms وenjazims، أي **35 استدعاء تقريباً من الـ views كانت جاهزة أصلاً**). المشكلة كانت
فقط أن أغلب دوال `get_*_report()` داخل كل Generator لا تستخدم `self.branch` الذي تستلمه أصلاً — يعني
**لم يكن هناك حاجة لأي تعديل في أي `views.py`**، فقط في `reports.py` نفسها.

**القاعدة العامة المتّبعة لكل دالة:**
- استعلام على نموذج له علاقة مباشرة أو غير مباشرة بـ `Stock` (فاتورة، سطر فاتورة، حركة مخزون، مرتجع):
  `filter_by_branch_via(qs, self.branch, field='...stock__branch')` — المسار يختلف حسب النموذج
  (`stock__branch` مباشرة، أو `invoice__stock__branch`، أو `original_invoice__stock__branch` لمرتجع
  مبيعات، أو `purchase_return__original_invoice__stock__branch` لسطر مرتجع مشتريات — نفس درس §12).
- استعلام على نموذج له حقل `branch` مباشر (عميل/مورد/مصروف/خزينة/حساب بنكي): `.for_branch(self.branch)`
  (أو `filter_by_branch_via(qs, self.branch, field='customer__branch')`/`supplier__branch`/
  `treasury__branch`/`bank_account__branch` لو الاستعلام على نموذج تابع مثل الدفتر/الحركة).
- **تقارير كشف حساب/رصيد لكيان واحد محدد بمعرّفه** (`get_customer_statement`, `get_supplier_statement`,
  `get_statement_report` للخزينة/الحساب البنكي) — **لم تُفلتَر عمداً**: هذه شاشات "افتح سجل عميل/مورد/
  خزينة/حساب واحد بعينه" وليست قوائم مجمّعة؛ الكيان نفسه أصلاً فرعي حصرياً (عميل/مورد/خزينة/حساب) أو
  الوصول له محكوم بصلاحية لا بفرع (نفس فلسفة §3 نقطة 5) — تقييده إضافياً هنا قرار وصول (access
  control) وليس فلترة استعلام، وخارج نطاق الطلب الحالي.
- **تقارير الأرصدة المُجمَّعة لكل الكيانات** (`get_customer_balances`, `get_supplier_balances`) —
  **هذه فُلترت** (خلافاً للنقطة السابقة) لأنها تعرض قائمة كل العملاء/الموردين دفعة واحدة، فتحتاج تقييد
  فعلي بالفرع.

**الدوال التي عُدِّلت (بالاسم، لكل تطبيق):**
- `sales/reports.py::SalesReportGenerator`: `get_by_customer_report`, `get_by_item_report`,
  `get_by_date_report`, `get_customer_balances`, `get_payments_report`, `get_returns_report`,
  `get_by_user_report`, `get_profit_margin_report`, `get_by_payment_method_report`.
- `sales/reports.py::IncomeStatementGenerator` (قائمة الدخل — تقرير منفصل تماماً، لم يكن يستقبل
  `branch` في `__init__` إطلاقاً): أُضيف باراميتر `branch=None` + فلترة `invoices`/`returns`/
  `expenses`/`cogs_qs`. **أُضيف تمرير `branch=getattr(request, 'branch', None)`** في نداءَي
  `IncomeStatementGenerator(...)` بـ `apps/sales/views.py` (`income_statement_report` و
  `income_statement_report_export`) — هذان الاثنان الاستثناء الوحيد اللي احتاج تعديل `views.py`
  فعلياً في كل هذه الجولة، تحديداً لأن الكلاس نفسه ما كانش بيقبل `branch` من الأساس.
- `purchases/reports.py::PurchasesReportGenerator`: `get_by_supplier_report`, `get_by_item_report`,
  `get_by_date_report`, `get_supplier_balances`, `get_payments_report`, `get_returns_report`,
  `get_by_user_report`, `get_price_history_report`.
- `stocks/reports.py::StocksReportGenerator`: `get_by_item_report`, `get_by_category_report`,
  `get_by_stock_report`, `get_item_movement_report` (+ استعلام `opening_qty` الفرعي بداخلها),
  `get_low_stock_report`, `get_controlled_substances_report` (enjazpms فقط), `get_valuation_report`,
  `get_non_moving_report` (+ استعلامَي `moving_item_ids`/`last_movement_qs` الفرعيين بداخلها).
- `treasury/reports.py::TreasuryReportGenerator`: `get_movements_summary` (+ قائمة `treasuries`
  المُستخدَمة كخيارات فلتر في نهاية الدالة).
- `bank_accounts/reports.py::BankAccountReportGenerator`: `get_movements_summary` (+ قائمة `accounts`
  المماثلة).
- `expenses/reports.py::ExpensesReportGenerator`: `get_by_category_report`, `get_by_date_report`,
  `get_details_report`.

**ما لم يُنفَّذ عمداً:**
- `insurance/reports.py::InsuranceReportGenerator` — لا يستقبل `branch` في `__init__` أصلاً، ولا يوجد
  حقل `branch` على نماذج التأمين (مستبعدة عمداً من نظام الفروع بالكامل منذ §9 الأصلي). تفعيلها يحتاج
  إضافة حقل `branch` لنماذج التأمين أولاً — خارج نطاق هذه الجولة.
- التصنيفات (`Category`) لا تملك حقل `branch` (مورد مركزي، راجع §14.2) — `get_by_category_report` في
  `stocks/reports.py` يفلتر الكميات لكل فئة بالفرع، لكن قائمة الفئات نفسها (`Category.objects.filter
  (tenant=...)`) تبقى بلا فلترة لأنها كتالوج مشترك، تماماً مثل `Item`.

### الفحوصات

`python manage.py check` و`makemigrations --check --dry-run` نظيفان في كلا المشروعين (نفس تحذير
`rest_framework.W001` القديم فقط، ومigration واحدة جديدة فقط: `notification_branch`). تشغيل
`apps.sales`, `apps.purchases`, `apps.stocks`, `apps.treasury`, `apps.bank_accounts`, `apps.expenses`,
`apps.notifications`, `apps.core.tests_full`, `apps.core.test_system_surfaces`,
`apps.core.test_browser_surfaces` (71 اختبار في enjazpms، فشل واحد فقط غير متعلق بهذا العمل —
`ModuleNotFoundError: playwright` بيئي بحت) — كلها نجحت. **نفس التعديلات بالضبط طُبِّقت في enjazims**
(تأكدنا فقط من غياب `get_expiry_notifications`/`get_controlled_substances_report` هناك، وهو فرق
مُوثَّق مسبقاً وليس سهواً).

## 16. إخفاء "الاشتراك" و"سعر الصرف" و"إعدادات النشاط" عن الفرع — ناف بار + حجب فعلي على مستوى الـ view

الطلب: هذه الثلاثة روابط (قائمة "Tenant Info" المنسدلة بالناف بار، وكذلك رابط "معلومات الاشتراك"
بالسايد بار) هي من صلاحيات مدير النشاط حصراً (راجع §13 الأصلي). إخفاؤها من الواجهة فقط غير كافٍ —
لازم "أي صفحة مخفية ما يكون ليها وصول" حتى برابط مباشر معروف، بغض النظر عن أي صلاحية ممنوحة صراحة
لمستخدم الفرع عبر مجموعات صلاحيات مخصّصة (نفس فلسفة `BRANCH_BLOCKED_KEYS` من §13 لكن هنا على مستوى
الـ view كامل بدل مفتاح صلاحية واحد، لأن `view_tenant_settings`/`change_tenant_settings` أنفسهم غير
مُدرَجين أصلاً في `BRANCH_BLOCKED_KEYS`، فقط مُستبعَدين من المنح التلقائي لمشرف الفرع).

### 16.1 الواجهة (Templates)

- `apps/core/templates/components/navbar.html`: قائمة "Tenant Info" المنسدلة (اسم النشاط + أيقونة
  متجر) كانت تظهر لأي مستخدم طالما `current_tenant` موجود. صارت الآن مشروطة بالكامل بـ
  `{% if current_tenant and not request.branch %}` — أي مستخدم فرع (`request.branch` موجود، يُضبط في
  `TenantMiddleware`) لا يرى القائمة إطلاقاً (لا "إعدادات النشاط"، ولا "سعر الصرف"، ولا "الاشتراك" —
  الثلاثة كانت روابطها الوحيدة داخل هذه القائمة). سابقاً كان كل عنصر مُغلَّف بشرط `not request.branch`
  منفرد، فكانت القائمة تظهر فارغة (بلا أي `<li>`) لمستخدم الفرع — تم تبسيطها لإخفاء القائمة كلها بدل
  ذلك.
- `apps/core/templates/components/sidebar.html`: رابط "معلومات الاشتراك" (قسم "Subscription" المستقل)
  كان أُخفي مسبقاً بـ `{% if not request.branch %}` (جولة سابقة لم تُوثَّق وقتها — تُوثَّق الآن هنا).
  رابط "إعدادات المنشأة" داخل قسم "الإعداد الأولي" بالسايد بار مُخفي أصلاً منذ §13 (القسم كله مُغلَّف
  بـ `{% if not request.branch %}` + `has_perm_key:'view_tenant_settings'`) — لا تغيير مطلوب هناك.

### 16.2 الحجب الفعلي على مستوى الـ view (`deny_branch_scoped`)

أُضيف decorator جديد بـ `apps/accounts/decorators.py`:

```python
def deny_branch_scoped(view_func):
    """يمنع أي مستخدم مرتبط بفرع من الوصول لهذا الـ view مهما كانت صلاحياته
    الممنوحة عبر مجموعات الصلاحيات."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        if getattr(request, 'branch', None):
            return _deny(request)  # 403 JSON لو AJAX، وإلا redirect لـ core:no_permission
        return view_func(request, *args, **kwargs)
    return wrapper
```

نفس نمط `require_capability`/`require_plan_feature` الموجودَين مسبقاً بنفس الملف (حجب غير قابل
للتجاوز حتى لمدير النشاط نفسه في تلك الحالتين — هنا العكس: يُطبَّق على مستخدم الفرع، ويُستثنى منه
مدير النشاط والسوبريوزر). أُضيف كطبقة **إضافية** فوق `@require_permission(...)` الموجود مسبقاً على كل
view (لا يستبدله) في `apps/core/views.py`، على الستة views التالية (كل الروابط/الـ APIs الخاصة
بالاشتراك وسعر الصرف وإعدادات النشاط):

```python
@login_required
@deny_branch_scoped
@require_permission('view_tenant_settings')
def tenant_settings(request): ...

@login_required
@deny_branch_scoped
@require_permission('change_tenant_settings')
@require_POST
def tenant_settings_update_api(request): ...

@login_required
@deny_branch_scoped
@require_permission('change_tenant_settings')
@require_POST
def exchange_rate_update_api(request): ...

@login_required
@deny_branch_scoped
@require_permission('change_tenant_settings')
def exchange_rate_page(request): ...

@login_required
@deny_branch_scoped
@require_permission('change_tenant_settings')
def exchange_rate_history_api(request): ...

@login_required
@deny_branch_scoped
def subscription_info(request): ...  # `core:subscription` — ملاحظة: لم يكن عليها أي حماية
                                       # صلاحيات من الأساس، `@login_required` فقط. أي مستخدم فرع
                                       # كان يقدر يفتحها بالرابط المباشر قبل هذا الإصلاح.
```

### 16.3 لماذا هذا التصميم

- الحجب عبر القالب وحده (`{% if not request.branch %}`) يمنع **الظهور** فقط، وليس **الوصول**. مستخدم
  فرع يعرف الرابط المباشر (`/settings/tenant/`, `/settings/exchange-rate/`, `/subscription/`) كان
  يقدر يفتحه ويشوف/يعدّل بيانات مستوى النشاط كامل — عيب أمني حقيقي، مش مجرد تجميل واجهة.
  `deny_branch_scoped` يسد هذه الثغرة بغض النظر عن القالب.
- `subscription_info` كانت الأخطر: لا يوجد عليها أي `@require_permission` من الأساس (فقط
  `@login_required`)، فحتى مستخدم فرع بلا أي صلاحيات ممنوحة كان بإمكانه الوصول إليها.
- لم يُستخدَم `BRANCH_BLOCKED_KEYS` (نمط §13) لأن المفتاحين `view_tenant_settings`/
  `change_tenant_settings` غير مُدرَجين فيها أصلاً (فقط مُستبعَدين من منح مشرف الفرع التلقائي)،
  ولإضافتهما لـ `BRANCH_BLOCKED_KEYS` كان سيتطلب تدقيق كل استخدام آخر لنفس المفتاحين في الكود
  للتأكد من عدم كسر شيء غير متعلق بهذه الصفحات الثلاث تحديداً — الحل الأضيق والأوضح كان decorator
  مخصص على الـ views الستة نفسها.

### الفحوصات

`python manage.py check` نظيف في كلا المشروعين (نفس تحذير `rest_framework.W001` القديم فقط، بلا
migrations جديدة — لا تعديل على أي model). فحص تجميع القالبين (`navbar.html` في كلا المشروعين) عبر
`django.template.loader.get_template` نجح بلا أخطاء. تشغيل `apps.core` و`apps.accounts` (كل الاختبارات
فيهما) في كلا المشروعين نجح بالكامل.
