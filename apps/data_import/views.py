import io

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render

from apps.accounts.decorators import require_permission, require_any_permission
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
@require_any_permission('import_items', 'import_customers', 'import_suppliers')
def hub(request):
    user = request.user
    can_all = user.is_superuser
    return render(request, 'data_import/hub.html', {
        'can_import_items': can_all or user.has_perm_key('import_items'),
        'can_import_customers': can_all or user.has_perm_key('import_customers'),
        'can_import_suppliers': can_all or user.has_perm_key('import_suppliers'),
    })


@login_required
@require_permission('import_items')
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


def _tenant_lists(tenant, schema):
    lists = {}
    sources = {s.get('tenant_source') for s in schema if s['dtype'] == 'choice_tenant'}
    if 'category' in sources:
        lists['category'] = list(
            Category.objects.for_tenant(tenant).filter(is_active=True).order_by('name').values_list('name', flat=True)
        )
    if 'stock' in sources:
        lists['stock'] = list(
            Stock.objects.for_tenant(tenant).filter(is_active=True).order_by('name').values_list('name', flat=True)
        )
    return lists


@login_required
@require_permission('import_items')
def product_template_download(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if products_import_blocked_reason(tenant):
        return redirect('data_import:product_page')

    schema = get_product_schema(tenant)
    tenant_lists = _tenant_lists(tenant, schema)

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
def customer_template_download(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    return _simple_template_download(tenant, get_customer_schema, 'العملاء', 'قالب_استيراد_العملاء.xlsx')


@login_required
@require_permission('import_customers')
def customer_import_commit(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    from apps.customers.models import Customer
    return _simple_import_commit(request, tenant, get_customer_schema, Customer, 'عميل')


@login_required
@require_permission('import_suppliers')
def supplier_import_page(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    return render(request, 'data_import/simple_import.html', {
        'title': 'استيراد الموردين',
        'icon': 'fa-truck',
        'template_url': 'data_import:supplier_template',
        'commit_url': 'data_import:supplier_commit',
        'hub_url': 'data_import:hub',
    })


@login_required
@require_permission('import_suppliers')
def supplier_template_download(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    return _simple_template_download(tenant, get_supplier_schema, 'الموردين', 'قالب_استيراد_الموردين.xlsx')


@login_required
@require_permission('import_suppliers')
def supplier_import_commit(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    from apps.suppliers.models import Supplier
    return _simple_import_commit(request, tenant, get_supplier_schema, Supplier, 'مورد')
