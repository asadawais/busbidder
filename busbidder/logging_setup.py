"""Logging configuration.

All modules log through ``logging.getLogger(__name__)``. The application calls
``configure_logging`` once at start-up, which sends everything from INFO and
up to a log file. Only errors are echoed to the console, because the CLI
prints its own readable summaries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import config

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(log_file: Optional[Path] = None,
                      file_level: int = logging.INFO,
                      console_level: int = logging.ERROR) -> logging.Logger:
    log_file = Path(log_file) if log_file else config.LOG_FILE
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("busbidder")
    root.setLevel(logging.DEBUG)
    # Remove handlers from an earlier call so messages are not duplicated.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(console)

    root.propagate = False
    return root
