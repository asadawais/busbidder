"""The traveller's request: hard constraints plus soft preferences.

Hard constraints (an offer that violates one is removed before ranking):
    destination, travel window, maximum ticket price, seats for all passengers,
    maximum transfers, changeable ticket, refundable ticket, minimum checked
    bags, maximum journey duration.

Soft preferences (only influence the score, never remove an offer):
    preferred departure time window and the criterion weights.

The split follows one rule: a hard constraint is something the traveller
*cannot* accept being violated (cannot pay more, cannot travel another day,
needs to be able to change the ticket). Anything that is "nicer if" is a
preference and is traded off in the score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import List, Optional

from .exceptions import InvalidRequestError
from .validation import CODE_RE, MAX_LUGGAGE_BAGS, MAX_TRANSFERS

MINUTES_PER_DAY = 24 * 60

# Named departure windows. "night" wraps past midnight.
WINDOW_PRESETS = {
    "early": ("05:00", "08:00"),
    "morning": ("06:00", "12:00"),
    "afternoon": ("12:00", "17:00"),
    "evening": ("17:00", "21:00"),
    "night": ("21:00", "02:00"),
}

# Custom windows such as "08:00-10:30" or "7:30 - 9:00".
WINDOW_RE = re.compile(r"^(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})$")

MAX_PASSENGERS = 9


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _parse_clock(text: str) -> time:
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise InvalidRequestError(f"Invalid clock time: {text!r}") from None


@dataclass(frozen=True)
class TimeWindow:
    """A preferred departure window. ``end`` before ``start`` wraps midnight."""

    start: time
    end: time
    label: str

    @classmethod
    def parse(cls, text: str) -> "TimeWindow":
        key = text.strip().lower()
        if key in WINDOW_PRESETS:
            start, end = WINDOW_PRESETS[key]
            return cls(_parse_clock(start), _parse_clock(end), key)
        match = WINDOW_RE.match(text.strip())
        if not match:
            presets = ", ".join(WINDOW_PRESETS)
            raise InvalidRequestError(
                f"Departure window {text!r} must be one of {presets} or HH:MM-HH:MM"
            )
        start = _parse_clock(match.group("start"))
        end = _parse_clock(match.group("end"))
        if start == end:
            raise InvalidRequestError("Departure window start and end cannot be equal")
        return cls(start, end, f"{start:%H:%M}-{end:%H:%M}")

    @property
    def wraps_midnight(self) -> bool:
        return self.end < self.start

    def contains(self, t: time) -> bool:
        if self.wraps_midnight:
            return t >= self.start or t <= self.end
        return self.start <= t <= self.end

    def minutes_outside(self, t: time) -> int:
        """0 inside the window, otherwise minutes to the nearest edge.

        Distances are measured around the clock, so 23:30 is 30 minutes
        from a window starting at 00:00, not 23.5 hours.
        """
        if self.contains(t):
            return 0
        m = _minutes(t)

        def clock_distance(a: int, b: int) -> int:
            d = abs(a - b) % MINUTES_PER_DAY
            return min(d, MINUTES_PER_DAY - d)

        return min(clock_distance(m, _minutes(self.start)),
                   clock_distance(m, _minutes(self.end)))

    def __str__(self) -> str:
        span = f"{self.start:%H:%M}-{self.end:%H:%M}"
        return span if self.label == span else f"{self.label} ({span})"


@dataclass(frozen=True)
class TravelRequest:
    destination: str
    earliest_date: date
    latest_date: date
    max_price_eur: Optional[float] = None
    preferred_departure: Optional[TimeWindow] = None
    max_transfers: Optional[int] = None
    require_changeable: bool = False
    require_refundable: bool = False
    min_luggage_bags: Optional[int] = None
    max_duration_min: Optional[int] = None
    passengers: int = 1

    def __post_init__(self) -> None:
        if not CODE_RE.match(self.destination):
            raise InvalidRequestError(f"Destination code {self.destination!r} is not valid")
        if self.latest_date < self.earliest_date:
            raise InvalidRequestError("The travel window ends before it starts")
        if self.max_price_eur is not None and self.max_price_eur <= 0:
            raise InvalidRequestError("Maximum price must be positive")
        if self.max_transfers is not None and not 0 <= self.max_transfers <= MAX_TRANSFERS:
            raise InvalidRequestError(f"Maximum transfers must be between 0 and {MAX_TRANSFERS}")
        if self.min_luggage_bags is not None and not 0 <= self.min_luggage_bags <= MAX_LUGGAGE_BAGS:
            raise InvalidRequestError(f"Luggage must be between 0 and {MAX_LUGGAGE_BAGS} bags")
        if self.max_duration_min is not None and self.max_duration_min <= 0:
            raise InvalidRequestError("Maximum duration must be positive")
        if not 1 <= self.passengers <= MAX_PASSENGERS:
            raise InvalidRequestError(f"Passengers must be between 1 and {MAX_PASSENGERS}")

    # --- descriptions shown to the user -------------------------------------

    def hard_constraint_lines(self, destination_name: Optional[str] = None) -> List[str]:
        dest = destination_name or self.destination
        if self.earliest_date == self.latest_date:
            window = f"{self.earliest_date:%d %b %Y}"
        else:
            window = f"{self.earliest_date:%d %b} to {self.latest_date:%d %b %Y}"
        lines = [f"Destination: Madrid to {dest}", f"Travel date: {window}",
                 f"Seats needed: {self.passengers}"]
        if self.max_price_eur is not None:
            lines.append(f"Maximum price per ticket: EUR {self.max_price_eur:.2f}")
        if self.max_transfers is not None:
            lines.append(f"Maximum transfers: {self.max_transfers}")
        if self.require_changeable:
            lines.append("Ticket must be changeable")
        if self.require_refundable:
            lines.append("Ticket must be refundable")
        if self.min_luggage_bags is not None:
            lines.append(f"At least {self.min_luggage_bags} checked bag(s)")
        if self.max_duration_min is not None:
            hours, minutes = divmod(self.max_duration_min, 60)
            lines.append(f"Maximum journey time: {hours} h {minutes:02d} min")
        return lines

    def preference_lines(self) -> List[str]:
        if self.preferred_departure is None:
            return ["Preferred departure: none (all departure times score equally)"]
        return [f"Preferred departure: {self.preferred_departure}"]
