"""Traveller profiles from data/profiles.json.

A profile has weights, a preferred departure window and some optional extra
hard constraints. Applying it to a base trip (destination and dates) gives a
full TravelRequest and Weights. Running several profiles on the same trip
shows how the same offers can give different recommendations for different
travellers.

If the trip and the profile set the same constraint, the stricter value wins
(lower price, fewer transfers, more bags, shorter journey). So a profile can
only narrow the trip, never widen it.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from . import config
from .exceptions import BusBidderError, ConfigurationError, DataFileError
from .models import Marketplace
from .ranking import SearchResult, search
from .request import TimeWindow, TravelRequest
from .weights import Weights

PROFILES_FILE = config.DATA_DIR / "profiles.json"

# Constraints a profile can set and how each one is combined with the base trip.
_STRICTER = {
    "max_price_eur": min,
    "max_transfers": min,
    "max_duration_min": min,
    "min_luggage_bags": max,
    "require_changeable": lambda a, b: a or b,
    "require_refundable": lambda a, b: a or b,
}


@dataclass(frozen=True)
class TravellerProfile:
    profile_id: str
    name: str
    description: str
    weights: Weights
    preferred_departure: Optional[TimeWindow]
    constraints: Mapping[str, Any]

    def apply(self, base: TravelRequest) -> TravelRequest:
        changes: Dict[str, Any] = {}
        for key, value in self.constraints.items():
            current = getattr(base, key)
            changes[key] = value if current is None else _STRICTER[key](current, value)
        if self.preferred_departure is not None:
            changes["preferred_departure"] = self.preferred_departure
        return dataclasses.replace(base, **changes)

    def constraint_text(self) -> str:
        if not self.constraints:
            return "none"
        parts = []
        for key, value in self.constraints.items():
            if value is True:
                parts.append(key.replace("require_", "must be "))
            else:
                parts.append(f"{key.replace('_', ' ')} {value}")
        return ", ".join(parts)


def _parse_profile(raw: Mapping[str, Any]) -> TravellerProfile:
    profile_id = raw.get("id")
    if not isinstance(profile_id, str) or not profile_id:
        raise ConfigurationError("Every profile needs a text 'id'")
    try:
        unknown = sorted(set(raw.get("constraints", {})) - set(_STRICTER))
        if unknown:
            raise ConfigurationError(f"unknown constraint(s): {', '.join(unknown)}")
        window_text = raw.get("preferred_departure")
        return TravellerProfile(
            profile_id=profile_id,
            name=str(raw.get("name") or profile_id),
            description=str(raw.get("description") or ""),
            weights=Weights(raw.get("weights") or {}),
            preferred_departure=TimeWindow.parse(window_text) if window_text else None,
            constraints=dict(raw.get("constraints") or {}),
        )
    except BusBidderError as err:
        raise ConfigurationError(f"Profile {profile_id!r}: {err}") from None


def load_profiles(path: Path = PROFILES_FILE) -> Dict[str, TravellerProfile]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise DataFileError(f"Profile file not found: {path}") from None
    except json.JSONDecodeError as err:
        raise DataFileError(f"{Path(path).name} is not valid JSON: {err}") from None
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
        raise DataFileError(f"{Path(path).name}: expected an object with a 'profiles' list")
    profiles: Dict[str, TravellerProfile] = {}
    for raw in data["profiles"]:
        if not isinstance(raw, dict):
            raise ConfigurationError("Each profile must be a JSON object")
        profile = _parse_profile(raw)
        if profile.profile_id in profiles:
            raise ConfigurationError(f"Profile id {profile.profile_id!r} is used twice")
        profiles[profile.profile_id] = profile
    if not profiles:
        raise ConfigurationError("No traveller profiles defined")
    return profiles


@dataclass
class ProfileResult:
    profile: TravellerProfile
    result: SearchResult


def compare_profiles(market: Marketplace, base: TravelRequest,
                     profiles: List[TravellerProfile]) -> List[ProfileResult]:
    """Run the same base trip once per profile."""
    return [ProfileResult(p, search(market, p.apply(base), p.weights)) for p in profiles]


def cross_ranks(results: List[ProfileResult]) -> Dict[str, Dict[str, Optional[int]]]:
    """For each profile's winner, find its rank under every profile (None if removed).

    This shows how much the recommendation depends on who is travelling. An
    offer can win for one traveller and be removed or ranked low for another,
    even though the offers are the same.
    """
    table: Dict[str, Dict[str, Optional[int]]] = {}
    for owner in results:
        best = owner.result.best
        if best is None:
            continue
        row: Dict[str, Optional[int]] = {}
        for other in results:
            ranks = {r.offer.offer_id: r.rank for r in other.result.ranked}
            row[other.profile.profile_id] = ranks.get(best.offer.offer_id)
        table[owner.profile.profile_id] = row
    return table


def select_profiles(profiles: Dict[str, TravellerProfile],
                    ids: Optional[List[str]] = None) -> List[TravellerProfile]:
    if not ids:
        return list(profiles.values())
    missing = [i for i in ids if i not in profiles]
    if missing:
        raise ConfigurationError(f"Unknown profile(s): {', '.join(missing)}. "
                                 f"Available: {', '.join(profiles)}")
    return [profiles[i] for i in ids]
