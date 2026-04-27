from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import CustomerForm
from .models import Customer


def _ensure_tenant(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return None
    return tenant


@login_required
def customer_list(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return redirect('core:no_tenant')

    qs = Customer.objects.for_tenant(tenant)
    total = qs.count()
    active = qs.filter(is_active=True).count()
    inactive = total - active

    context = {
        'form': CustomerForm(),
        'stats': {
            'total': total,
            'active': active,
            'inactive': inactive,
        },
    }
    return render(request, 'customers/customer_list.html', context)


def _serialize_form_errors(form):
    return {field: [str(error) for error in errors] for field, errors in form.errors.items()}


@login_required
def customer_table_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    draw = int(request.GET.get('draw', 1))
    start = int(request.GET.get('start', 0))
    length = int(request.GET.get('length', 25))
    search_value = request.GET.get('search[value]', '').strip()
    status = request.GET.get('status', '').strip()

    queryset = Customer.objects.for_tenant(tenant)
    records_total = queryset.count()

    if status == 'active':
        queryset = queryset.filter(is_active=True)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False)

    if search_value:
        queryset = queryset.filter(
            Q(name__icontains=search_value)
            | Q(code__icontains=search_value)
            | Q(phone__icontains=search_value)
            | Q(email__icontains=search_value)
            | Q(city__icontains=search_value)
        )

    records_filtered = queryset.count()

    order_column_index = request.GET.get('order[0][column]', '0')
    order_dir = request.GET.get('order[0][dir]', 'asc')
    order_column_name = request.GET.get(f'columns[{order_column_index}][data]', 'created_at')

    allowed_order_fields = {
        'code': 'code',
        'name': 'name',
        'phone': 'phone',
        'city': 'city',
        'opening_balance': 'opening_balance',
        'is_active': 'is_active',
        'created_at': 'created_at',
    }
    order_field = allowed_order_fields.get(order_column_name, 'created_at')
    if order_dir == 'desc':
        order_field = f'-{order_field}'

    queryset = queryset.order_by(order_field)[start:start + length]

    data = [
        {
            'id': customer.id,
            'code': customer.code,
            'name': customer.name,
            'phone': customer.phone or '-',
            'city': customer.city or '-',
            'opening_balance': str(customer.opening_balance),
            'is_active': customer.is_active,
        }
        for customer in queryset
    ]

    return JsonResponse(
        {
            'draw': draw,
            'recordsTotal': records_total,
            'recordsFiltered': records_filtered,
            'data': data,
        }
    )


@login_required
def customer_create_api(request):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    form = CustomerForm(request.POST)
    if form.is_valid():
        customer = form.save(commit=False)
        customer.tenant = tenant
        customer.created_by = request.user
        customer.updated_by = request.user
        customer.save()
        return JsonResponse({
            'success': True,
            'message': 'تم إضافة العميل بنجاح',
            'id': customer.id,
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
def customer_detail_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)

    return JsonResponse({
        'success': True,
        'data': {
            'id': customer.id,
            'name': customer.name,
            'phone': customer.phone,
            'email': customer.email,
            'city': customer.city,
            'address': customer.address,
            'opening_balance': str(customer.opening_balance),
            'credit_limit': str(customer.credit_limit),
            'notes': customer.notes,
            'is_active': customer.is_active,
        }
    })


@login_required
def customer_update_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)
    form = CustomerForm(request.POST, instance=customer)

    if form.is_valid():
        customer = form.save(commit=False)
        customer.updated_by = request.user
        customer.save()
        return JsonResponse({
            'success': True,
            'message': 'تم تعديل بيانات العميل بنجاح',
        })

    return JsonResponse({
        'success': False,
        'message': 'يرجى التحقق من الحقول المطلوبة',
        'errors': _serialize_form_errors(form),
    }, status=400)


@login_required
def customer_delete_api(request, pk):
    tenant = _ensure_tenant(request)
    if not tenant:
        return JsonResponse({'success': False, 'message': 'لا يوجد نشاط تجاري'}, status=400)

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    customer = get_object_or_404(Customer.objects.for_tenant(tenant), pk=pk)
    customer.delete()
    return JsonResponse({
        'success': True,
        'message': 'تم حذف العميل بنجاح',
    })


@login_required
def customer_create(request):
    return redirect('customers:list')


@login_required
def customer_update(request, pk):
    return redirect('customers:list')


@login_required
def customer_detail(request, pk):
    return redirect('customers:list')


@login_required
def customer_delete(request, pk):
    return redirect('customers:list')
