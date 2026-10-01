"""Validated matching policy using the standard library's TOML reader."""

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class MatchingPolicy:
    version: str = "2"
    name_match_threshold: float = 0.90
    name_conflict_threshold: float = 0.65
    ambiguity_name_threshold: float = 0.75
    name_weight: float = 70.0
    address_weight: float = 30.0
    accepted_entity_statuses: tuple[str, ...] = ("active",)
    allowed_name_variations: tuple[tuple[str, str], ...] = (
        ("property", "properties"), ("holding", "holdings"),
    )
    generic_name_tokens: tuple[str, ...] = (
        "medical", "healthcare", "health", "holdings", "holding", "properties",
        "property", "group", "investment", "investments", "commercial", "assets", "retail",
    )

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a nonblank string")
        for name in ("name_match_threshold", "name_conflict_threshold", "ambiguity_name_threshold"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be a finite number between 0 and 1")
        if not self.name_conflict_threshold < self.ambiguity_name_threshold <= self.name_match_threshold:
            raise ValueError("thresholds must satisfy conflict < ambiguity <= match")
        for name in ("name_weight", "address_weight"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be a finite number between 0 and 100")
        if not math.isclose(self.name_weight + self.address_weight, 100, rel_tol=0, abs_tol=1e-9):
            raise ValueError("name_weight and address_weight must sum to 100")
        for name in ("accepted_entity_statuses", "generic_name_tokens"):
            values = getattr(self, name)
            if not isinstance(values, (list, tuple)) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise ValueError(f"{name} must be an array of nonblank strings")
            normalized = tuple(sorted({value.strip().casefold() for value in values}))
            if name == "accepted_entity_statuses" and not normalized:
                raise ValueError("accepted_entity_statuses must not be empty")
            object.__setattr__(self, name, normalized)
        if not isinstance(self.allowed_name_variations, (list, tuple)):
            raise ValueError("allowed_name_variations must be an array of two-word pairs")
        pairs = []
        for pair in self.allowed_name_variations:
            if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                    or any(not isinstance(word, str) or not word.strip().isalpha() for word in pair)):
                raise ValueError("allowed_name_variations must contain pairs of single alphabetic words")
            normalized_pair = tuple(sorted(word.strip().casefold() for word in pair))
            if normalized_pair[0] == normalized_pair[1]:
                raise ValueError("allowed_name_variations must pair different words")
            pairs.append(normalized_pair)
        object.__setattr__(self, "allowed_name_variations", tuple(sorted(set(pairs))))

    @property
    def fingerprint(self) -> str:
        """Identify actual policy values, including edits without a version bump."""
        values = asdict(self)
        for name in ("name_match_threshold", "name_conflict_threshold", "ambiguity_name_threshold",
                     "name_weight", "address_weight"):
            values[name] = float(values[name])
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest()


def load_policy(path: str | Path) -> MatchingPolicy:
    with Path(path).open("rb") as stream:
        values = tomllib.load(stream)
    unknown = set(values) - {item.name for item in fields(MatchingPolicy)}
    if unknown:
        raise ValueError(f"Unknown matching policy fields: {', '.join(sorted(unknown))}")
    return MatchingPolicy(**values)
