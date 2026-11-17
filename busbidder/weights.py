"""Criterion weights and how they are checked.

- Every name has to be a registered criterion.
- No weight can be negative.
- The four core criteria (price, duration, departure, flexibility) each need
  a weight above zero, so every ranking uses at least four criteria.
- The weights must add up to 1.0. Percentages that add up to 100 are also
  accepted and converted. Anything else gives an error instead of being
  rescaled, so a typo can't change the ranking without anyone noticing.
- One weight above 0.5 is allowed but gives a warning, since that criterion
  can then outvote all the others together.
"""

from __future__ import annotations

import math
import re
from typing import Dict, Iterator, List, Mapping

from .criteria import CORE_CRITERIA, criterion_names
from .exceptions import ConfigurationError

SUM_TOLERANCE = 0.001
DOMINANCE_WARNING = 0.5

# One "name=value" pair, e.g. "price=40" or "flexibility = 0.2"
WEIGHT_PAIR_RE = re.compile(r"^\s*(?P<name>[a-z_]+)\s*=\s*(?P<value>-?\d+(?:\.\d+)?)\s*$")

DEFAULT_WEIGHTS = {"price": 0.40, "duration": 0.25, "departure": 0.15, "flexibility": 0.20}


class Weights(Mapping[str, float]):
    """An immutable, validated mapping from criterion name to weight."""

    def __init__(self, values: Mapping[str, float]) -> None:
        self._values = self._validate(dict(values))
        self.warnings: List[str] = [
            f"{name} has {weight:.0%} of the total weight and can outvote all other criteria"
            for name, weight in self._values.items() if weight > DOMINANCE_WARNING
        ]

    @staticmethod
    def _validate(values: Dict[str, float]) -> Dict[str, float]:
        known = set(criterion_names())
        unknown = sorted(set(values) - known)
        if unknown:
            raise ConfigurationError(
                f"Unknown criterion(s): {', '.join(unknown)}. Known: {', '.join(sorted(known))}"
            )
        for name, value in values.items():
            if value < 0:
                raise ConfigurationError(f"Weight for {name} cannot be negative")
        missing = [name for name in CORE_CRITERIA if values.get(name, 0) <= 0]
        if missing:
            raise ConfigurationError(
                f"Core criteria need a weight above zero: {', '.join(missing)}"
            )
        total = math.fsum(values.values())
        if abs(total - 100) <= 100 * SUM_TOLERANCE:
            values = {name: value / 100 for name, value in values.items()}
            total = math.fsum(values.values())
        if abs(total - 1.0) > SUM_TOLERANCE:
            raise ConfigurationError(
                f"Weights total {total:g}; they must total 1.0 (or 100 as percentages)"
            )
        return values

    @classmethod
    def parse(cls, text: str) -> "Weights":
        """Parse "price=40,duration=25,departure=15,flexibility=20"."""
        values: Dict[str, float] = {}
        for part in text.split(","):
            if not part.strip():
                continue
            match = WEIGHT_PAIR_RE.match(part)
            if not match:
                raise ConfigurationError(f"Cannot read weight {part.strip()!r}; use name=value")
            name = match.group("name")
            if name in values:
                raise ConfigurationError(f"Weight for {name} is given twice")
            values[name] = float(match.group("value"))
        return cls(values)

    @classmethod
    def default(cls) -> "Weights":
        return cls(DEFAULT_WEIGHTS)

    def __getitem__(self, name: str) -> float:
        return self._values[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Weights({self.describe()})"

    def describe(self) -> str:
        return ", ".join(f"{name} {weight:.0%}" for name, weight in self._values.items())
