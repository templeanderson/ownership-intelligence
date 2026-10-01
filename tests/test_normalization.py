import pytest

from net_lease_ownership.normalization import normalize_address, normalize_company_name


@pytest.mark.parametrize("original,expected", [
    ("ABC Medical Holdings LLC", "abc medical holdings llc"),
    ("ABC MEDICAL HOLDINGS, L.L.C.", "abc medical holdings llc"),
    ("  ABC   Medical\tHoldings\nLLC  ", "abc medical holdings llc"),
    ("Maple & Stone Holdings, LLC", "maple and stone holdings llc"),
    ("O’Brien Medical, L.L.C.", "obrien medical llc"),
    ("Oakridge Commercial Incorporated", "oakridge commercial inc"),
    ("Oakridge Commercial, INC.", "oakridge commercial inc"),
    ("ABC Holdings L.L.P.", "abc holdings llp"),
    ("ABC Holdings L.P.", "abc holdings lp"),
    ("Alpha-Beta Holdings LLC", "alpha beta holdings llc"),
    ("", ""), ("  ", ""),
])
def test_company_normalization(original, expected):
    assert normalize_company_name(original) == expected


@pytest.mark.parametrize("original,expected", [
    ("ABC Medical Holdings, L.L.C.", "abc medical holdings"),
    ("ABC Corporation", "abc"),
    ("ABC Limited", "abc"),
    ("LLC Medical Holdings", "llc medical holdings"),
    ("ABC Property Group", "abc property group"),
    ("", ""),
])
def test_only_terminal_legal_suffix_is_removed(original, expected):
    assert normalize_company_name(original, remove_legal_suffix=True) == expected


@pytest.mark.parametrize("original,expected", [
    (" 100 MAIN STREET, Dallas, TX 75001 ", "100 main st, dallas tx 75001"),
    ("100 Main St., DALLAS, TX 75001", "100 main st, dallas tx 75001"),
    ("900 North Example Road Ste. 200, Arlington, TX 76010",
     "900 n example rd suite 200, arlington tx 76010"),
    ("900 N. Example Rd. #200, Arlington, TX 76010",
     "900 n example rd suite 200, arlington tx 76010"),
    ("100 Main Street, West Lake, TX 75001", "100 main st, west lake tx 75001"),
    ("P.O. Box 321, Dallas, TX 75001", "po box 321, dallas tx 75001"),
    ("100 Main Road Apartment 5", "100 main rd apt 5"),
    ("", ""), ("  ", ""),
])
def test_address_normalization(original, expected):
    assert normalize_address(original) == expected


@pytest.mark.parametrize("left,right", [
    ("100 Main Street Suite 200", "100 Main Street Suite 201"),
    ("100 Main Street", "101 Main Street"),
    ("100 Main Street", "100 Main Street Suite 200"),
    ("100 Main Street, Dallas, TX 75001", "100 Main Street, Dallas, TX 75002"),
])
def test_address_identifiers_are_not_erased(left, right):
    assert normalize_address(left) != normalize_address(right)


@pytest.mark.parametrize("left,right", [
    ("Sunrise Dallas Property LLC", "Sunrise Investment Group LLC"),
    ("ABC Medical Holdings LLC", "ABC Med Holdings LLC"),
    ("Sunrise Property Group LLC", "Sunrise Properties Group LLC"),
])
def test_meaningful_name_differences_are_preserved(left, right):
    assert normalize_company_name(left, remove_legal_suffix=True) != normalize_company_name(
        right, remove_legal_suffix=True)


@pytest.mark.parametrize("value", ["ABC MEDICAL, L.L.C.", "Maple & Stone LLC", "", "Alpha-Beta Inc."])
def test_company_normalization_is_idempotent(value):
    normalized = normalize_company_name(value)
    assert normalize_company_name(normalized) == normalized


@pytest.mark.parametrize("value", [
    "100 Main Rd., Dallas, TX 75001", "900 North Road #200", "P.O. Box 321", "",
    "100 Main Street, West Lake, TX 75001", "100 Main Street, North Road, TX 75001",
    "100 Main Street, East Place, TX 75001", ", West Lake, TX 75001",
])
def test_address_normalization_is_idempotent(value):
    normalized = normalize_address(value)
    assert normalize_address(normalized) == normalized


@pytest.mark.parametrize("normalizer", [normalize_company_name, normalize_address])
@pytest.mark.parametrize("value", [None, 123])
def test_non_string_input_is_rejected(normalizer, value):
    with pytest.raises(TypeError, match="requires a string"):
        normalizer(value)
