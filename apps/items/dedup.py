"""
Duplicate detection for tenant-level Item creation (used by the product
importer). Reuses the normalization/matching primitives in
apps.catalog.matching so a tenant's own Item table and the shared
MasterDrug catalog are compared with identical rules.
"""
import difflib

from apps.catalog.matching import drug_key, normalize_text, classify_match
from .models import Item


def find_existing_item_match(tenant, name='', generic_name='', dosage_form='', strength='', barcode=''):
    """
    Returns {'item': Item, 'match_type': 'barcode'|'exact'|'fuzzy', 'confidence': float}
    or None if nothing looks like a duplicate.
    """
    barcode = (barcode or '').strip()
    if barcode:
        item = Item.objects.for_tenant(tenant).filter(barcode=barcode).first()
        if item:
            return {'item': item, 'match_type': 'barcode', 'confidence': 1.0}

    generic_norm, dosage_norm, strength_norm = drug_key(name, generic_name, dosage_form, strength)
    if not generic_norm:
        return None

    qs = Item.objects.for_tenant(tenant).filter(is_active=True)
    exact_filters = {'generic_name__iexact': generic_name.strip()} if generic_name else {'name__iexact': name.strip()}
    if dosage_form:
        exact_filters['dosage_form__iexact'] = dosage_form.strip()
    if strength:
        exact_filters['strength__iexact'] = strength.strip()
    exact = qs.filter(**exact_filters).first()
    if exact:
        return {'item': exact, 'match_type': 'exact', 'confidence': 1.0}

    # Bucketed difflib fallback: Item has no dedicated _normalized column (unlike
    # MasterDrug), so normalize on the fly and prefix-filter case-insensitively
    # to keep the candidate set small instead of scanning the whole tenant.
    bucket_field = 'generic_name' if generic_name else 'name'
    prefix = generic_norm[:3]
    bucket = qs.filter(**{f'{bucket_field}__istartswith': prefix}) if prefix else qs.none()

    best_item, best_ratio = None, 0.0
    for candidate in bucket:
        candidate_norm = normalize_text(getattr(candidate, bucket_field, ''))
        ratio = difflib.SequenceMatcher(None, generic_norm, candidate_norm).ratio()
        if ratio > best_ratio:
            best_item, best_ratio = candidate, ratio

    if best_item and classify_match(best_ratio) != 'new':
        return {'item': best_item, 'match_type': 'fuzzy', 'confidence': round(best_ratio, 2)}

    return None
