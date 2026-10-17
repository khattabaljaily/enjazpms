import io

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render

from functools import wraps

from apps.accounts.decorators import (
    _deny,
    require_permission, require_any_permission, require_capability, require_plan_feature,
)
from apps.core.io_utils import xlsx_response
from apps.items.models import Category
from apps.stocks.models import Stock

from .gating import products_import_blocked_reason, BLOCKED_MESSAGES
from .schemas import get_product_schema, get_customer_schema, get_supplier_schema
from .xlsx_builder import build_import_template
from .product_importer import import_products
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
    context = {
        'blocked_reason': blocked_reason,
        'blocked_message': BLOCKED_MESSAGES.get(blocked_reason),
    }
    return render(request, 'data_import/product_import.html', context)


def _tenant_lists(tenant, schema, branch=None):
    lists = {}
    sources = {s.get('tenant_source') for s in schema if s['dtype'] == 'choice_tenant'}
    if 'category' in sources:
        lists['category'] = list(
            Category.objects.for_tenant(tenant).filter(is_active=True).order_by('name').values_list('name', flat=True)
        )
    if 'stock' in sources:
        lists['stock'] = list(
            Stock.objects.for_tenant(tenant).for_branch(branch).filter(is_active=True).order_by('name').values_list('name', flat=True)
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
    wb = build_import_template(schema, tenant_lists, sheet_title='المنتجات', instructions=instructions)

    buffer = io.BytesIO()
    wb.save(buffer)
    response = xlsx_response('قالب_استيراد_المنتجات.xlsx')
    response.write(buffer.getvalue())
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

    if 'file' not in request.FILES:
        return JsonResponse({'success': False, 'message': 'لم يتم رفع أي ملف'}, status=400)

    result = import_products(tenant, request.FILES['file'], request.user)

    msg = f"تم استيراد {result['created']} منتج بنجاح"
    if result.get('updated'):
        msg += f"، وتحديث {result['updated']} منتج موجود مسبقاً"
    if result['errors']:
        msg += f". {len(result['errors'])} صف به أخطاء"
    if result.get('possible_duplicates'):
        msg += f". {len(result['possible_duplicates'])} صف يشبه منتجاً موجوداً — يُنصح بمراجعتها"

    return JsonResponse({
        'success': True,
        'message': msg,
        'created': result['created'],
        'updated': result.get('updated', 0),
        'errors': result['errors'],
        'possible_duplicates': result.get('possible_duplicates', []),
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
