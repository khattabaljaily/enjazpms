"""
Staging pipeline for building the shared MasterDrug catalog from heterogeneous
supplier price lists. No async task queue exists in this project (see the
plan doc) — process_batch_chunk() is designed to be called repeatedly by an
HTTP endpoint (one call = one chunk), with the browser polling until done.
Nothing here ever writes MasterDrug/MasterDrugAlias directly except
commit_batch(), which only runs after a human has reviewed the staged rows.
"""
from django.db import transaction
from django.utils import timezone

from apps.ai.services import extract_drug_rows_batch, match_drug_to_master
from apps.core.io_utils import parse_uploaded_file

from .file_extraction import is_pdf_or_image, parse_pdf_or_image
from .matching import drug_key, find_exact_candidates, find_fuzzy_candidates, classify_match
from .models import MasterCategory, MasterDrug, MasterDrugAlias, CatalogImportBatch, CatalogImportRow


def create_import_batch(uploaded_file, user, source_label: str = '') -> CatalogImportBatch:
    """
    Parses the uploaded file and stages one CatalogImportRow per source row.
    Most real supplier price lists are PDF or plain photos, not Excel — see
    apps.catalog.file_extraction for that path; apps.core.io_utils handles
    the Excel/CSV case shared with the rest of the app.
    """
    if is_pdf_or_image(uploaded_file.name):
        rows, err = parse_pdf_or_image(uploaded_file)
    else:
        rows, err = parse_uploaded_file(uploaded_file)
    if err:
        raise ValueError(err)
    if not rows:
        raise ValueError('الملف فارغ أو لا يحتوي على بيانات')

    batch = CatalogImportBatch.objects.create(
        original_filename=uploaded_file.name,
        uploaded_by=user,
        source_label=source_label,
        status='pending',
        total_rows=len(rows),
    )
    CatalogImportRow.objects.bulk_create([
        CatalogImportRow(batch=batch, row_number=i, raw_data=row)
        for i, row in enumerate(rows, start=2)
    ])
    return batch


def fix_missing_generic_names(limit: int = 200, batch_size: int = 20) -> dict:
    """
    One-off cleanup for MasterDrug rows that got committed with a blank
    generic_name — this happens when the AI couldn't infer the active
    ingredient from a terse price-list line and the reviewer approved the
    row anyway. Re-runs the same free-text extraction used during import,
    this time against each drug's own trade name, and fills in whatever
    was missing (never overwrites a field the admin already set by hand).
    Rows with no usable trade name at all (extraction totally failed and
    was approved regardless) are deleted rather than "fixed".
    """
    candidates = list(MasterDrug.objects.filter(generic_name='', status='active')[:limit])

    ghosts = []
    fixable = []
    fixable_text = []
    for drug in candidates:
        alias = drug.aliases.filter(is_primary=True).first() or drug.aliases.first()
        trade_name = (alias.trade_name if alias else '').strip()
        if not trade_name:
            ghosts.append(drug)
        else:
            fixable.append(drug)
            fixable_text.append({'text': trade_name})

    if ghosts:
        MasterDrug.objects.filter(id__in=[d.id for d in ghosts]).delete()

    fixed = 0
    still_missing = 0
    if fixable_text:
        extracted_list = extract_drug_rows_batch(fixable_text, batch_size=batch_size)
        for drug, extracted in zip(fixable, extracted_list):
            generic_name = extracted.get('generic_name', '').strip()
            if not generic_name:
                still_missing += 1
                continue
            drug.generic_name = generic_name
            if not drug.dosage_form:
                drug.dosage_form = extracted.get('dosage_form', '').strip()
            if not drug.strength:
                drug.strength = extracted.get('strength', '').strip()
            drug.save()
            fixed += 1

    return {'fixed': fixed, 'deleted_empty': len(ghosts), 'still_missing': still_missing}


def _seen_keys_in_batch(batch: CatalogImportBatch) -> dict:
    """Normalized-key -> row for rows already classified 'new' in this same
    batch, so later chunks can flag likely duplicates WITHIN one price list."""
    seen = {}
    for row in batch.rows.filter(match_status='new').only('id', 'row_number', 'extracted_data'):
        data = row.extracted_data or {}
        key = drug_key(data.get('name', ''), data.get('generic_name', ''),
                        data.get('dosage_form', ''), data.get('strength', ''))
        if key[0]:
            seen[key] = row
    return seen


def _classify_row(extracted: dict, seen_in_batch: dict):
    """Returns (match_status, matched_master_drug, confidence, review_notes)."""
    key = drug_key(extracted.get('name', ''), extracted.get('generic_name', ''),
                    extracted.get('dosage_form', ''), extracted.get('strength', ''))
    generic_norm, dosage_norm, strength_norm = key
    if not generic_norm:
        return 'needs_review', None, None, 'تعذّر استخراج اسم أو مادة فعالة من هذا الصف'

    active_drugs = MasterDrug.objects.filter(status='active')

    exact = find_exact_candidates(active_drugs, generic_norm, dosage_norm, strength_norm).first()
    if exact:
        return 'duplicate_exact', exact, 1.0, ''

    candidates = find_fuzzy_candidates(active_drugs, generic_norm, bucket_field='generic_name_normalized')
    if candidates:
        best_drug, best_ratio = candidates[0]
        classification = classify_match(best_ratio)
        if classification == 'duplicate_fuzzy':
            matched_name = match_drug_to_master(
                extracted, [c.generic_name for c, _ in candidates]
            )
            if matched_name:
                for drug, ratio in candidates:
                    if drug.generic_name == matched_name:
                        return 'duplicate_fuzzy', drug, round(ratio, 2), ''
            return 'needs_review', best_drug, round(best_ratio, 2), 'تطابق محتمل — يحتاج تأكيد يدوي'
        if classification == 'duplicate_exact':
            return 'duplicate_exact', best_drug, round(best_ratio, 2), ''

    if key in seen_in_batch:
        other = seen_in_batch[key]
        return 'needs_review', None, None, f'يشبه الصف رقم {other.row_number} في نفس الملف'

    return 'new', None, None, ''


def process_batch_chunk(batch: CatalogImportBatch, chunk_size: int = 25) -> dict:
    """Processes the next chunk of pending rows. Call repeatedly until done=True."""
    chunk_rows = list(
        batch.rows.filter(match_status='pending').order_by('row_number')[:chunk_size]
    )
    if not chunk_rows:
        if batch.status not in ('reviewing', 'committed'):
            batch.status = 'reviewing'
            batch.save(update_fields=['status'])
        return {'processed': batch.processed_rows, 'total': batch.total_rows, 'done': True}

    if batch.status == 'pending':
        batch.status = 'extracting'
        batch.save(update_fields=['status'])

    extracted_list = extract_drug_rows_batch(
        [row.raw_data for row in chunk_rows], batch_size=len(chunk_rows)
    )

    seen_in_batch = _seen_keys_in_batch(batch)

    for row, extracted in zip(chunk_rows, extracted_list):
        row.extracted_data = extracted
        status, matched_drug, confidence, notes = _classify_row(extracted, seen_in_batch)
        row.match_status = status
        row.matched_master_drug = matched_drug
        row.confidence_score = confidence
        row.review_notes = notes
        row.save(update_fields=['extracted_data', 'match_status', 'matched_master_drug',
                                 'confidence_score', 'review_notes'])
        if status == 'new':
            key = drug_key(extracted.get('name', ''), extracted.get('generic_name', ''),
                            extracted.get('dosage_form', ''), extracted.get('strength', ''))
            seen_in_batch[key] = row

    batch.processed_rows = batch.rows.exclude(match_status='pending').count()
    batch.save(update_fields=['processed_rows'])

    done = batch.processed_rows >= batch.total_rows
    if done:
        batch.status = 'reviewing'
        batch.save(update_fields=['status'])

    return {'processed': batch.processed_rows, 'total': batch.total_rows, 'done': done}


def _resolve_master_category(name: str, user):
    name = (name or '').strip()
    if not name:
        return None
    category, _ = MasterCategory.objects.get_or_create(name=name)
    return category


def commit_batch(batch: CatalogImportBatch, user, decisions: dict) -> dict:
    """
    decisions: {row_id: {
        'action': 'create_new' | 'link_alias' | 'skip',
        'master_drug_id': int (required for link_alias),
        'generic_name', 'dosage_form', 'strength', 'category',
        'trade_name', 'manufacturer', 'barcode',
        'requires_prescription', 'is_controlled_substance',
    }}
    Returns {'committed': int, 'skipped': int, 'errors': [...]}
    """
    committed = 0
    skipped = 0
    errors = []

    batch.status = 'committing'
    batch.save(update_fields=['status'])

    for row_id, decision in decisions.items():
        try:
            row = batch.rows.get(pk=row_id)
        except CatalogImportRow.DoesNotExist:
            continue

        action = decision.get('action')
        if action == 'skip':
            row.match_status = 'rejected'
            row.reviewed_by = user
            row.reviewed_at = timezone.now()
            row.save(update_fields=['match_status', 'reviewed_by', 'reviewed_at'])
            skipped += 1
            continue

        try:
            with transaction.atomic():
                if action == 'create_new':
                    if not decision.get('generic_name', '').strip() and not decision.get('trade_name', '').strip():
                        errors.append({'row': row.row_number, 'message': 'الصف بلا اسم تجاري ولا اسم علمي — تم تجاوزه'})
                        continue
                    master_drug = MasterDrug.objects.create(
                        generic_name=decision.get('generic_name', '').strip(),
                        dosage_form=decision.get('dosage_form', '').strip(),
                        strength=decision.get('strength', '').strip(),
                        category=_resolve_master_category(decision.get('category', ''), user),
                        requires_prescription=bool(decision.get('requires_prescription')),
                        is_controlled_substance=bool(decision.get('is_controlled_substance')),
                        created_by=user, updated_by=user,
                    )
                    MasterDrugAlias.objects.create(
                        master_drug=master_drug,
                        trade_name=decision.get('trade_name', '').strip() or master_drug.generic_name,
                        manufacturer=decision.get('manufacturer', '').strip(),
                        barcode=decision.get('barcode', '').strip(),
                        is_primary=True, source_batch=batch, created_by=user,
                    )
                elif action == 'link_alias':
                    master_drug = MasterDrug.objects.get(pk=decision['master_drug_id'])
                    MasterDrugAlias.objects.create(
                        master_drug=master_drug,
                        trade_name=decision.get('trade_name', '').strip(),
                        manufacturer=decision.get('manufacturer', '').strip(),
                        barcode=decision.get('barcode', '').strip(),
                        is_primary=False, source_batch=batch, created_by=user,
                    )
                else:
                    errors.append({'row': row.row_number, 'message': f'إجراء غير معروف: {action}'})
                    continue

                row.matched_master_drug = master_drug
                row.match_status = 'committed'
                row.reviewed_by = user
                row.reviewed_at = timezone.now()
                row.save(update_fields=['matched_master_drug', 'match_status', 'reviewed_by', 'reviewed_at'])
                committed += 1
        except Exception as exc:
            errors.append({'row': row.row_number, 'message': str(exc)})

    if not batch.rows.filter(match_status__in=('pending', 'new', 'duplicate_exact', 'duplicate_fuzzy', 'needs_review')).exists():
        batch.status = 'committed'
    else:
        batch.status = 'reviewing'
    batch.save(update_fields=['status'])

    return {'committed': committed, 'skipped': skipped, 'errors': errors}
