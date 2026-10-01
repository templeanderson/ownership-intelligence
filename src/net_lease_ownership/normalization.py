"""Deterministic comparisons; no identity inference or AI calls.

Callers retain original fields. Empty inputs normalize to empty strings, which
must never be interpreted as corroborating evidence in later matching logic.
"""

import re
import unicodedata


NORMALIZATION_VERSION = "2"

# Missing-value markers remain visible in source/normalized fields but must not
# become useful matching evidence. These are fixed safeguards, not score policy.
_MISSING_MARKERS = {
    "n a", "na", "unknown", "unknown owner", "unknown entity", "tbd",
    "to be determined", "none", "null", "not applicable", "not available",
    "not known", "not provided", "unavailable", "missing", "pending",
    "undisclosed", "0",
}

# Normalize dotted abbreviations before punctuation can split their letters.
_DOTTED_SUFFIXES = {
    "llc": r"\bl\s*\.\s*l\s*\.\s*c\s*\.?",
    "llp": r"\bl\s*\.\s*l\s*\.\s*p\s*\.?",
    "lp": r"\bl\s*\.\s*p\s*\.?",
}
_LEGAL_SUFFIXES = {"llc", "llp", "lp", "inc", "corp", "ltd"}
_LEGAL_ALIASES = {
    "incorporated": "inc",
    "corporation": "corp",
    "limited": "ltd",
}
_STREET_ABBREVIATIONS = {
    "street": "st", "road": "rd", "avenue": "ave", "boulevard": "blvd",
    "drive": "dr", "lane": "ln", "court": "ct", "parkway": "pkwy",
    "highway": "hwy", "place": "pl", "circle": "cir", "terrace": "ter",
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
    "ste": "suite", "apartment": "apt",
}


def _text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("Normalization requires a string; use '' for missing data")
    return unicodedata.normalize("NFKC", value).casefold().strip()


def _clean_punctuation(value: str) -> str:
    # Apostrophes and periods within words are removed; other punctuation
    # becomes a separator, so a hyphen cannot merge two meaningful tokens.
    value = value.replace("'", "").replace("’", "").replace(".", "")
    value = re.sub(r"[^\w\s]", " ", value)
    value = value.replace("_", " ")
    return " ".join(value.split())


def normalize_company_name(value: str, *, remove_legal_suffix: bool = False) -> str:
    """Standardize a name, optionally removing only a trailing legal suffix.

    Meaningful words such as 'medical', 'holdings', and 'properties' are kept.
    This function does not expand business abbreviations or resolve aliases.
    """
    value = _text(value).replace("&", " and ")
    for suffix, pattern in _DOTTED_SUFFIXES.items():
        value = re.sub(pattern + r"(?=\W|$)", suffix, value)
    words = _clean_punctuation(value).split()
    if words:
        words[-1] = _LEGAL_ALIASES.get(words[-1], words[-1])
    if remove_legal_suffix and words and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def is_useful_name(comparison: str) -> bool:
    """Reject missing markers and names without any letters, without erasing them."""
    return comparison not in _MISSING_MARKERS and any(character.isalpha() for character in comparison)


def is_useful_address(normalized: str) -> bool:
    """Require a basic numbered street or PO box, not postal validation.

    This conservative US-fixture check treats unsupported formats as research
    evidence. City or ZIP digits cannot stand in for a missing street address.
    """
    street = normalized.partition(",")[0].strip()
    if street in _MISSING_MARKERS or not street:
        return False
    if re.match(r"^po box [1-9]\d*\b", street):
        return True
    return bool(re.match(r"^[1-9]\d*[a-z]?\s+", street)
                and any(character.isalpha() for character in street))


def normalize_address(value: str) -> str:
    """Normalize US-style sample addresses without dropping numbers or units.

    The street/city comma is retained so normalization remains idempotent and
    city names such as 'West Lake' stay outside street abbreviation rules.
    """
    value = _text(value)
    street, separator, remainder = value.partition(",")
    street = re.sub(r"#\s*(?=\w)", " suite ", street)
    street = re.sub(r"\bp\s*\.?\s*o\s*\.?\s+box\b", "po box", street)
    words = _clean_punctuation(street).split()
    normalized_street = " ".join(_STREET_ABBREVIATIONS.get(word, word) for word in words)
    normalized_remainder = _clean_punctuation(remainder) if separator else ""
    if separator and normalized_remainder:
        return f"{normalized_street}, {normalized_remainder}"
    return normalized_street
