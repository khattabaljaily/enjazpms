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


_TRAILING_NUMBER_RE = re.compile(r'(\d+(?:[.,]\d+)?)\s*$')
_LEADING_NUMBER_RE = re.compile(r'^(\d+(?:[.,]\d+)?)\s*(.*)$')


_FIRST_AMOUNT_RE = re.compile(r'\d[\d.,]*\s*(?:mg|mcg|ug|gm|g|ml|iu|%)', re.IGNORECASE)
_STRENGTH_WORDS = {'mg', 'mcg', 'ug', 'g', 'gm', 'ml', 'iu', 'l', 'dl', 'cm2', 'dose', 'doses', 'vial', 'sachet',
                   'amp', 'ampoule', 'w', 'v', 'mmol', 'meq', 'unit', 'units', 'tab', 'tablet', 'hr', 'h', 'kg', 'cm'}


def is_clean_strength(strength: str) -> bool:
    """True for amounts like "500 mg", "5mg/5ml", "0.05%", "100 IU/ml". Rejects the
    junk the PDF reader leaves in the strength cell: cut-off text ("5 mg Glyburide /"),
    unbalanced brackets, drug words, and anything very long."""
    strength = (strength or '').strip()
    if not strength or len(strength) > 30 or not re.search(r'\d', strength):
        return False
    if strength.count('(') != strength.count(')') or strength[-1] in '/(+-,&':
        return False
    return all(w in _STRENGTH_WORDS for w in re.findall(r'[A-Za-z]+', strength.lower()))


def merge_strength_into_name(name: str, strength: str) -> str:
    """Appends the strength to a trade name ("Panadol" + "500 mg" -> "Panadol 500 mg"),
    without parentheses, unless the name already carries it."""
    name = (name or '').strip()
    strength = (strength or '').strip()
    if not is_clean_strength(strength):
        return name
    if not name:
        return strength
    squashed_name = normalize_text(name).replace(' ', '')
    if normalize_text(strength).replace(' ', '') in squashed_name:
        return name
    first = _FIRST_AMOUNT_RE.match(strength)
    if first and normalize_text(first.group(0)).replace(' ', '') in squashed_name:
        return name      # the name already states the main amount ("Irinotel 100 mg" + "100mg/5ml")
    # Name already ends with the strength's number ("Acetazolamide 250" + "250 mg"):
    # only the unit is missing.
    tail = _TRAILING_NUMBER_RE.search(name)
    head = _LEADING_NUMBER_RE.match(strength)
    if tail and head and tail.group(1).replace(',', '.') == head.group(1).replace(',', '.') and head.group(2):
        return f'{name} {head.group(2)}'
    return f'{name} {strength}'


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


# ---------------------------------------------------------------------------
# PDF-import clean-up helpers
# ---------------------------------------------------------------------------

# Stray lowercase letter glued in front of a name by PDF extraction
# ("aAzox 250mg" -> "Azox 250mg", "pApifrin-Z" -> "Apifrin-Z").
_STRAY_PREFIX_RE = re.compile(r'^[a-z](?=[A-Z])')

_COMPANY_SYNONYMS = {
    'limited': 'ltd', 'company': 'co', 'private': 'pvt', 'corporation': 'corp',
    'incorporated': 'inc', 'laboratories': 'lab', 'laboratory': 'lab', 'labs': 'lab',
    'pharmaceuticals': 'pharma', 'pharmaceutical': 'pharma', 'industries': 'ind', 'industry': 'ind',
}


def clean_product_name(name: str) -> str:
    """Removes the stray leading lowercase letter and collapses whitespace."""
    name = _WHITESPACE.sub(' ', (name or '').strip())
    return _STRAY_PREFIX_RE.sub('', name)


def manufacturer_key(name: str) -> str:
    """Comparison key for company names: ignores case, punctuation, spacing and
    ltd/limited-style spelling variants ("Remedica Ltd." == "Remedica Ltd")."""
    text = normalize_text((name or '').replace('&', ' and '))
    words = [_COMPANY_SYNONYMS.get(w, w) for w in text.split()]
    return ''.join(words)


def build_manufacturer_canon(names) -> dict:
    """
    names: iterable of raw company names (with repeats — frequency picks the winner).
    Returns {raw_name: canonical_name}. Variants that only differ by punctuation/
    case/ltd-vs-limited are merged; a name that is a truncated prefix (>=12 chars)
    of exactly one longer company is merged into it too (PDF cells cut at a width).
    The canonical spelling is the most frequent variant (ties -> longer, then
    the one without a trailing period).
    """
    from collections import Counter, defaultdict

    freq = Counter(n.strip() for n in names if n and n.strip())
    by_key = defaultdict(list)
    for raw, cnt in freq.items():
        by_key[manufacturer_key(raw)].append((raw, cnt))

    keys = sorted(k for k in by_key if k)
    target = {}

    def resolve(k):
        seen = set()
        while k in target and k not in seen:
            seen.add(k)
            k = target[k]
        return k

    # A truncated key merges into the longer company it prefixes. If several
    # longer keys exist but they all collapse into one company (A < AB < ABC),
    # it merges into that one too — iterate until nothing changes so a single
    # run reaches the final state.
    changed = True
    while changed:
        changed = False
        for k in keys:
            if len(k) < 12 or k in target:
                continue
            roots = {resolve(o) for o in keys if o != k and o.startswith(k)}
            roots.discard(k)
            if len(roots) == 1:
                target[k] = roots.pop()
                changed = True

    groups = defaultdict(list)
    for k, variants in by_key.items():
        if k:
            groups[resolve(k)].extend(variants)

    canon = {}
    for variants in groups.values():
        best = max(variants, key=lambda v: (v[1], len(v[0]), not v[0].endswith('.')))[0]
        for raw, _ in variants:
            canon[raw] = best
    return canon


# ---------------------------------------------------------------------------
# Detecting rows whose scientific / trade names were stored in each other's field
# ---------------------------------------------------------------------------

_UNIT_WORDS = {'mg', 'g', 'gm', 'ml', 'mcg', 'iu', 'w', 'v', 'l', 'ug', 'mmol', 'meq'}
_TRAILING_STRENGTH_RE = re.compile(r'[\s\-–]*\d[\d.,]*\s*(?:mg|mcg|gm|g|ml|iu|%)?\b.*$', re.IGNORECASE)


def name_match_key(name: str) -> str:
    """Name with strengths/numbers/unit words dropped, for comparing a scientific
    name against the catalog's generic names ("Acetazolamide 250" -> "acetazolamide")."""
    words = [w for w in normalize_text(clean_product_name(name)).split()
             if not any(ch.isdigit() for ch in w) and w not in _UNIT_WORDS]
    return ' '.join(words)


def strip_trailing_strength(name: str) -> str:
    """"Acetazolamide 250" -> "Acetazolamide" (keeps the name if nothing would be left)."""
    stripped = _TRAILING_STRENGTH_RE.sub('', (name or '').strip()).strip(' -–')
    return stripped if len(stripped) >= 3 else (name or '').strip()


_SCIENTIFIC_ENDINGS = (
    'ine', 'ol', 'ide', 'ate', 'cillin', 'mycin', 'azole', 'pril', 'sartan', 'statin', 'vir', 'mab', 'ium',
    'one', 'fenac', 'profen', 'oxacin', 'dipine', 'prazole', 'tidine', 'thiazide', 'sone', 'cycline',
    'sulfate', 'chloride', 'sodium', 'acid', 'hydrochloride',
)


def scientific_score(name: str) -> int:
    """Weak tie-breaker: how many words of the name end like INN / chemical names."""
    return sum(1 for w in name_match_key(name).split() if len(w) > 4 and w.endswith(_SCIENTIFIC_ENDINGS))


class NamePairIndex:
    """
    Evidence for which of two names is the scientific one, taken from the whole
    catalog: a scientific name is paired with MANY different trade names
    ("Aceclofenac" <-> Aceclo, Acenac, Aclotas...), a trade name with one or few
    scientific names. Both field orders are counted, so rows stored reversed
    cannot pollute the evidence. Names cut at ~20 chars by the PDF reader are
    folded into the longer name they prefix.
    """

    def __init__(self, pairs):
        from collections import defaultdict
        import bisect
        raw = [(name_match_key(g), name_match_key(t)) for g, t in pairs]
        raw = [(g, t) for g, t in raw if g and t and g != t]
        self._keys = sorted({k for pair in raw for k in pair})
        self._bisect = bisect
        self._canon_cache = {}
        self.partners = defaultdict(set)
        for g, t in raw:
            g, t = self.canon(g), self.canon(t)
            if g != t:
                self.partners[g].add(t)
                self.partners[t].add(g)

    def canon(self, key: str) -> str:
        if key in self._canon_cache:
            return self._canon_cache[key]
        result = key
        if len(key) >= 15:
            i = self._bisect.bisect_left(self._keys, key)
            longer = []
            while i < len(self._keys) and self._keys[i].startswith(key):
                if len(self._keys[i]) > len(key) and self._keys[i][len(key)] != ' ':    # cut mid-word
                    longer.append(self._keys[i])
                i += 1
            if longer:
                result = min(longer, key=len)
        self._canon_cache[key] = result
        return result

    def degree(self, name: str) -> int:
        return len(self.partners.get(self.canon(name_match_key(name)), ()))


def is_reversed_pair(generic_name: str, trade_name: str, index: NamePairIndex) -> bool:
    """True when the field called "trade" holds the scientific name of the pair."""
    g_key, t_key = name_match_key(generic_name), name_match_key(trade_name)
    if not g_key or not t_key or g_key.split(' ')[:1] == t_key.split(' ')[:1]:
        return False      # "Abacavir" vs "Abacavir Sulfate": salt variants, not a brand/scientific pair
    dg, dt = index.degree(generic_name), index.degree(trade_name)
    if dg != dt:
        return dt > dg
    return scientific_score(trade_name) > scientific_score(generic_name)
