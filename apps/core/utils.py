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
    branch=None لا يفلتر شيء. السجلات المرتبطة بمخزن بدون فرع تظل ظاهرة.
    """
    if branch is None:
        return qs
    from django.db.models import Q
    return qs.filter(Q(**{field: branch}) | Q(**{f'{field}__isnull': True}))


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
    if requested_id:
        branch = branches_for_filter.filter(pk=requested_id).first()
        if branch:
            return branch, True, branches_for_filter
    return None, True, branches_for_filter


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
    - obj بلا فرع محدد على طول المسار (NULL تاريخي): يبقى ظاهراً — نفس
      فلسفة for_branch() تماماً (لا نُخفي بيانات قديمة بأثر رجعي).
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

    if any(r is None for r in resolved):
        return

    if branch not in resolved:
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