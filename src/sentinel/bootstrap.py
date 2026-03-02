from __future__ import annotations

from sentinel.utils import logs


def initialise() -> None:
    logs.configure_logging()
