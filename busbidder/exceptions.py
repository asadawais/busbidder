"""Custom exceptions.

All errors in the app inherit from BusBidderError, so the CLI can catch that
one class and show a readable message instead of a traceback.

Record errors (RecordError and its subclasses) store the field, the bad value
and a message, so the loader can write the exact reason to the log and the
quarantine files. The subclass decides what happens to the record. Most are
rejected, but SuspiciousRecordError is put aside for review.
"""

from __future__ import annotations

from typing import Any


class BusBidderError(Exception):
    """Base class for all application errors."""


# Files and configuration


class DataFileError(BusBidderError):
    """A data file is missing, unreadable or has the wrong structure."""


class ConfigurationError(BusBidderError):
    """Invalid configuration, for example weights that do not sum to 1.0."""


class InvalidRequestError(BusBidderError):
    """A travel request from the user is invalid (bad dates, unknown city...)."""


# Record validation


class RecordError(BusBidderError):
    """A single data record failed validation."""

    def __init__(self, field: str, value: Any, message: str) -> None:
        self.field = field
        self.value = value
        self.message = message
        super().__init__(f"{field}={value!r}: {message}")


class MissingFieldError(RecordError):
    """A required field is empty or absent."""


class InvalidValueError(RecordError):
    """A value cannot be parsed or is outside its allowed range."""


class UnknownReferenceError(RecordError):
    """A record refers to a company or destination that does not exist."""


class InconsistentRecordError(RecordError):
    """Fields contradict each other, e.g. arrival before departure."""


class DuplicateRecordError(RecordError):
    """The record's identifier has already been loaded."""


class SuspiciousRecordError(RecordError):
    """The record is possible but implausible and needs human review."""


# Booking


class BookingError(BusBidderError):
    """A booking could not be created or updated."""


class SeatUnavailableError(BookingError):
    """The selected offer has no seats left."""


class BookingNotFoundError(BookingError):
    """No booking exists with the given reference."""
