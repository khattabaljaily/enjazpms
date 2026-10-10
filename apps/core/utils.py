CURRENCY_SYMBOLS = {
    'SDG': 'ج.س', 'USD': '$', 'CNY': '¥', 'AED': 'د.إ',
    'SAR': 'ر.س', 'EGP': 'ج.م', 'EUR': '€', 'GBP': '£',
    'JOD': 'د.أ', 'KWD': 'د.ك', 'QAR': 'ر.ق', 'BHD': 'د.ب', 'OMR': 'ر.ع',
}

CURRENCY_NAMES_AR = {
    'USD': 'الدولار الأمريكي', 'CNY': 'اليوان الصيني', 'AED': 'الدرهم الإماراتي',
    'SAR': 'الريال السعودي', 'EGP': 'الجنيه المصري', 'EUR': 'اليورو',
    'GBP': 'الجنيه الإسترليني', 'JOD': 'الدينار الأردني', 'KWD': 'الدينار الكويتي',
    'QAR': 'الريال القطري', 'BHD': 'الدينار البحريني', 'OMR': 'الريال العماني',
    'SDG': 'الجنيه السوداني',
}


def currency_symbol(code):
    return CURRENCY_SYMBOLS.get((code or '').upper(), code or '')


def convert_arabic_numerals(value):
    """Convert Arabic numerals to English in a string."""
    arabic_digits = '٠١٢٣٤٥٦٧٨٩'
    english_digits = '0123456789'
    return ''.join(english_digits[arabic_digits.index(c)] if c in arabic_digits else c for c in str(value))


def filter_by_branch_via(qs, branch, field='stock__branch'):
    """
    فلترة queryset حسب الفرع عبر علاقة غير مباشرة (مثال: فاتورة → مخزن → فرع).
    branch=None لا يفلتر شيء. عند تحديد فرع لا تظهر إلا سجلات ذلك الفرع.
    """
    if branch is None:
        return qs
    return qs.filter(**{field: branch})


def filter_by_branch_strict(qs, branch, field='stock__branch'):
    """
    مثل filter_by_branch_via لكن صارم: عند اختيار فرع لا تظهر إلا سجلات ذلك
    الفرع، ولا تُضاف سجلات المخازن/الحسابات التي بلا فرع. تُستعمل في
    لوحة التحكم والتحليلات والتقارير حتى لا تبقى أرقام ثابتة عند تغيير الفرع.
    branch=None (إجمالي المؤسسة) لا يفلتر شيئاً.
    """
    if branch is None:
        return qs
    return qs.filter(**{field: branch})


def resolve_report_scope(request):
    """
    يحسم أي فرع يُستخدم لفلترة أي Dashboard/تقرير لهذا الطلب — نقطة الدخول
    الموحّدة المطلوبة في خطة تنفيذ Enterprise (القسم 8.1)، بدل أن تقرأ كل
    شاشة request.branch مباشرة كما كانت تفعل قبل هذا التعديل.

    يُرجع (branch, is_central_admin, branches_for_filter):
      - branch: كائن Branch أو None (None = بلا فلترة/إجمالي كل الفروع).
      - is_central_admin: True فقط لو Enterprise ومستخدم مركزي (request.branch
        فارغ) — عندها فقط تُعرض قائمة اختيار فرع في الواجهة.
      - branches_for_filter: فروع المشترك النشطة (لملء القائمة المنسدلة)، أو
        queryset فارغ إن لم يكن Enterprise/مستخدم مركزي.

    single_store/multi_stock: request.branch دائماً None أصلاً، فتُرجع
    (None, False, فارغ) — نفس سلوك getattr(request, 'branch', None) القديم
    تماماً، صفر تغيير. مستخدم محصور بفرعه (branch supervisor/موظف): يبقى
    مقفولاً على request.branch بصرف النظر عمّا يُرسله في querystring — لا
    تفويض من العميل، الحسم من جانب الخادم فقط.
    """
    from apps.core.models import Branch

    tenant = getattr(request, 'tenant', None)
    user_branch = getattr(request, 'branch', None)

    if not tenant or not tenant.is_enterprise() or user_branch is not None:
        return user_branch, False, Branch.objects.none()

    branches_for_filter = Branch.objects.filter(tenant=tenant, is_active=True)
    requested_id = request.GET.get('branch')
    if requested_id and str(requested_id).isdigit():
        branch = branches_for_filter.filter(pk=requested_id).first()
        if branch:
            return branch, True, branches_for_filter
    return None, True, branches_for_filter


def operational_money_accounts(qs, request):
    """
    الخزائن/الحسابات البنكية التي يجوز للمستخدم الحالي استخدامها في عمليات
    يومية (تحصيل، صرف، سداد، رواتب، مصروفات...).

    - تستبعد دائماً حسابات الإدارة المركزية: هذه تُدار من شاشة الإدارة
      المركزية فقط عبر التحويلات، ولا تُستعمل في عمليات الفروع.
    - مستخدم مربوط بفرع: حسابات فرعه فقط — وإلا استطاع مدير فرع الصرف من
      خزينة فرع آخر أو الإيداع فيها بمجرد تمرير معرّفها.
    بلا فرع (single_store / multi_stock): لا تغيير، إذ لا توجد حسابات مركزية.
    """
    qs = qs.filter(is_head_office=False)
    branch = getattr(request, 'branch', None)
    if branch is not None:
        qs = qs.filter(branch=branch)
    return qs


def enforce_branch_ownership(request, obj, field='branch'):
    """
    يتحقق أن كائناً مُحمَّلاً بالفعل (عادة عبر get_object_or_404(Model.objects
    .for_tenant(tenant), pk=pk) في شاشة "تفاصيل/تعديل/حذف سجل واحد") ينتمي
    لفرع المستخدم الحالي، قبل عرضه أو التعامل معه — يمنع IDOR عبر الفروع
    (مستخدم بفرع يُخمِّن رقم pk تسلسلي لسجل فرع آخر في نفس الـ tenant).

    مكمِّل لـ for_branch()/filter_by_branch_via() (اللذان يفلتران القوائم)
    لا بديل عنهما — هذا للحالة التي يُجلب فيها سجل واحد بمعرّفه مباشرة، حيث
    فلترة القوائم لا تنطبق أصلاً. اكتُشفت الحاجة له عبر أداة التدقيق
    check_branch_scoping (خطة تنفيذ Enterprise، القسم 3.4) التي كشفت أن
    عشرات شاشات "تفاصيل" تُصفّي بـ tenant فقط دون الفرع.

    - request.branch فارغ (مستخدم مركزي / نسخة single_store/multi_stock):
      لا تحقق — يرى كل شيء كالمعتاد، صفر تغيير في السلوك.
    - obj بلا فرع محدد على طول المسار: يُرفض لمستخدم الفرع (404) — كل شغل
      الفرع مستقل ولا سجلات بلا فرع (للتحويلات ذات الطرفين استثناء، أدناه).
    - غير ذلك وفرع الكائن يخالف فرع الطلب: Http404 (لا نكشف حتى وجود
      السجل، بنفس أسلوب get_object_or_404 القياسي في Django).

    field يدعم علاقات غير مباشرة بنفس صيغة filter_by_branch_via (مثال:
    'stock__branch' أو 'invoice__branch'). يقبل أيضاً قائمة مسارات لموديلات
    "التحويل" ذات الطرفين (StockTransfer.from_stock/to_stock،
    TreasuryTransfer.from_treasury/to_treasury...): تُرفض العملية فقط لو
    تحدَّد الفرع في **كل** المسارات وخالف فرع الطلب في كليهما؛ ظهور فرع
    الطلب في أي طرف (وارد أو صادر لفرعه) يكفي للسماح — نفس منطق "التحويل
    يخص فرعي لو كنت أحد طرفيه".
    """
    from django.http import Http404

    branch = getattr(request, 'branch', None)
    if branch is None:
        return

    fields = [field] if isinstance(field, str) else list(field)
    resolved = []
    for f in fields:
        target = obj
        for part in f.split('__'):
            target = getattr(target, part, None)
            if target is None:
                break
        resolved.append(target)

    # سجل بفرع واحد (مسار واحد) بلا فرع لا يخص أي فرع: يُرفض لمستخدم الفرع.
    # أما المسارات غير المباشرة وسجلات التحويل ذات الطرفين فتبقى مرنة (طرف الإدارة المركزية بلا فرع بالتصميم).
    if (len(fields) > 1 or '__' in fields[0]) and any(r is None for r in resolved):
        return

    if branch not in resolved:
        raise Http404


def enforce_transfer_branch_ownership(request, from_branch_id, to_branch_id):
    """
    فحص ملكية مخصَّص لإلغاء تحويل بين خزينة/حساب فرع وخزينة/حساب الإدارة
    المركزية — لا يصلح استخدام enforce_branch_ownership متعدد المسارات هنا:
    ذلك يتجاوز الفحص بالكامل لو تحدَّد فرع NULL على **أي** طرف (فلسفته أن
    NULL يعني بيانات قديمة اختيارية)، بينما هنا الطرف NULL (الإدارة
    المركزية) *دائماً* بلا فرع بالتصميم — لو استخدمناها لهذه الحالة، أي
    مستخدم فرع كان سيقدر يلغي أي تحويل يخص الإدارة المركزية بصرف النظر عن
    فرعه، لأن NULL يظهر في كل تحويل من هذا النوع.

    القاعدة الصحيحة هنا: مستخدم مركزي (request.branch=None، مدير النشاط)
    بلا قيد كالمعتاد؛ مستخدم فرع مسموح فقط لو فرعه هو أحد طرفي التحويل
    فعلياً (لا يكفي أن يكون الطرف الآخر NULL).
    """
    from django.http import Http404

    branch = getattr(request, 'branch', None)
    if branch is None:
        return
    if branch.id not in (from_branch_id, to_branch_id):
        raise Http404


def setup_branch_field(form, tenant, branch, field_name='branch'):
    """
    يهيئ حقل branch في فورم عميل/مورد/مندوب (أو أي نموذج مشابه له حقل branch
    مباشر) حسب سياق الطلب — العملاء والموردون والمناديب يجب أن يتبعوا فرعاً
    محدداً دائماً (لا سجلات "مركزية" مشتركة بين كل الفروع)، خلافاً للمخازن
    (راجع apps/stocks/forms.py) التي يبقى اختيار فرعها اختيارياً للمستخدم
    المركزي:

    - مستخدم مربوط بفرع (branch غير فارغ): يُحذف الحقل من الفورم كلياً — الفرع
      يُختم تلقائياً في الـ view بدون اختيار يدوي.
    - مستخدم مركزي (branch=None) على tenant من نسخة multi_branch: الحقل يبقى
      لكن يصبح إلزامياً.
    - غير ذلك (نسخة single_store/multi_stock بلا فروع أصلاً): يُحذف الحقل.
    """
    if field_name not in form.fields:
        return
    if branch is not None:
        del form.fields[field_name]
    elif tenant is not None and getattr(tenant, 'version_type', None) == 'multi_branch':
        from apps.core.models import Branch
        form.fields[field_name].queryset = Branch.objects.filter(tenant=tenant, is_active=True)
        form.fields[field_name].required = True
        form.fields[field_name].empty_label = 'اختر الفرع'
    else:
        del form.fields[field_name]

class BranchLabelMixin:
    """
    للتقارير: عند عرض مدير النشاط (مستخدم مركزي في نسخة المؤسسات) لتقرير إجمالي
    كل الفروع، يُلحق اسم الفرع باسم العميل/المورد/المخزن/الخزينة... ليعرف أي فرع
    يتبع كل اسم. تقرير فرع واحد أو نسخ بلا فروع: الاسم كما هو.
    تتطلب self.tenant و self.branch في الكلاس.
    """

    @property
    def show_branch(self):
        tenant = getattr(self, 'tenant', None)
        return getattr(self, 'branch', None) is None and bool(tenant and tenant.is_enterprise())

    def _branch_name(self, branch_id):
        names = getattr(self, '_branch_names_cache', None)
        if names is None:
            from apps.core.models import Branch
            names = dict(Branch.objects.filter(tenant=self.tenant).values_list('id', 'name'))
            self._branch_names_cache = names
        return names.get(branch_id, '')

    def _party(self, obj, fallback='—'):
        """اسم الكيان (عميل/مورد/مخزن/خزينة/حساب...) مع فرعه عند الحاجة."""
        if obj is None:
            return fallback
        return self._labeled(getattr(obj, 'name', None) or str(obj), obj)

    def _labeled(self, text, obj):
        if not self.show_branch or obj is None:
            return text
        bid = getattr(obj, 'branch_id', None)
        if bid:
            name = self._branch_name(bid)
            return f'{text} ({name})' if name else text
        if getattr(obj, 'is_head_office', False):
            return f'{text} (الإدارة المركزية)'
        return text


def is_central_request(request):
    """True إذا نفّذ require_scoped_permission نطاق الإدارة المركزية لهذا الطلب."""
    return bool(getattr(request, 'central_scope', False))


def scope_by_central_stock(qs, request, field='stock'):
    """
    يفصل بيانات المخزن المركزي عن مخازن الفروع في النمط الهجين:
      - نطاق الإدارة المركزية: المخزن المركزي فقط.
      - غيره: يُستبعد المخزن المركزي دائماً (حتى لمستخدم مركزي بصلاحيات فروع).
    field هو مسار علاقة المخزن (مثال: 'stock' أو 'invoice__stock').
    """
    if is_central_request(request):
        return qs.filter(**{f'{field}__is_central': True})
    return qs.exclude(**{f'{field}__is_central': True})


def scoped_suppliers(request, tenant):
    """
    الموردون ضمن نطاق الطلب: نطاق الإدارة المركزية = الموردون المركزيون (بلا فرع)؛
    غيره = موردو الفرع/المعتاد، مع استبعاد المركزيين في النمط الهجين.
    """
    from apps.suppliers.models import Supplier
    if is_central_request(request):
        return Supplier.unscoped.filter(tenant=tenant, branch__isnull=True)
    qs = Supplier.objects.for_tenant(tenant).for_branch(getattr(request, 'branch', None))
    if tenant.is_hybrid_purchasing() and getattr(request, 'branch', None) is None:
        qs = qs.exclude(branch__isnull=True)
    return qs


def scoped_money_accounts(qs, request):
    """
    الخزائن/الحسابات البنكية للعمليات اليومية ضمن نطاق الطلب: نطاق الإدارة المركزية
    = حسابات الإدارة المركزية فقط؛ غيره = operational_money_accounts المعتادة.
    """
    if is_central_request(request):
        return qs.filter(is_head_office=True)
    return operational_money_accounts(qs, request)
