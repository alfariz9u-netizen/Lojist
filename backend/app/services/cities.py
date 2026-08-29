"""
Canonical Saudi city list + normalization, so "جده"/"جدة"/"jeddah" all
resolve to one canonical spelling before matching. No Google Maps / geo
API in this MVP -- a static table is enough for the corridors that
actually matter, per project scope.
"""

# canonical -> list of accepted variant spellings (typos, alt transliteration)
_CITY_VARIANTS: dict[str, list[str]] = {
    "الرياض": ["الرياض", "رياض", "riyadh"],
    "جدة": ["جدة", "جده", "jeddah", "jedda"],
    "الدمام": ["الدمام", "دمام", "dammam"],
    "الخبر": ["الخبر", "خبر", "khobar"],
    "الظهران": ["الظهران", "ظهران", "dhahran"],
    "مكة": ["مكة", "مكه", "مكة المكرمة", "mecca", "makkah"],
    "المدينة": ["المدينة", "المدينه", "المدينة المنورة", "madinah", "medina"],
    "الطائف": ["الطائف", "الطايف", "taif"],
    "أبها": ["أبها", "ابها", "abha"],
    "تبوك": ["تبوك", "tabuk"],
    "حائل": ["حائل", "حايل", "hail"],
    "القصيم": ["القصيم", "بريدة", "buraydah", "qassim"],
    "ينبع": ["ينبع", "yanbu"],
    "الجبيل": ["الجبيل", "jubail"],
    "نجران": ["نجران", "najran"],
    "جازان": ["جازان", "جيزان", "jazan", "gizan"],
}

_LOOKUP: dict[str, str] = {}
for _canonical, _variants in _CITY_VARIANTS.items():
    for _v in _variants:
        _LOOKUP[_v.strip().lower()] = _canonical

CANONICAL_CITIES = sorted(_CITY_VARIANTS.keys())


def normalize_city(raw: str | None) -> str | None:
    """Returns the canonical spelling for a known city, or the
    whitespace-trimmed original if it isn't recognized (so an unusual
    but real city name isn't silently dropped -- it just won't
    auto-match on spelling variants)."""
    if not raw:
        return None
    stripped = raw.strip()
    return _LOOKUP.get(stripped.lower(), stripped)


def is_known_city(raw: str | None) -> bool:
    if not raw:
        return False
    return raw.strip().lower() in _LOOKUP
