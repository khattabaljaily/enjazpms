"""
Normalization + duplicate-matching primitives shared by:
- apps.catalog.models (MasterDrug/MasterDrugAlias normalized fields)
- apps.catalog.ingest (matching extracted price-list rows against MasterDrug)
- apps.items.dedup (matching imported rows against a tenant's own Item table)

No fuzzy-matching library is used on purpose — the codebase has no such
dependency today, and the existing precedent for approximate text matching
is plain stdlib `difflib` (apps/ai/services.py::find_similar_post). This
module reuses that same technique, but bucketed by a prefix of the
normalized field being compared, so it never scans an entire table.
"""
import difflib
import re
import unicodedata

_ARABIC_DIACRITICS = re.compile(r'[ً-ٰٟۖ-ۭ]')
_WHITESPACE = re.compile(r'\s+')
_PUNCTUATION = re.compile(r'[^\w\s]', re.UNICODE)

# number + unit token, e.g. "500 mg", "500mg", "500 مجم"
_STRENGTH_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*([a-zA-Z؀-ۿ%/]+)')

_UNIT_ALIASES = {
    'mg': 'mg', 'ملغ': 'mg', 'مجم': 'mg', 'ملغم': 'mg',
    'g': 'g', 'gm': 'g', 'جم': 'g', 'جرام': 'g',
    'mcg': 'mcg', 'ميكروجرام': 'mcg', 'µg': 'mcg',
    'ml': 'ml', 'مل': 'ml',
    'iu': 'iu', 'وحدة': 'iu',
    '%': '%',
}


def normalize_text(value: str) -> str:
    """Lowercase, strip Arabic diacritics/punctuation, collapse whitespace."""
    if not value:
        return ''
    value = unicodedata.normalize('NFKC', str(value))
    value = _ARABIC_DIACRITICS.sub('', value)
    value = _PUNCTUATION.sub(' ', value)
    value = _WHITESPACE.sub(' ', value)
    return value.strip().lower()


def normalize_strength(value: str) -> str:
    """
    Extract a canonical "{number}|{unit}" token from free text like
    "500 mg" / "500مجم" / "5ml". Returns '' if nothing parseable is found.
    """
    if not value:
        return ''
    match = _STRENGTH_RE.search(str(value))
    if not match:
        return ''
    number, unit = match.groups()
    number = number.replace(',', '.')
    unit_key = normalize_text(unit)
    unit = _UNIT_ALIASES.get(unit_key, unit_key)
    return f'{number}|{unit}'


def drug_key(name: str = '', generic_name: str = '', dosage_form: str = '', strength: str = '') -> tuple:
    """
    Returns (generic_name_normalized, dosage_form_normalized, strength_normalized),
    falling back to normalized `name` for the first component when generic_name
    is blank (many supplier price lists only have name/price, no generic name).
    """
    generic_norm = normalize_text(generic_name) or normalize_text(name)
    return generic_norm, normalize_text(dosage_form), normalize_strength(strength)


def find_exact_candidates(queryset, generic_norm: str, dosage_norm: str = '', strength_norm: str = '',
                           generic_field: str = 'generic_name_normalized',
                           dosage_field: str = 'dosage_form_normalized',
                           strength_field: str = 'strength_normalized'):
    """
    Indexed exact lookup — the fast first-pass before any fuzzy matching.
    Field names default to MasterDrug's `_normalized` columns; pass the
    `*_field` kwargs to point at differently-named fields (e.g. Item, which
    has no dedicated normalized columns — see apps.items.dedup for how it
    normalizes on the fly with Django's `Lower`/`iexact` instead).
    """
    if not generic_norm:
        return queryset.none()
    qs = queryset.filter(**{generic_field: generic_norm})
    if dosage_norm:
        qs = qs.filter(**{dosage_field: dosage_norm})
    if strength_norm:
        qs = qs.filter(**{strength_field: strength_norm})
    return qs


def find_fuzzy_candidates(queryset, normalized_name: str, bucket_field: str = 'generic_name_normalized',
                           limit: int = 8, floor: float = 0.55):
    """
    Bucketed difflib fallback: restricts `queryset` to rows sharing the first
    3 characters of `bucket_field` (cheap, indexed prefix filter — keeps the
    candidate set small) then ranks that bucket with SequenceMatcher.

    Returns a list of (obj, ratio) sorted by ratio desc, capped at `limit`.
    """
    if not normalized_name:
        return []
    prefix = normalized_name[:3]
    bucket = queryset.filter(**{f'{bucket_field}__startswith': prefix})

    scored = []
    for obj in bucket:
        candidate = getattr(obj, bucket_field, '') or ''
        ratio = difflib.SequenceMatcher(None, normalized_name, candidate).ratio()
        if ratio >= floor:
            scored.append((obj, ratio))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


def classify_match(ratio: float) -> str:
    """
    Tunable thresholds — starting point, adjust once real price lists have
    been run through the pipeline a few times.
    """
    if ratio >= 0.92:
        return 'duplicate_exact'
    if ratio >= 0.65:
        return 'duplicate_fuzzy'
    return 'new'
