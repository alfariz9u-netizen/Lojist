import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.services.cities import normalize_city


def test_spelling_variants_resolve_to_canonical():
    assert normalize_city("جده") == "جدة"
    assert normalize_city("جدة") == "جدة"
    assert normalize_city("jeddah") == "جدة"
    assert normalize_city("الطايف") == "الطائف"
    assert normalize_city("الطائف") == "الطائف"


def test_unknown_city_passes_through_trimmed():
    assert normalize_city("  بريدة الجديدة  ") == "بريدة الجديدة"


def test_none_and_empty():
    assert normalize_city(None) is None
    assert normalize_city("") is None
