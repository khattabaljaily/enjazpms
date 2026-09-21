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

**تنبيه مهم:** لم يتم اختبار هذا العمل مقابل قاعدة بيانات حقيقية (لا يوجد اتصال DB متاح في بيئة
التطوير المحلية وقت التنفيذ) — فقط `check`/`makemigrations --dry-run` عبر بيئة افتراضية مؤقتة
(`/tmp/enjazpms_check_venv`). **يجب اختبار تسجيل الدخول بحساب شركة متعددة الفروع فعلياً قبل أي نشر
للإنتاج**، والتحقق من:
- ظهور القوائم (عملاء، موردون، موظفون، وكلاء، خزائن، حسابات بنكية، مصروفات، فواتير مبيعات/مشتريات،
  مخزون) مفلترة حسب فرع المستخدم فقط.
- عدم ظهور أي فرق في السلوك لمستخدم بدون فرع (باقة عادية) — يجب أن يرى كل شيء كالسابق تماماً.
- عمل التحويلات بين الفروع (مخزون/خزينة/حساب بنكي) بشكل طبيعي دون قيود.
- ظهور سجل النشاطات مفلتراً حسب الفرع.
