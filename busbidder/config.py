"""Central paths and constants.

Keeping file locations in one place means the loader, CLI and tests never
hard-code paths, and a different data folder can be passed in when needed.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
COMPANIES_FILE = DATA_DIR / "companies.json"
DESTINATIONS_FILE = DATA_DIR / "destinations.csv"
OFFERS_FILE = DATA_DIR / "offers.csv"
INJECTED_FAULTS_FILE = DATA_DIR / "injected_faults.json"

OUTPUT_DIR = PROJECT_ROOT / "output"
QUARANTINE_DIR = OUTPUT_DIR / "quarantine"

LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILE = LOG_DIR / "busbidder.log"

# All journeys depart from the Madrid hub.
ORIGIN = "Madrid"

# Customer ratings use a 1.0 to 5.0 scale (inclusive).
RATING_MIN = 1.0
RATING_MAX = 5.0

# Thresholds that do not make a record impossible but suspicious enough that a
# human should look at it before it is offered to travellers.
MAX_PLAUSIBLE_SPEED_KMH = 110.0
MAX_PLAUSIBLE_PRICE_EUR = 250.0

# Tolerance when comparing the stated duration with the departure/arrival times.
DURATION_TOLERANCE_MIN = 1

DEFAULT_SEED = 42
