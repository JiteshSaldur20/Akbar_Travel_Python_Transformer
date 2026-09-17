"""Central logging configuration for the Sabre connector.

Secret hygiene: tokens, passwords, and authorization headers must never
be logged. Log only non-sensitive identifiers and counts.
"""

import logging
import sys

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging once for the service."""
    global _CONFIGURED

    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s - %(message)s"
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger, configuring logging if needed."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
