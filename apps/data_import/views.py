import io

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render

from apps.accounts.decorators import require_permission
from apps.core.io_utils import xlsx_response
from apps.items.models import Category
from apps.stocks.models import Stock

from .gating import products_import_blocked_reason, BLOCKED_MESSAGES
from .schemas import get_product_schema
from .xlsx_builder import build_import_template
from .product_importer import import_products


def _ensure_tenant(request):
    return getattr(request, 'tenant', None)


@login_required
@require_permission('import_items')
def hub(request):
    return render(request, 'data_import/hub.html')


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
    if result['errors']:
        msg += f". {len(result['errors'])} صف به أخطاء"

    return JsonResponse({
        'success': True,
        'message': msg,
        'created': result['created'],
        'errors': result['errors'],
    })
