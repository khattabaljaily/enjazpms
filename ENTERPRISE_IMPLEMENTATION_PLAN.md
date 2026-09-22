# خطة تنفيذ نسخة المؤسسات (Enterprise Multi-Branch) — Implementation Plan

> مرحلة تخطيط فقط — لا يوجد أي كود تنفيذي كامل في هذا المستند ولا أي تعديل على كود المشروع.
> المرجع الرسمي: `ENTERPRISE_ARCHITECTURE_SPEC.md`
> مرجع التشخيص: `ENTERPRISE_GAP_ANALYSIS.md`
> تاريخ الإعداد: 2026-09-22

## ملاحظة افتتاحية

شجرة المشروع وقت إعداد هذه الخطة فيها تعديلات غير محفوظة (uncommitted) في: `apps/accounts/decorators.py`, `apps/accounts/forms.py`, `apps/accounts/models.py`, `apps/accounts/views.py`, `apps/core/templates/components/navbar.html`, `apps/core/templates/components/sidebar.html`, `apps/core/views.py`, `apps/store/views.py`. هذه الخطة لا تفترض شيئاً عن محتواها ولا تلمسها. **قبل بدء Phase 1 فعلياً**، يجب على الفريق حسم هذه التعديلات (commit أو مراجعة أو تراجع) حتى لا تختلط مع فرع العمل الجديد — هذا أول بند تنفيذي عملي قبل أي كود.

هذه الخطة تبني على حقيقة مهمة من الـ Gap Analysis: **البنية التحتية الأساسية موجودة فعلاً** (`Tenant.version_type`, `Branch`, `Stock.branch`, `User.branch/branch_role`, `TenantQuerySet.for_branch`, `filter_by_branch_via`, `require_company_owner`). المطلوب ليس بناء من الصفر، بل: (أ) تقوية نقاط الربط الضعيفة، (ب) تحويل الإنفاذ من "اتفاقية يدوية" إلى "آلية مركزية"، (ج) فصل صلاحيات مدير الفرع فعلياً عن مدير النشاط، كل ذلك مع عزل تام لسلوك single_store وmulti_stock.

---

## 1. تعريف نوع النسخة (Version Type)

### القرار
**لا حاجة لموديل جديد.** `Tenant.version_type` الموجود حالياً (`single_store` / `multi_stock` / `multi_branch`) هو المصدر الوحيد للحقيقة (source of truth) لنوع النسخة، ويبقى كذلك. هذا يطابق مباشرة القسم الحادي عشر من المواصفة الذي يطلب "تحديد نوع النسخة بوضوح" بقيم مطابقة تماماً للأسماء الثلاث.

### التعديل المطلوب (قرار تصميم، ليس كوداً بعد)
الفجوة رقم 6 في التقرير: `version_type` مُشتق جبراً من `subscription_plan` عبر `PLAN_LIMITS[plan]['allowed_version_types'][-1]` في `TenantForm.clean()`. القرار المقترح:

- **الإبقاء على القيد الحالي كسلوك افتراضي** (كل باقة تفرض حداً أقصى منطقي لنوع النسخة) — لا نغيّر هذا الآن لتفادي مخاطرة غير ضرورية خارج نطاق مشروع Enterprise.
- **نضيف فقط** مساراً صريحاً لاحقاً (خارج نطاق هذه الخطة، يُذكر هنا للتوثيق فقط) لو احتاج فريق المبيعات "enterprise-lite" — ليس جزءاً من Phase 1-5.

### مبدأ العزل التقني (Gate واحد لكل شيء)
كل منطق Enterprise الجديد (middleware enforcement، الأدوار الجديدة، الـ Dashboard الموحد، الخزينة المركزية) يُفعَّل عبر **دالة/property واحدة مركزية**:

```python
# apps/core/models.py على Tenant، أو helper في apps/core/utils.py
def is_enterprise(self) -> bool:
    return self.version_type == 'multi_branch'
```

**قاعدة صارمة:** أي كود جديد يخص Enterprise يجب أن يُغلَّف بشرط `tenant.is_enterprise()` (أو ما يعادله عبر `request.tenant`). لا استثناءات. هذا هو خط الدفاع الأول لضمان أن single_store/multi_stock لا يريان أي فرق في السلوك.

---

## 2. تعديلات الموديلات المطلوبة

### 2.1 حقل `branch` مباشر على السجلات المالية الرئيسية

الفجوة رقم 3 (عالية): `SaleInvoice`, `PurchaseInvoice`, `SaleReturn`, `PurchaseReturn` ترتبط بالفرع بشكل غير مباشر فقط عبر `stock.branch` (أو `original_invoice.stock.branch`). نضيف حقل `branch` FK **مباشر واختياري (nullable)** على:

- `SaleInvoice.branch`
- `PurchaseInvoice.branch`
- `SaleReturn.branch`
- `PurchaseReturn.branch`

```python
# مثال توضيحي — ليس كوداً نهائياً
branch = models.ForeignKey(
    'core.Branch', null=True, blank=True,
    on_delete=models.PROTECT, related_name='%(class)s_set'
)
```

**لماذا PROTECT لا SET_NULL:** لمنع حذف فرع فيه فواتير تاريخية بصمت؛ حذف/إيقاف فرع يجب أن يمر بمسار "أرشفة/إيقاف" (`Branch.is_active=False`) لا حذف فعلي.

### 2.2 خطة Migration آمنة (لا تكسر بيانات single_store/multi_stock)

الخطوات (كل خطوة migration منفصلة قابلة للـ rollback):

1. **Migration A** — إضافة الحقل كـ `null=True, blank=True` بدون أي default إجباري. لا تأثير فوري على أي صف موجود.
2. **Migration B (Data migration منفصلة)** — تعبئة الحقل تلقائياً للسجلات القديمة: `branch = stock.branch` (أو عبر `original_invoice.stock.branch` للمرتجعات). تُنفَّذ بـ `RunPython` مع batch processing (`iterator()` + `bulk_update`) لتفادي قفل الجدول على قواعد بيانات كبيرة.
3. **لا Migration تجعل الحقل `NOT NULL`** — يبقى nullable دائماً، لأن:
   - النسخة الأولى/الثانية (single_store/multi_stock) لا تملك فروعاً حقيقية أصلاً (أو فرع افتراضي واحد فقط) — لا داعي لإجبارها على قيمة.
   - فواتير قديمة قد تكون بلا `stock` من الأساس (حالة نادرة موثقة في التقرير) فلا يوجد مصدر لتعبئة الحقل.
4. **منطق الحفظ (`save()` / signal `pre_save`)**: عند إنشاء فاتورة جديدة، إن كان `branch` فارغاً و`stock.branch` موجوداً، يُملأ تلقائياً من `stock.branch` (fallback شفاف، لا يتطلب تغيير أي view موجود في single_store/multi_stock لأن `stock.branch` غالباً `None` هناك أصلاً).
5. **الفهرسة:** إضافة `db_index=True` أو composite index `(tenant, branch)` على الحقول الجديدة لدعم فلترة التقارير بأداء جيد (يرتبط بالمخاطر في القسم 10).

### 2.3 نموذج "مدير الفرع" الصريح على `Branch`

الفجوة رقم 5: لا يوجد حقل "مدير الفرع" على `Branch` نفسه؛ الربط معكوس (على `User`). نضيف:

```python
# apps/core/models.py — على Branch
manager = models.ForeignKey(
    'accounts.User', null=True, blank=True,
    on_delete=models.SET_NULL, related_name='managed_branches'
)
```

هذا حقل **إضافي مساعد للعرض والإدارة السريعة**، ولا يلغي `User.branch_role='manager'` — بل الاثنان يجب أن يتزامنا عبر منطق عمل مركزي (تفصيل في القسم 7). الحقل nullable ولا يُستخدم إطلاقاً خارج `tenant.is_enterprise()`.

### 2.4 تصنيف الخزينة (Treasury Kind)

الفجوة رقم 4/8: لا يوجد تمييز نوعي للخزينة. نضيف حقل اختياري:

```python
# apps/treasury/models.py — على Treasury
TREASURY_KIND_CHOICES = [
    ('branch', 'خزينة فرع'),
    ('owner', 'خزينة مدير النشاط (مركزية)'),
    ('shared', 'خزينة مشتركة/عامة'),
]
treasury_kind = models.CharField(
    max_length=10, choices=TREASURY_KIND_CHOICES, default='branch'
)
```

**خطة Migration:** حقل جديد بـ `default='branch'` (آمن رجعياً: كل خزينة قديمة تُصنَّف "فرع" ما لم تكن `branch IS NULL`، وعندها Data migration بسيطة تجعلها `owner` تلقائياً إن كان `is_system_default=True` أو `branch IS NULL`). لا تغيير في سلوك single_store/multi_stock لأنها أصلاً لا تستخدم هذا الحقل في أي شرط عمل.

### 2.5 ملخص جدول الموديلات المعدَّلة

| الموديل | الحقل الجديد | Nullable | يؤثر على single/multi_stock؟ |
|---|---|---|---|
| `SaleInvoice` | `branch` FK | نعم | لا (fallback من stock.branch، القيمة تبقى None فعلياً) |
| `PurchaseInvoice` | `branch` FK | نعم | لا |
| `SaleReturn` | `branch` FK | نعم | لا |
| `PurchaseReturn` | `branch` FK | نعم | لا |
| `Branch` | `manager` FK | نعم | لا (يُستخدم فقط عند is_enterprise) |
| `Treasury` | `treasury_kind` | لا (default) | لا (يُستخدم فقط في قواعد enterprise) |

---

## 3. آلية إنفاذ العزل المركزية (بديل الاعتماد اليدوي على `for_branch()`)

هذا هو أهم بند في الخطة (الفجوة الحرجة رقم 1 و2). الهدف: تحويل الفلترة من "خطوة اختيارية ينساها المطوّر" إلى "سلوك افتراضي يحتاج إلغاءً صريحاً".

### 3.1 طبقة الـ Manager/QuerySet — إنفاذ افتراضي على مستوى الاستعلام

`TenantQuerySet.for_branch()` الحالية تبقى كما هي (لا نكسرها)، لكن نضيف **manager جديداً مُلزماً** فوقها لموديلات Enterprise الحساسة فقط:

```python
# مثال توضيحي فقط
class BranchScopedManager(TenantManager):
    def for_request(self, request):
        qs = self.for_tenant(request.tenant)
        if request.tenant.is_enterprise() and request.branch is not None:
            return qs.for_branch(request.branch)
        return qs  # single/multi_stock أو مدير مركزي: بلا تغيير في السلوك
```

الفرق الجوهري عن الوضع الحالي: بدل أن يستدعي كل view دالة الفلترة يدوياً بنفسه، **الـ View/Mixin يستدعي `Model.objects.for_request(request)` كنقطة دخول موحّدة واحدة**، وأي مطوّر جديد يتبع هذا النمط تلقائياً لأنه الطريقة "الطبيعية" لجلب البيانات، لا استثناءً يجب تذكره.

### 3.2 Middleware — حقن سياق الفرع + علم enforcement

`TenantMiddleware` الحالي يحقن `request.branch`. نضيف طبقة صغيرة فوقه (بدون تعديل سلوكه الحالي لغير enterprise):

```python
# مثال توضيحي
class BranchEnforcementMiddleware:
    def __call__(self, request):
        request.branch_scope_required = (
            getattr(request, 'tenant', None)
            and request.tenant.is_enterprise()
            and request.branch is not None
        )
        return self.get_response(request)
```

هذا العلم يُستخدم في الـ Mixin (القسم 3.3) ليقرر هل يفرض الفلترة أم لا — **مضمون أنه `False` دائماً لـ single_store/multi_stock**.

### 3.3 Mixin موحّد للـ Class-Based Views

```python
# مثال توضيحي
class BranchScopedViewMixin:
    branch_lookup_field = 'branch'  # أو 'stock__branch' لموديلات غير مباشرة

    def get_queryset(self):
        qs = super().get_queryset()
        if getattr(self.request, 'branch_scope_required', False):
            qs = filter_by_branch_via(qs, self.request.branch, field=self.branch_lookup_field)
        return qs
```

يُضاف هذا الـ Mixin تدريجياً (Phase بعد Phase) لكل CBV يعرض بيانات حساسة، بدل حذف استدعاءات `for_branch()` القديمة فوراً (الاثنان يتعايشان أثناء الانتقال — انظر مبدأ Strangler في القسم 9).

### 3.4 أداة تدقيق/Lint (خط دفاع ثانٍ)

سكربت فحص (management command) يُشغَّل في CI: يمر على كل View/APIView يستخدم موديلاً من قائمة "الموديلات الحساسة للفرع" (`SaleInvoice`, `PurchaseInvoice`, `Expense`, `Stock`, ...) ويتحقق أنه إما (أ) يرث `BranchScopedViewMixin`، أو (ب) يستدعي `for_branch`/`filter_by_branch_via` صراحة في جسمه، أو (ج) مُعلَّم صراحة بديكوريتر `@branch_scope_exempt(reason=...)` لتوثيق الاستثناء عمداً. أي View جديد لا يطابق أياً من الثلاثة يفشل الفحص. هذا يحوّل "النسيان" من خطأ صامت إلى فشل CI ظاهر.

### 3.5 كيف يبقى مضموناً معزولاً عن single_store/multi_stock

- كل نقاط الإنفاذ أعلاه مشروطة بـ `tenant.is_enterprise()` (مباشرة أو عبر `request.branch_scope_required`).
- `for_branch()`/`filter_by_branch_via()` الحاليتين **لا تُحذفان ولا تتغيران** — الكود القديم الذي يستدعيهما يدوياً في single_store/multi_stock يستمر بالعمل بنفس السلوك بالضبط (لأن `request.branch` أصلاً `None` غالباً لهاتين النسختين، فالفلترة "no-op").
- اختبار عزل صريح (Phase 5): تشغيل نفس اختبارات القبول القديمة (إن وُجدت) على tenant من نوع single_store قبل وبعد كل Phase، والتأكد من تطابق النتائج بايتاً بايت (regression baseline).

---

## 4. إعادة هيكلة الأدوار والصلاحيات

### 4.1 القرار الأساسي (يحسم فجوة رقم 1 وسؤال التقرير رقم 1)

**لا نستبدل نظام JSON Permissions الحالي بـ Django Groups/Permissions القياسي** — هذا تغيير جذري عالي المخاطر وغير ضروري؛ نظام `PermissionGroup` الحالي (per-tenant، JSONField) يعمل ومناسب لبنية multi-tenant. **القرار: نُبقي آلية `PermissionGroup` كما هي كمحرك صلاحيات**، لكن **نفصل فعلياً** صلاحيات `branch_role='manager'` عن `is_tenant_admin` بدل منحهما نفس الاتساع.

### 4.2 التعديل المحدد

في `User.get_permission_keys()` / `has_perm_key()` (القسم الأكثر خطورة في التقرير):

- `is_tenant_admin` أو `is_superuser`: يبقى تجاوزاً كاملاً (كما هو) — **فقط عندما `not tenant.is_enterprise()` أو `request.branch is None`**.
- عندما `tenant.is_enterprise() and user.is_branch_manager()`: **لا يُمنح تجاوزاً كاملاً بعد الآن**. بدلاً من ذلك، يُقيَّد بمجموعة صلاحيات محددة مسبقاً — إمّا:
  - (أ) `PermissionGroup` مخصصة اسمها الاصطلاحي "مدير فرع" تُنشأ تلقائياً لكل tenant من نوع enterprise عند الترقية (تحتوي كل مفاتيح الصلاحيات التشغيلية: مبيعات/مشتريات/استلام/مصروفات/مخزون/تقارير فرع)، أو
  - (ب) علم صريح على الصلاحيات نفسها في `apps/accounts/permissions.py`: `scope = 'branch' | 'tenant'` لكل permission key، بحيث مدير الفرع يحصل تلقائياً على كل مفاتيح `scope='branch'` فقط، ولا يحصل أبداً على مفاتيح `scope='tenant'` (إدارة الفروع، إدارة المستخدمين المركزية، إعدادات النشاط).

**التوصية:** الخيار (ب) أفضل هندسياً (مصدر حقيقة واحد في `permissions.py`، لا حاجة لصيانة PermissionGroup تلقائية لكل tenant)، لكنه يتطلب مراجعة شاملة لملف `permissions.py` (غير مفحوص بالتفصيل بعد — أول خطوة عملية في Phase 3 هي فحصه كاملاً قبل القرار النهائي بين (أ) و(ب)).

### 4.3 ربط صريح User↔Branch↔Role

نحتفظ بـ `User.branch` + `User.branch_role` كما هما (لا داعي لموديل ربط M2M جديد ما دام المستخدم يتبع فرعاً واحداً فقط حسب المواصفة الحالية — "يجب ربط المستخدم بفرع محدد"). العملية الإدارية الجديدة (القسم 7 أدناه) هي ما يضمن الاتساق بين `User.branch_role='manager'` و`Branch.manager` (الحقل الجديد من القسم 2.3).

### 4.4 الموظف العادي (Employee role)

لا تغيير بنيوي — يبقى `branch_role='employee'` + `PermissionGroup` محددة (مبيعات فقط / مخزون فقط / إلخ) كما هو موصوف في القسم خامساً من المواصفة، وهذا **موجود فعلاً وناضج** حسب التقرير. لا عمل إضافي مطلوب هنا خارج التحقق ضمن Phase 5.

---

## 5. تطبيق الصلاحيات في كل الطبقات (Views, APIs, Querysets, Reports)

### 5.1 منهجية الجرد (Inventory) قبل التعديل

Phase مخصصة (Phase 2 جزئياً + Phase 3): جرد شامل بـ grep/AST لكل الـ Views/APIViews/Serializers التي تلمس الموديلات الحساسة للفرع (القائمة من القسم 3.4)، وتصنيفها في جدول تتبع (tracking sheet، يُحفظ كملف عمل منفصل لا كجزء من هذه الخطة):

| # | الملف | الـ View/API | الموديل | الحالة الحالية | الإجراء المطلوب |
|---|---|---|---|---|---|
| ... | ... | ... | ... | يستخدم for_branch؟ / لا شيء | تطبيق Mixin / توثيق استثناء |

### 5.2 خطوات عملية بدون كسر النسخة الأولى/الثانية

1. **لكل صف في جدول الجرد**: إضافة `BranchScopedViewMixin` (القسم 3.3) بدل حذف الكود القديم — الاثنان يتعايشان (idempotent: فلترة مزدوجة لا تُغيّر النتيجة، فقط تضمن التطبيق حتى لو نُسي الاستدعاء اليدوي).
2. **APIs (DRF أو ما يعادلها)**: تطبيق نفس الـ Mixin على مستوى `get_queryset()` في الـ ViewSet/APIView — نفس المنطق تماماً، حتى لا يبقى "ثغرة API" كما حذّرت المواصفة صراحة (القسم عاشراً، المثال: منع الوصول لـ API إنشاء فاتورة مباشرة).
3. **عمليات الكتابة (Create/Update/Delete)**: إضافة تحقق صريح في `form_valid()`/`perform_create()`: إن كان `request.branch_scope_required` ويحاول المستخدم إنشاء/تعديل سجل بـ`branch` مختلف عن `request.branch` → رفض (403)، بصرف النظر عن صلاحياته الأخرى. هذا يمنع "تلاعب مباشر بالـ API" (payload manipulation) حتى لو الواجهة لا تعرضه.
4. **Reports**: كل دالة تقرير تمر عبر نفس نقطة الدخول (`Model.objects.for_request(request)`) بدل بناء queryset يدوي من جديد؛ إعادة استخدام دوال التقارير الحالية (`sales/reports.py`, `stocks/reports.py`) مع حقنها بالفلتر الموحّد بدل إعادة كتابتها.
5. **مراجعة الاستثناءات**: أي View يتعمد تجاوز الفلترة (مثل شاشات `require_company_owner` — إدارة الفروع نفسها) يُعلَّم صراحة بديكوريتر `@branch_scope_exempt` (القسم 3.4) بدل تركه بلا فلترة بصمت.

---

## 6. الخزينة المركزية لمدير النشاط

### 6.1 التمييز في الموديل

يُستخدم الحقل الجديد `Treasury.treasury_kind` (القسم 2.4):

- `owner`: خزينة مدير النشاط المركزية. تُنشأ تلقائياً (Data migration + منطق عند ترقية tenant إلى enterprise) بـ `branch=None, treasury_kind='owner'`.
- `branch`: خزينة تابعة لفرع محدد (`branch` غير فارغ عادة).
- `shared`: احتياطي لأي حالة مركزية غير "owner" (مثال مستقبلي: خزينة مشروع مشترك بين فرعين) — خارج نطاق هذا الإصدار، الحقل يُضاف الآن لتفادي migration إضافية لاحقاً فقط.

### 6.2 قاعدة العمل (Business Rule)

- الرواتب/السلفيات/الخصومات المركزية (القسم سادساً بالمواصفة): تُصرف افتراضياً فقط من خزينة `treasury_kind='owner'` عندما `tenant.is_enterprise()`. يُطبَّق هذا كتحقق (validation) في نموذج/serializer الدفع، وليس قيداً على مستوى قاعدة البيانات (لإبقاء المرونة ولعدم كسر single_store/multi_stock التي لا تستخدم هذا التصنيف أصلاً).
- مدير الفرع (بعد فصل الصلاحيات في القسم 4) **لا يحصل على صلاحية الوصول لخزينة `owner`** إطلاقاً ضمن `scope='branch'` — هذا يُطبَّق تلقائياً بمجرد ربط مفتاح صلاحية "الوصول لخزينة مركزية" بـ `scope='tenant'`.

---

## 7. عملية تعيين مدير الفرع

### 7.1 التصميم المقترح

الدور يُعرَّف **على الفرع (Branch.manager) كمصدر حقيقة أساسي**، مع تزامن تلقائي إلى `User.branch_role`:

1. شاشة إدارية جديدة (ضمن صلاحية `require_company_owner`، فقط لمدير النشاط) لتعيين/تغيير مدير فرع: تختار Branch + User (يجب أن يكون بالفعل `User.branch == Branch` أو تُعيَّن معاً في نفس العملية).
2. **منطق منع التعارض (على مستوى الخدمة/service layer، لا Django DB constraint صارم لتفادي مخاطرة migration)**:
   - عند تعيين `Branch.manager = user_x`: تُنفَّذ في transaction واحدة:
     - إن كان هناك مدير سابق لنفس الفرع (`old_manager`)، يُخفَّض `branch_role` إلى `employee` تلقائياً (ما لم يُنقل لفرع آخر كمدير في نفس العملية).
     - `user_x.branch = branch`, `user_x.branch_role = 'manager'` يُحدَّثان معاً.
   - **قاعدة "مدير واحد نشط لكل فرع"**: تُفرض عبر تحقق في الخدمة (service function) قبل الحفظ، وليس `unique=True` على DB مباشرة (لتفادي مشاكل migration إن وُجدت بيانات تاريخية متعارضة تحتاج تنظيفاً يدوياً أولاً قبل تفعيل القيد الصارم لاحقاً).
3. **مسموح لاحقاً (خارج هذا الإصدار):** أكثر من "مشرف" على نفس الفرع بدور أضعف من "مدير" — غير مطلوب في المواصفة الحالية، يُذكر فقط للتوثيق.

### 7.2 لماذا لا نجعل `Branch.manager` هو المصدر الوحيد فوراً

النظام الحالي يعتمد على `User.branch_role` في القرارات (`is_branch_manager()`) في أماكن كثيرة عبر الكود. تغيير مصدر الحقيقة فجأة يخاطر بكسر أماكن غير مفحوصة. لذا: `Branch.manager` حقل **مرآة/مساعد** يُحدَّث بواسطة نفس العملية الإدارية التي تُحدّث `User.branch_role`، لا بديل فوري له. القرار بجعله المصدر الوحيد (وإزالة الاعتماد على `branch_role`) يُترك لمراجعة لاحقة بعد استقرار Phase 3.

---

## 8. Dashboard والتقارير الموحدة

### 8.1 مكوّن فلتر موحّد وقابل لإعادة الاستخدام

فلتر واحد (Python helper + مكوّن UI واحد) يُستخدم في كل شاشة تقرير/Dashboard:

```python
# مثال توضيحي — apps/core/reporting.py (اسم مقترح)
def resolve_report_scope(request):
    """
    يُرجع (branch_filter, period_filter, stock_filter) بمنطق موحّد:
    - إن لم يكن tenant.is_enterprise(): يتجاهل branch_filter دائماً (سلوك حالي بلا تغيير).
    - إن كان enterprise ومدير نشاط: افتراضي = كل الفروع، مع إمكانية اختيار فرع محدد من querystring.
    - إن كان enterprise ومدير فرع: مقفول على فرعه فقط (لا يظهر اختيار فرع آخر في الواجهة، ويُرفض في الـ backend لو أُرسل).
    """
```

هذا المكوّن يُستدعى من **كل** دالة تقرير (مبيعات، مشتريات، مصروفات، أرباح، مخزون، رواتب) بدل أن يبني كل تقرير منطق الفلترة بنفسه من جديد — يحل الفجوة رقم 7 (Dashboard موحّد غير مؤكد الوجود المنهجي).

### 8.2 خطوات التطبيق

1. بناء الـ helper + مكوّن UI مشترك (Phase 4).
2. تطبيقه أولاً على Dashboard الرئيسي لمدير النشاط (الأولوية القصوى حسب القسم سابعاً بالمواصفة).
3. تعميمه تدريجياً على تقارير كل موديول (نفس منهجية "جدول جرد" من القسم 5.1، لكن لملفات `reports.py`).
4. لكل تقرير: التأكد أن نفس فلتر الفرع يُطبَّق على الـ API الذي يغذي الرسوم/الجداول، لا فقط على الصفحة الأولى (تحذير صريح في المواصفة، القسم سابعاً/عاشراً).

---

## 9. ترتيب مراحل التنفيذ (Roadmap)

نهج "Strangler Fig": كل مرحلة تضيف طبقة جديدة دون حذف القديمة فوراً، وقابلة للتسليم والاختبار المستقل، مع regression check على single_store/multi_stock بعد كل مرحلة.

### Phase 0 — تمهيد (قبل أي كود Enterprise)
- حسم التعديلات غير المحفوظة الحالية في الشجرة (commit/مراجعة/تراجع).
- فحص تفصيلي لملف `apps/accounts/permissions.py` (لم يُفحص بعمق في الـ Gap Analysis) — مدخل ضروري لقرار القسم 4.2.
- إعداد baseline اختبارات/سيناريوهات يدوية على tenant single_store وmulti_stock حاليين (قبل أي تغيير) لاستخدامها كمرجع مقارنة لاحقاً.

### Phase 1 — الموديلات + Migrations
- إضافة `Tenant.is_enterprise()` helper.
- إضافة حقول `branch` على `SaleInvoice`/`PurchaseInvoice`/`SaleReturn`/`PurchaseReturn` + data migration للتعبئة التلقائية.
- إضافة `Branch.manager`.
- إضافة `Treasury.treasury_kind` + data migration للخزائن الموجودة.
- **معيار القبول:** تشغيل كامل الـ migrations على نسخة من قاعدة بيانات إنتاج (staging) بدون أي خطأ، والتحقق أن عدد الصفوف والقيم القديمة في single_store/multi_stock لم يتغيّر.

### Phase 2 — Middleware + آلية الإنفاذ المركزية
- `BranchEnforcementMiddleware` (أو تمديد `TenantMiddleware` الحالي).
- `BranchScopedManager`/`for_request()`.
- `BranchScopedViewMixin`.
- أداة تدقيق CI (`branch_scope_exempt` + فحص جرد).
- **معيار القبول:** تشغيلها على tenant enterprise تجريبي وتحقق العزل بين فرعين وهميين، مع تأكيد صفر تغيير في سلوك tenant single_store/multi_stock تجريبي (نفس الاستعلامات، نفس النتائج).

### Phase 3 — الأدوار والصلاحيات
- فصل صلاحيات `branch_role='manager'` عن `is_tenant_admin` (القرار بين خيار أ/ب من القسم 4.2).
- عملية تعيين مدير الفرع (القسم 7) + تزامن `Branch.manager`↔`User.branch_role`.
- تطبيق `require_company_owner` + الفصل الجديد على كل شاشات الإدارة المركزية.
- **معيار القبول:** مدير فرع تجريبي لا يستطيع الوصول لأي شاشة/API إدارة مركزية حتى بمحاولة استدعاء مباشر، ويحتفظ بكل صلاحياته التشغيلية داخل فرعه فقط.

### Phase 4 — Dashboard والتقارير الموحدة
- بناء `resolve_report_scope()` + مكوّن الفلتر الموحّد.
- تطبيقه على Dashboard مدير النشاط أولاً، ثم تعميمه على تقارير المبيعات/المشتريات/المصروفات/المخزون/الرواتب.
- تفعيل قاعدة عمل خزينة `owner` للرواتب المركزية.
- **معيار القبول:** Dashboard يعرض إجمالي كل الفروع افتراضياً لمدير النشاط، وفلتر "فرع محدد" يطابق تقرير ذلك الفرع منفرداً رقمياً.

### Phase 5 — اختبار شامل بالسيناريوهات الـ20
- تنفيذ كل السيناريوهات الـ20 من القسم الثاني عشر بالمواصفة، مع تركيز خاص على 8 و9 و10 و20 (العزل وعدم تأثر النسخة الأولى/الثانية).
- Regression كامل مقابل baseline الـ Phase 0.
- **معيار القبول:** الـ 20 سيناريو تمر بنجاح موثقة، وصفر انحراف في سلوك single_store/multi_stock عن الـ baseline.

---

## 10. تقدير المخاطر

| # | الخطر | الاحتمال/الأثر | كيفية التجنب |
|---|---|---|---|
| 1 | **كسر بيانات قديمة عند إضافة حقل `branch` المباشر** (خصوصاً data migration على جدول فواتير كبير في الإنتاج) | متوسط / عالٍ | Migration على مرحلتين (schema ثم data)، تنفيذ الـ data migration بـ batching، اختبار كامل على نسخة staging من بيانات الإنتاج قبل التطبيق الفعلي، الحقل يبقى nullable دائماً فلا فشل قيود. |
| 2 | **تضارب/تراكب صلاحيات عند فصل مدير الفرع عن مدير النشاط** (مستخدمون حاليون بـ`branch_role='manager'` قد يفقدون وصولاً كانوا يعتمدون عليه فعلياً بحكم الأمر الواقع) | عالٍ | تفعيل الفصل خلف `tenant.is_enterprise()` فقط (tenants قديمة single/multi_stock غير متأثرة إطلاقاً)، وتشغيل الفصل الجديد أولاً على tenant تجريبي/pilot واحد قبل التعميم، مع سجل تدقيق (audit log) للصلاحيات المرفوضة خلال أول أسبوعين لرصد أي كسر غير متوقع. |
| 3 | **أداء الاستعلامات بعد إضافة فلترة مركزية إضافية** (JOIN إضافي عبر `filter_by_branch_via` على تقارير كبيرة، أو ازدواج الفلترة بين Mixin وكود يدوي قديم) | متوسط / متوسط | إضافة indexes مركّبة (`tenant, branch`) على الحقول الجديدة والموجودة، قياس أداء التقارير الثقيلة (مبيعات/مخزون) على بيانات staging بحجم واقعي قبل الدمج، إزالة الفلترة اليدوية المكررة تدريجياً بعد التأكد من استقرار الـ Mixin (لا إبقاء فلترتين دائماً). |
| 4 | **نسيان تطبيق الفلترة/الصلاحيات في نقطة وصول جديدة أو غير مفحوصة** (نفس النمط الذي تكرر تاريخياً حسب سجل git: SaleReturn/PurchaseReturn، dashboard stats) | عالٍ (تكرر فعلياً من قبل) | أداة تدقيق CI الإلزامية (القسم 3.4) تمنع دمج أي View/API جديد على موديل حساس بلا Mixin أو استثناء موثّق؛ جدول الجرد الشامل (القسم 5.1) يغطي كل الكود الحالي لا الجديد فقط. |
| 5 | **تسريب تأثير غير مقصود على single_store/multi_stock** بسبب مشاركة كود (middleware، managers، helpers) بين كل الأنواع الثلاثة | متوسط / عالٍ جداً (مخالفة مباشرة لأهم قيد في المواصفة) | كل نقطة إنفاذ جديدة مشروطة صراحة بـ `tenant.is_enterprise()` كخط دفاع أول، واختبار regression تلقائي بعد كل Phase يقارن سلوك tenant single_store وmulti_stock تجريبيين قبل/بعد بايتاً بايت، ولا يُدمج أي Phase قبل نجاح هذا الاختبار. |

---

## خلاصة القرارات الأساسية

1. `Tenant.version_type` الحالي يبقى المصدر الوحيد لنوع النسخة — لا موديل جديد، فقط helper `is_enterprise()`.
2. حقول `branch` مباشرة (nullable) تُضاف على الفواتير والمرتجعات، مع fallback تلقائي من `stock.branch` وmigration بمرحلتين آمنتين.
3. الإنفاذ المركزي يُبنى كطبقة إضافية (Manager + Middleware + Mixin + أداة CI) تتعايش مع `for_branch()`/`filter_by_branch_via()` الحاليتين، لا تستبدلهما فوراً.
4. نظام `PermissionGroup` (JSON) الحالي يبقى المحرك، لكن صلاحيات مدير الفرع تُفصل فعلياً عن مدير النشاط عبر `scope='branch'|'tenant'` بدل الاعتماد على فلترة البيانات فقط.
5. `Branch.manager` حقل مرآة جديد يتزامن مع `User.branch_role` عبر عملية إدارية واحدة تمنع تعارض أكثر من مدير نشط لكل فرع.
6. `Treasury.treasury_kind` يميّز خزينة مدير النشاط المركزية (`owner`) عن خزائن الفروع، مع قاعدة عمل تقيّد صرف الرواتب المركزية منها فقط في نسخة enterprise.
7. كل نقطة إنفاذ/فلترة/صلاحية جديدة مشروطة صراحة بـ `tenant.is_enterprise()` — هذا هو ضمان العزل الوحيد والمطلق عن single_store/multi_stock.
8. التنفيذ يتبع نهج Strangler عبر 5 مراحل (+ Phase 0 تمهيدي)، كل مرحلة تُختم بمعيار قبول واضح وregression check قبل الانتقال للتالية.
