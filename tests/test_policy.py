from dataclasses import replace
from pathlib import Path

import pytest

from net_lease_ownership.policy import MatchingPolicy, load_policy


def test_checked_in_policy_matches_defaults():
    path = Path(__file__).resolve().parents[1] / "config/matching.toml"
    assert load_policy(path) == MatchingPolicy()


@pytest.mark.parametrize("changes", [
    {"name_match_threshold": 1.1}, {"name_conflict_threshold": -0.1},
    {"ambiguity_name_threshold": float("nan")}, {"name_match_threshold": True},
    {"name_conflict_threshold": "0.65"}, {"name_match_threshold": 0.60},
    {"ambiguity_name_threshold": 0.95}, {"ambiguity_name_threshold": 0.65},
    {"name_weight": float("inf")}, {"name_weight": -1}, {"address_weight": True},
    {"name_weight": 80}, {"version": ""}, {"version": 1},
    {"accepted_entity_statuses": []}, {"accepted_entity_statuses": "active"},
    {"accepted_entity_statuses": [""]}, {"generic_name_tokens": [None]},
])
def test_invalid_policy_is_rejected(changes):
    with pytest.raises(ValueError):
        MatchingPolicy(**changes)


def test_policy_lists_are_normalized_and_frozen():
    statuses = [" ACTIVE ", "active", "Good Standing"]
    policy = MatchingPolicy(accepted_entity_statuses=statuses)
    statuses.append("dissolved")
    assert policy.accepted_entity_statuses == ("active", "good standing")


def test_fingerprint_changes_with_actual_settings_without_version_bump():
    policy = MatchingPolicy()
    assert replace(policy, name_match_threshold=0.95).version == policy.version
    assert replace(policy, name_match_threshold=0.95).fingerprint != policy.fingerprint
    assert MatchingPolicy(name_weight=70, address_weight=30).fingerprint == policy.fingerprint


def test_unknown_config_field_is_rejected(tmp_path):
    path = tmp_path / "matching.toml"
    path.write_text("name_match_thresold = 0.99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown matching policy fields"):
        load_policy(path)


def test_partial_config_uses_documented_defaults(tmp_path):
    path = tmp_path / "matching.toml"
    path.write_text("name_match_threshold = 0.95\n", encoding="utf-8")
    assert load_policy(path) == MatchingPolicy(name_match_threshold=0.95)


@pytest.mark.parametrize("variations", ["property", [("property",)], [("property", "properties", "third")],
                                      [("property group", "properties")], [("10", "20")],
                                      [(None, "properties")], [("property", "property")]])
def test_invalid_variation_pairs_are_rejected(variations):
    with pytest.raises(ValueError, match="allowed_name_variations"):
        MatchingPolicy(allowed_name_variations=variations)


def test_variation_pairs_are_canonical_frozen_and_fingerprinted():
    pairs = [[" PROPERTY ", "Properties"]]
    policy = MatchingPolicy(allowed_name_variations=pairs)
    pairs[0][0] = "east"
    assert policy.allowed_name_variations == (("properties", "property"),)
    assert policy.fingerprint == MatchingPolicy(allowed_name_variations=(("properties", "property"),)).fingerprint
    assert policy.fingerprint != MatchingPolicy(allowed_name_variations=()).fingerprint
