"""
Platform-owner tooling for building/curating the shared MasterDrug catalog.
Gated the same way apps/core/views.py::admin_dashboard gates its platform-
admin area — this is NOT tenant-scoped, so it does not use the tenant
permission decorators the rest of the app uses.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .ingest import create_import_batch, process_batch_chunk, commit_batch, fix_missing_generic_names
from .matching import normalize_text
from .models import MasterCategory, MasterDrug, MasterDrugAlias, CatalogImportBatch, CatalogImportRow


def _is_platform_staff(user):
    return user.is_superuser or getattr(user, 'is_platform_staff', False)


def _require_platform_staff(view_func):
    @login_required
    def wrapper(request, *args, **kwargs):
        if not _is_platform_staff(request.user):
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'message': 'ليس لديك صلاحية'}, status=403)
            return render(request, 'core/no_permission.html', status=403)
        return view_func(request, *args, **kwargs)
    return wrapper


# ============================================================
# Import pipeline: upload → chunked extraction → review → commit
# ============================================================

@_require_platform_staff
def catalog_import_upload(request):
    if request.method == 'POST':
        uploaded = request.FILES.get('file')
        if not uploaded:
            return JsonResponse({'success': False, 'message': 'لم يتم رفع أي ملف'}, status=400)
        try:
            batch = create_import_batch(uploaded, request.user, source_label=request.POST.get('source_label', ''))
        except ValueError as exc:
            return JsonResponse({'success': False, 'message': str(exc)}, status=400)
        return JsonResponse({'success': True, 'batch_id': batch.id})

    recent_batches = CatalogImportBatch.objects.order_by('-uploaded_at')[:20]
    return render(request, 'catalog/import_upload.html', {'recent_batches': recent_batches})


@_require_platform_staff
def catalog_import_progress(request, batch_id):
    batch = get_object_or_404(CatalogImportBatch, pk=batch_id)
    return render(request, 'catalog/import_progress.html', {'batch': batch})


@_require_platform_staff
@require_POST
def catalog_import_process_chunk_api(request, batch_id):
    batch = get_object_or_404(CatalogImportBatch, pk=batch_id)
    result = process_batch_chunk(batch)
    return JsonResponse({'success': True, **result})


@_require_platform_staff
def catalog_import_review(request, batch_id):
    batch = get_object_or_404(CatalogImportBatch, pk=batch_id)
    rows = batch.rows.exclude(match_status='committed').exclude(match_status='rejected').select_related('matched_master_drug').order_by('row_number')
    status_order = {'needs_review': 0, 'duplicate_fuzzy': 1, 'duplicate_exact': 2, 'new': 3, 'pending': 4}
    rows = sorted(rows, key=lambda r: status_order.get(r.match_status, 9))
    return render(request, 'catalog/import_review.html', {'batch': batch, 'rows': rows})


@_require_platform_staff
@require_POST
def catalog_import_commit_api(request, batch_id):
    batch = get_object_or_404(CatalogImportBatch, pk=batch_id)
    try:
        decisions = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'success': False, 'message': 'بيانات غير صالحة'}, status=400)

    result = commit_batch(batch, request.user, decisions)
    return JsonResponse({'success': True, **result})


@_require_platform_staff
def master_drug_search_api(request):
    """Used by the review screen's "link to existing drug" picker."""
    q = normalize_text(request.GET.get('q', ''))
    qs = MasterDrug.objects.filter(status='active')
    if q:
        qs = qs.filter(generic_name_normalized__icontains=q)
    data = [
        {'id': d.id, 'label': str(d)}
        for d in qs.order_by('generic_name')[:20]
    ]
    return JsonResponse({'results': data})


# ============================================================
# Browse / curate the committed catalog
# ============================================================

@_require_platform_staff
@require_POST
def catalog_fix_missing_generic_names_api(request):
    result = fix_missing_generic_names()
    return JsonResponse({'success': True, **result})


@_require_platform_staff
def master_drug_list(request):
    q = request.GET.get('q', '').strip()
    qs = MasterDrug.objects.filter(status='active').select_related('category')
    if q:
        qs = qs.filter(generic_name_normalized__icontains=normalize_text(q))
    qs = qs.order_by('generic_name')[:200]
    missing_generic_count = MasterDrug.objects.filter(status='active', generic_name='').count()
    return render(request, 'catalog/master_drug_list.html', {
        'drugs': qs, 'q': q, 'missing_generic_count': missing_generic_count,
    })


@_require_platform_staff
def master_drug_detail(request, pk):
    drug = get_object_or_404(MasterDrug, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'merge_into':
            target_id = request.POST.get('target_id')
            target = get_object_or_404(MasterDrug, pk=target_id)
            if target.pk != drug.pk:
                drug.status = 'merged'
                drug.merged_into = target
                drug.save(update_fields=['status', 'merged_into'])
                drug.aliases.update(master_drug=target)
        elif action == 'update':
            drug.generic_name = request.POST.get('generic_name', '').strip() or drug.generic_name
            drug.dosage_form = request.POST.get('dosage_form', '').strip()
            drug.strength = request.POST.get('strength', '').strip()
            drug.item_type = request.POST.get('item_type', '').strip() or 'product'
            drug.description = request.POST.get('description', '').strip()
            drug.default_unit_name = request.POST.get('default_unit_name', '').strip()
            drug.requires_prescription = bool(request.POST.get('requires_prescription'))
            drug.is_controlled_substance = bool(request.POST.get('is_controlled_substance'))
            drug.is_insurance_excluded = bool(request.POST.get('is_insurance_excluded'))
            drug.track_expiry = bool(request.POST.get('track_expiry'))
            drug.track_batch = bool(request.POST.get('track_batch'))
            drug.track_serial = bool(request.POST.get('track_serial'))
            if request.FILES.get('image'):
                drug.image = request.FILES['image']
            category_name = request.POST.get('category', '').strip()
            if category_name:
                drug.category, _ = MasterCategory.objects.get_or_create(name=category_name)
            else:
                drug.category = None
            drug.updated_by = request.user
            drug.save()
        elif action == 'add_alias':
            trade_name = request.POST.get('trade_name', '').strip()
            if trade_name:
                MasterDrugAlias.objects.create(
                    master_drug=drug, trade_name=trade_name,
                    manufacturer=request.POST.get('manufacturer', '').strip(),
                    barcode=request.POST.get('barcode', '').strip(),
                    is_primary=not drug.aliases.exists(),
                    created_by=request.user,
                )
        return redirect('catalog:master_drug_detail', pk=drug.pk)

    from apps.items.models import Item
    return render(request, 'catalog/master_drug_detail.html', {
        'drug': drug,
        'aliases': drug.aliases.all(),
        'tenant_items_count': drug.tenant_items.count(),
        'item_type_choices': Item.ITEM_TYPE_CHOICES,
    })


@_require_platform_staff
def master_drug_create(request):
    if request.method == 'POST':
        generic_name = request.POST.get('generic_name', '').strip()
        if not generic_name:
            return render(request, 'catalog/master_drug_create.html', {'error': 'الاسم العلمي مطلوب'})
        category_name = request.POST.get('category', '').strip()
        category = MasterCategory.objects.get_or_create(name=category_name)[0] if category_name else None
        drug = MasterDrug.objects.create(
            generic_name=generic_name,
            dosage_form=request.POST.get('dosage_form', '').strip(),
            strength=request.POST.get('strength', '').strip(),
            item_type=request.POST.get('item_type', '').strip() or 'product',
            description=request.POST.get('description', '').strip(),
            default_unit_name=request.POST.get('default_unit_name', '').strip(),
            category=category,
            requires_prescription=bool(request.POST.get('requires_prescription')),
            is_controlled_substance=bool(request.POST.get('is_controlled_substance')),
            track_expiry=bool(request.POST.get('track_expiry')),
            track_batch=bool(request.POST.get('track_batch')),
            track_serial=bool(request.POST.get('track_serial')),
            image=request.FILES.get('image'),
            created_by=request.user, updated_by=request.user,
        )
        trade_name = request.POST.get('trade_name', '').strip()
        if trade_name:
            MasterDrugAlias.objects.create(
                master_drug=drug, trade_name=trade_name,
                manufacturer=request.POST.get('manufacturer', '').strip(),
                barcode=request.POST.get('barcode', '').strip(),
                is_primary=True, created_by=request.user,
            )
        return redirect('catalog:master_drug_detail', pk=drug.pk)

    return render(request, 'catalog/master_drug_create.html', {})


@_require_platform_staff
@require_POST
def master_drug_delete_api(request, pk):
    drug = get_object_or_404(MasterDrug, pk=pk)
    name = drug.generic_name
    drug.delete()  # aliases CASCADE; tenant Item.master_drug is SET_NULL — no tenant product data is touched
    return JsonResponse({'success': True, 'message': f'تم حذف {name} من الكتالوج'})


@_require_platform_staff
@require_POST
def master_drug_alias_delete_api(request, pk, alias_id):
    alias = get_object_or_404(MasterDrugAlias, pk=alias_id, master_drug_id=pk)
    name = alias.trade_name
    alias.delete()
    return JsonResponse({'success': True, 'message': f'تم حذف {name}'})


@_require_platform_staff
def catalog_clear_all(request):
    """Wipes the ENTIRE shared catalog (all MasterDrug/aliases/import batches).
    Deliberately requires typing an exact confirmation phrase server-side —
    this is irreversible and affects every tenant's computed alternatives."""
    CONFIRM_PHRASE = 'حذف الكتالوج نهائياً'
    if request.method == 'POST':
        if request.POST.get('confirm_phrase', '').strip() != CONFIRM_PHRASE:
            return render(request, 'catalog/clear_all.html', {
                'confirm_phrase': CONFIRM_PHRASE,
                'error': 'العبارة غير مطابقة — لم يتم حذف أي شيء',
            })
        MasterDrug.objects.all().delete()
        CatalogImportBatch.objects.all().delete()
        return redirect('catalog:master_drug_list')

    return render(request, 'catalog/clear_all.html', {'confirm_phrase': CONFIRM_PHRASE})
