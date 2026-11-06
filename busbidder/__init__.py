"""BusBidder Spain: a bus-offer filtering, ranking and booking marketplace."""

import logging

__version__ = "1.0.0"

# Stay silent unless the application configures logging (see logging_setup).
logging.getLogger(__name__).addHandler(logging.NullHandler())
