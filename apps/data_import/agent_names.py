"""
توحيد أسماء الوكلاء/الشركات عند الاستيراد: نقطة زائدة، ة/ه، همزات، ال التعريف، وكلمات الشركة
(Ltd / Co / Company / شركة / المحدودة ...) كلها تعني نفس الوكيل، فلا يُنشأ اسمان له.

الاسم الموحَّد: كتابة موجودة فعلاً في المنتجات الحالية أولاً، وإلا أكثر كتابة تكراراً في الملف.
"""
import difflib
import re
from collections import Counter

from apps.data_import import smart_reader as sr

# كلمات تدل على الصفة القانونية لا على اسم الوكيل (تُهمل عند المقارنة فقط، لا من الاسم المحفوظ)
_LEGAL_WORDS = {
    'ltd', 'limited', 'co', 'company', 'corp', 'corporation', 'inc', 'llc', 'plc', 'pvt', 'sa', 'gmbh',
    'شركه', 'الشركه', 'المحدوده', 'محدوده', 'المحدودة', 'محدودة', 'مساهمه', 'م', 'ش', 'ذ', 'ذمم', 'شمم',
}
_FUZZY_CUTOFF = 0.9
_MIN_FUZZY_LEN = 6


def agent_key(name) -> str:
    """مفتاح المقارنة: اسم موحّد بلا الصفة القانونية وبلا ال التعريف."""
    text = sr.normalize_text(name)
    words = []
    for word in text.split():
        if word in _LEGAL_WORDS:
            continue
        if word.startswith('ال') and len(word) > 4:
            word = word[2:]
        words.append(word)
    return ' '.join(words)


def canonical_agent_map(raw_names, existing_names=()):
    """
    {كتابة في الملف: الاسم الموحّد}. raw_names قائمة (مع التكرار) بما في الملف،
    existing_names أسماء الوكلاء المحفوظة في منتجات النشاط.
    """
    counts = Counter(n.strip() for n in raw_names if n and n.strip())
    existing_by_key = {}
    for name in existing_names:
        key = agent_key(name)
        if key:
            existing_by_key.setdefault(key, name.strip())

    groups = {}  # مفتاح → {كتابة: تكرار}
    for raw, count in counts.items():
        key = agent_key(raw) or sr.normalize_text(raw)
        groups.setdefault(key, Counter())[raw] += count

    # دمج المفاتيح المتقاربة جداً (خطأ إملائي بسيط) — للأسماء الطويلة فقط حتى لا تُدمج شركتان مختلفتان
    keys = sorted(groups, key=lambda k: -sum(groups[k].values()))
    merged_into = {}
    anchors = []
    for key in keys:
        target = None
        if len(key) >= _MIN_FUZZY_LEN:
            close = difflib.get_close_matches(key, [a for a in anchors if len(a) >= _MIN_FUZZY_LEN], n=1,
                                              cutoff=_FUZZY_CUTOFF)
            target = close[0] if close else None
        if target:
            merged_into[key] = target
            groups[target].update(groups[key])
        else:
            anchors.append(key)

    result = {}
    for key in anchors:
        spellings = groups[key]
        keys_in_group = [key] + [k for k, t in merged_into.items() if t == key]
        canonical = next((existing_by_key[k] for k in keys_in_group if k in existing_by_key), None)
        if canonical is None:
            canonical = _clean(sorted(spellings.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0])
        for raw in spellings:
            result[raw] = canonical
    return result


def _clean(name):
    """يزيل النقاط/الفواصل الزائدة في آخر الاسم ويضبط المسافات."""
    return re.sub(r'[\s.,;:\-_/\\]+$', '', re.sub(r'\s+', ' ', name.strip()))
