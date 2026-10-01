"""Deterministic comparisons; no identity inference or AI calls.

Callers retain original fields. Empty inputs normalize to empty strings, which
must never be interpreted as corroborating evidence in later matching logic.
"""

import re
import unicodedata


NORMALIZATION_VERSION = "1"

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


def normalize_address(value: str) -> str:
    """Normalize US-style sample addresses without dropping numbers or units.

    Street abbreviations apply only before the first comma, preserving city
    names such as 'West Lake'. No geocoding or postal validation is performed.
    """
    value = _text(value)
    street, separator, remainder = value.partition(",")
    street = re.sub(r"#\s*(?=\w)", " suite ", street)
    street = re.sub(r"\bp\s*\.?\s*o\s*\.?\s+box\b", "po box", street)
    words = _clean_punctuation(street).split()
    normalized_street = " ".join(_STREET_ABBREVIATIONS.get(word, word) for word in words)
    normalized_remainder = _clean_punctuation(remainder) if separator else ""
    return " ".join(part for part in (normalized_street, normalized_remainder) if part)
