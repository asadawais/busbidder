"""BusBidder Spain: a bus-offer filtering, ranking and booking marketplace."""

import logging

__version__ = "1.0.0"

# No log output unless the app sets up logging (see logging_setup).
logging.getLogger(__name__).addHandler(logging.NullHandler())
