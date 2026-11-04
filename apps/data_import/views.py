import io
import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from functools import wraps

from apps.accounts.decorators import (
    _deny,
    require_permission, require_any_permission, require_capability, require_plan_feature,
)
from apps.core.io_utils import xlsx_response
from apps.items.models import Category
from apps.stocks.models import Stock
from apps.stocks.scope import opening_balance_stocks

from .gating import products_import_blocked_reason, BLOCKED_MESSAGES
from .schemas import get_product_schema, get_customer_schema, get_supplier_schema
from .xlsx_builder import build_import_template
from .product_importer import import_products, run_import, build_errors_workbook
from . import smart_reader
from apps.core.market_rates import exchange_rate_alert, can_update_exchange_rate
from .simple_importer import import_simple_entities


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


@login_required
@require_any_permission('add_categories', 'import_items', 'import_customers', 'import_suppliers', 'import_agents',
                        'add_central_suppliers')
def hub(request):
    user = request.user
    can_all = user.is_superuser
    tenant = _ensure_tenant(request)
    flags = {
        # التصنيفات تُدار من الإدارة فقط (مثل قائمة التصنيفات في القائمة الجانبية)
        'can_import_categories': (can_all or user.has_perm_key('add_categories')) and not getattr(request, 'branch', None),
        'can_import_items': can_all or (user.has_perm_key('import_items') and user.has_perm_key('add_items')),
        'can_import_customers': can_all or (user.has_perm_key('import_customers') and user.has_perm_key('add_customers')),
        'can_import_suppliers': can_all or _can_import_suppliers(request),
        'suppliers_are_central': not can_all and not _regular_supplier_import(user) and _central_suppliers_import(request),
        'can_import_agents': _agents_enabled(tenant, user) and (can_all or (user.has_perm_key('import_agents') and user.has_perm_key('add_agents'))),
    }
    flags['has_any'] = any(flags.values())
    return render(request, 'data_import/hub.html', flags)


def _regular_supplier_import(user):
    return user.has_perm_key('import_suppliers') and user.has_perm_key('add_suppliers')


def _central_suppliers_import(request):
    """النمط الهجين: مدير النشاط (بلا فرع) يستورد «الموردين المركزيين» بصلاحية إضافتهم، إذ لا يملك صلاحيات موردي الفروع."""
    tenant = _ensure_tenant(request)
    return bool(tenant and not getattr(request, 'branch', None) and tenant.is_hybrid_purchasing()
                and request.user.has_perm_key('add_central_suppliers'))


def _can_import_suppliers(request):
    return _regular_supplier_import(request.user) or _central_suppliers_import(request)


def supplier_import_access(view_func):
    """استيراد الموردين: (import_suppliers + add_suppliers) أو، في الهجين لمدير النشاط، add_central_suppliers."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('accounts:login')
        if request.user.is_superuser or _can_import_suppliers(request):
            return view_func(request, *args, **kwargs)
        return _deny(request)
    return wrapper


def _agents_enabled(tenant, user):
    """وحدة المناديب متاحة للمشترك (قدرة النسخة + ميزة الباقة)؛ السوبريوزر يتجاوزها."""
    if user.is_superuser:
        return True
    caps = getattr(tenant, 'capabilities', None)
    return bool(tenant and tenant.plan_allows('agents') and getattr(caps, 'has_agents_module', False))


@login_required
@require_permission('import_items')
@require_permission('add_items')
def product_import_page(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    blocked_reason = products_import_blocked_reason(tenant)
    from apps.core.utils import currency_symbol
    context = {
        'blocked_reason': blocked_reason,
        'blocked_message': BLOCKED_MESSAGES.get(blocked_reason),
        'hc_mode': tenant.hard_currency_mode,
        'local_currency_symbol': currency_symbol(tenant.currency or 'SDG'),
        'hc_currency_symbol': currency_symbol(tenant.hard_currency or 'USD'),
        'xr_alert': exchange_rate_alert(tenant),
        'xr_can_update': can_update_exchange_rate(request),
        'xr_hint': 'الأسعار في الملف تُقرأ بالعملة المحلية ويُحسب ما يعادلها بالعملة الصعبة بهذا السعر.',
    }
    return render(request, 'data_import/product_import.html', context)


def _currency_instructions(tenant):
    """سطور تعليمات الأسعار: في وضع العملة الصعبة تُكتب الأسعار بالجنيه ويحسب النظام ما يعادلها."""
    from apps.core.utils import currency_symbol, CURRENCY_NAMES_AR
    local = tenant.currency or 'SDG'
    lines = [f'اكتب الأسعار بـ{CURRENCY_NAMES_AR.get(local, local)} ({currency_symbol(local)}) كما في عناوين الأعمدة.']
    if tenant.hard_currency_mode:
        hc = tenant.hard_currency or 'USD'
        lines.append(
            f'وضع العملة الصعبة مفعّل: عند الاستيراد يحسب النظام ما يعادل كل سعر بـ{CURRENCY_NAMES_AR.get(hc, hc)} '
            f'بسعر الصرف المعتمد وقت الاستيراد (حالياً 1 {currency_symbol(hc)} = {tenant.exchange_rate:,.2f} '
            f'{currency_symbol(local)}) — حدّث سعر الصرف قبل الاستيراد إن تغيّر.'
        )
    return lines


def _tenant_lists(tenant, schema, branch=None):
    lists = {}
    sources = {s.get('tenant_source') for s in schema if s['dtype'] == 'choice_tenant'}
    if 'category' in sources:
        lists['category'] = list(
            Category.objects.for_tenant(tenant).filter(is_active=True).order_by('name').values_list('name', flat=True)
        )
    if 'stock' in sources:
        lists['stock'] = list(
            opening_balance_stocks(tenant, branch).order_by('name').values_list('name', flat=True)
        )
    return lists


@login_required
@require_permission('import_items')
@require_permission('add_items')
def product_template_download(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if products_import_blocked_reason(tenant):
        return redirect('data_import:product_page')

    schema = get_product_schema(tenant)
    tenant_lists = _tenant_lists(tenant, schema, request.user.branch if request.user.branch_id else None)

    instructions = [
        'إذا كان للمنتج وحدة واحدة فقط، عبّئ "اسم الوحدة الأساسية" واترك عمودي الوحدة الأكبر فارغين.',
        'إذا كان له وحدتان (مثال: حبة وكرتون)، عبّئ الوحدة الأساسية (الأصغر) ثم اسم الوحدة الأكبر وكم وحدة أساسية بداخلها.',
    ]
    instructions += _currency_instructions(tenant)
    wb = build_import_template(schema, tenant_lists, sheet_title='المنتجات', instructions=instructions)

    buffer = io.BytesIO()
    wb.save(buffer)
    response = xlsx_response('قالب_استيراد_المنتجات.xlsx')
    response.write(buffer.getvalue())
    return response


MAX_IMPORT_FILE_BYTES = 15 * 1024 * 1024


def _import_options(request):
    try:
        options = json.loads(request.POST.get('options') or '{}')
    except ValueError:
        options = {}
    return options if isinstance(options, dict) else {}


def _stashed_file(request, tenant):
    """(data, meta, token) من ملف سبق رفعه في المعاينة، أو None."""
    token = request.POST.get('token') or request.GET.get('token') or ''
    loaded = smart_reader.stash_load(tenant.id, request.user.id, token)
    if not loaded:
        return None
    data, meta = loaded
    return data, meta, token


@login_required
@require_permission('import_items')
@require_permission('add_items')
def product_import_analyze(request):
    """
    المعاينة الذكية: يرفع المستخدم أي ملف (أو يعيد التحليل بخيارات جديدة لنفس الملف عبر token) فيرجع
    ما فهمه النظام — الورقة وصف العناوين وتعيين الأعمدة والمشكلات والقرارات المطلوبة — دون حفظ أي منتج.
    """
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    blocked_reason = products_import_blocked_reason(tenant)
    if blocked_reason:
        return JsonResponse({'success': False, 'message': BLOCKED_MESSAGES[blocked_reason]}, status=400)

    upload = request.FILES.get('file')
    if upload:
        if upload.size > MAX_IMPORT_FILE_BYTES:
            return JsonResponse({'success': False, 'message': 'حجم الملف أكبر من 15 ميجابايت. قسّمه إلى ملفين.'}, status=400)
        data, filename = upload.read(), upload.name
        token = smart_reader.stash_save(tenant.id, request.user.id, data, filename)
        meta = {'filename': filename}
    else:
        stashed = _stashed_file(request, tenant)
        if not stashed:
            return JsonResponse({'success': False, 'message': 'انتهت صلاحية الملف المرفوع. ارفعه مرة أخرى.',
                                 'expired': True}, status=400)
        data, meta, token = stashed
        filename = meta.get('filename', 'file.xlsx')

    analysis, _ = run_import(tenant, request.user, data, filename, _import_options(request), meta, analyze_only=True)
    smart_reader.stash_update_meta(tenant.id, request.user.id, token, meta)
    if 'fatal' in analysis:
        return JsonResponse({'success': False, 'message': analysis['fatal']}, status=400)
    return JsonResponse({'success': True, 'token': token, 'analysis': analysis,
                         'exchange_alert': bool(exchange_rate_alert(tenant))})


@login_required
@require_permission('import_items')
@require_permission('add_items')
def product_suggest_categories(request):
    """اقتراح تصنيف بالذكاء الاصطناعي لمنتجات بلا تصنيف (دفعات صغيرة يرسلها المتصفح تباعاً)."""
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        payload = json.loads(request.body or '{}')
    except ValueError:
        payload = {}
    items = [i for i in (payload.get('items') or []) if isinstance(i, dict) and i.get('name')][:80]
    names = list(Category.objects.for_tenant(tenant).filter(is_active=True).values_list('name', flat=True))
    names += [str(n) for n in (payload.get('extra_categories') or []) if n][:100]
    if not items or not names:
        return JsonResponse({'success': True, 'suggestions': {}})
    from apps.ai.services import suggest_product_categories
    suggestions = suggest_product_categories([{'key': i.get('row'), 'name': i['name']} for i in items], names)
    if not suggestions:
        from django.conf import settings
        if not settings.DEEPSEEK_API_KEY:
            return JsonResponse({'success': False, 'message': 'خدمة الذكاء الاصطناعي غير مفعّلة على الخادم.'})
    return JsonResponse({'success': True, 'suggestions': suggestions})


@login_required
@require_permission('import_items')
@require_permission('add_items')
def product_import_errors_file(request):
    """تنزيل الصفوف التي لم تُستورد (بنفس أعمدة ملف العميل + السبب) لتصحيحها وإعادة رفعها."""
    tenant = _ensure_tenant(request)
    if not tenant:
        raise Http404
    content = smart_reader.stash_read_file(tenant.id, request.user.id, request.GET.get('token', ''), 'errors.xlsx')
    if content is None:
        raise Http404
    response = xlsx_response('صفوف_لم_تستورد.xlsx')
    response.write(content)
    return response


@login_required
@require_permission('import_items')
@require_permission('add_items')
def product_import_commit(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    blocked_reason = products_import_blocked_reason(tenant)
    if blocked_reason:
        return JsonResponse({
            'success': False,
            'message': BLOCKED_MESSAGES[blocked_reason],
            'blocked_reason': blocked_reason,
        }, status=400)

    stashed = None if 'file' in request.FILES else _stashed_file(request, tenant)
    if 'file' not in request.FILES and not stashed:
        if request.POST.get('token'):
            return JsonResponse({'success': False, 'message': 'انتهت صلاحية الملف المرفوع. ارفعه مرة أخرى.',
                                 'expired': True}, status=400)
        return JsonResponse({'success': False, 'message': 'لم يتم رفع أي ملف'}, status=400)

    errors_url = ''
    if stashed:
        data, meta, token = stashed
        result, plan = run_import(tenant, request.user, data, meta.get('filename', 'file.xlsx'),
                                  _import_options(request), meta)
        if 'fatal' in result:
            return JsonResponse({'success': False, 'message': result['fatal']}, status=400)
        if result['errors'] and plan:
            smart_reader.stash_write_file(tenant.id, request.user.id, token, 'errors.xlsx',
                                          build_errors_workbook(plan, result))
            errors_url = reverse('data_import:product_errors_file') + f'?token={token}'
    else:
        result = import_products(tenant, request.FILES['file'], request.user)

    failed = len({e['row'] for e in result['errors']})
    msg = f"تم استيراد {result['created']} منتج جديد"
    if result.get('updated'):
        msg += f"، وتحديث {result['updated']} منتج موجود مسبقاً"
    if result.get('merged'):
        msg += f"، ودمج {result['merged']} صف مع صف آخر في نفس الملف (راجع التنبيهات)"
    if result.get('categories_created'):
        msg += f"، وإنشاء {result['categories_created']} تصنيف جديد"
    if failed:
        msg += f". {failed} صف لم يُستورد بسبب أخطاء موضحة أدناه"
    if result.get('possible_duplicates'):
        msg += f". {len(result['possible_duplicates'])} صف يشبه منتجاً موجوداً — يُنصح بمراجعتها"

    return JsonResponse({
        'success': True,
        'message': msg,
        'created': result['created'],
        'updated': result.get('updated', 0),
        'merged': result.get('merged', 0),
        'categories_created': result.get('categories_created', 0),
        'skipped': result.get('skipped', 0),
        'errors': result['errors'],
        'warnings': result.get('warnings', [])[:500],
        'possible_duplicates': result.get('possible_duplicates', []),
        'errors_url': errors_url,
    })


# ============================================================
# Customers & Suppliers — simple entities, no gating, shared helpers
# ============================================================

def _simple_template_download(tenant, schema_fn, sheet_title, filename):
    schema = schema_fn(tenant)
    wb = build_import_template(schema, tenant_lists={}, sheet_title=sheet_title)
    buffer = io.BytesIO()
    wb.save(buffer)
    response = xlsx_response(filename)
    response.write(buffer.getvalue())
    return response


def _simple_import_commit(request, tenant, schema_fn, model, entity_label):
    if 'file' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'لم يتم رفع أي ملف'}, status=400)

    schema = schema_fn(tenant)
    result = import_simple_entities(tenant, request.FILES['file'], request.user, model, schema, entity_label)

    msg = f"تم استيراد {result['created']} {entity_label} بنجاح"
    if result['errors']:
        msg += f". {len(result['errors'])} صف به أخطاء"

    return JsonResponse({
        'success': True,
        'message': msg,
        'created': result['created'],
        'errors': result['errors'],
    })


@login_required
@require_permission('import_customers')
@require_permission('add_customers')
def customer_import_page(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    return render(request, 'data_import/simple_import.html', {
        'title': 'استيراد العملاء',
        'icon': 'fa-users',
        'template_url': 'data_import:customer_template',
        'commit_url': 'data_import:customer_commit',
        'hub_url': 'data_import:hub',
    })


@login_required
@require_permission('import_customers')
@require_permission('add_customers')
def customer_template_download(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    return _simple_template_download(tenant, get_customer_schema, 'العملاء', 'قالب_استيراد_العملاء.xlsx')


@login_required
@require_permission('import_customers')
@require_permission('add_customers')
def customer_import_commit(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    from apps.customers.models import Customer
    return _simple_import_commit(request, tenant, get_customer_schema, Customer, 'عميل')


@login_required
@supplier_import_access
def supplier_import_page(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    central = not request.user.is_superuser and not _regular_supplier_import(request.user)
    return render(request, 'data_import/simple_import.html', {
        'title': 'استيراد الموردين المركزيين' if central else 'استيراد الموردين',
        'icon': 'fa-truck',
        'template_url': 'data_import:supplier_template',
        'commit_url': 'data_import:supplier_commit',
        'hub_url': 'data_import:hub',
    })


@login_required
@supplier_import_access
def supplier_template_download(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    return _simple_template_download(tenant, get_supplier_schema, 'الموردين', 'قالب_استيراد_الموردين.xlsx')


@login_required
@supplier_import_access
def supplier_import_commit(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    from apps.suppliers.models import Supplier
    return _simple_import_commit(request, tenant, get_supplier_schema, Supplier, 'مورد')


# ============================================================
# Categories & Agents — صفحات الاستيراد الموحّدة؛ المنطق والقوالب في
# items.category_import_api / agents.import_api (استجابة JSON تفهمها simple_import.html)
# ============================================================

@login_required
@require_permission('add_categories')
def category_import_page(request):
    if not _ensure_tenant(request):
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if getattr(request, 'branch', None):
        return redirect('data_import:hub')
    return render(request, 'data_import/simple_import.html', {
        'title': 'استيراد التصنيفات',
        'icon': 'fa-tags',
        'template_url': 'items:category_template_api',
        'commit_url': 'items:category_import_api',
        'hub_url': 'data_import:hub',
    })


@login_required
@require_capability('has_agents_module')
@require_plan_feature('agents')
@require_permission('import_agents')
@require_permission('add_agents')
def agent_import_page(request):
    if not _ensure_tenant(request):
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    return render(request, 'data_import/simple_import.html', {
        'title': 'استيراد المناديب',
        'icon': 'fa-id-badge',
        'template_url': 'agents:download_template',
        'commit_url': 'agents:import_api',
        'hub_url': 'data_import:hub',
    })
