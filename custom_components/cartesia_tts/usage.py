"""Tracking of Cartesia credit consumption.

Two sources feed the same counter:

* a local tally of the characters this integration sends, which needs no extra
  credentials (Cartesia bills roughly one credit per character), and
* the real figures from ``GET /usage/credits``, which are exact and include
  usage from outside Home Assistant but require an admin API key.

The API figure wins whenever it is available. Cartesia exposes no remaining
balance, so "remaining" is always the configured allowance minus consumption.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


def current_period(moment: datetime | None = None) -> str:
    """Return the ``YYYY-MM`` bucket a moment belongs to, in local time."""
    return (moment or dt_util.now()).strftime("%Y-%m")


def period_start(period: str) -> datetime:
    """Return the first instant of a ``YYYY-MM`` bucket, in local time."""
    year, month = (int(part) for part in period.split("-"))
    return dt_util.start_of_local_day(
        datetime(year, month, 1, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    )


class UsageTracker:
    """Holds this month's consumption and notifies whoever displays it."""

    def __init__(self, allowance: int | None) -> None:
        """Initialize an empty tracker for the current month."""
        self.allowance = allowance
        self.period = current_period()
        self.local_used = 0
        self.api_used: int | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def used(self) -> int:
        """Return the best available consumption figure."""
        return self.api_used if self.api_used is not None else self.local_used

    @property
    def source(self) -> str:
        """Return where the current figure comes from."""
        return "api" if self.api_used is not None else "local"

    @property
    def remaining(self) -> int | None:
        """Return credits left, or None when no allowance is configured."""
        if not self.allowance:
            return None
        return max(self.allowance - self.used, 0)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired whenever the numbers change."""
        self._listeners.append(listener)

        def remove() -> None:
            self._listeners.remove(listener)

        return remove

    @callback
    def async_restore(self, used: int, period: str) -> None:
        """Seed the local counter from a restored sensor state."""
        if period == current_period():
            self.period = period
            self.local_used = used

    @callback
    def async_add_characters(self, characters: int) -> None:
        """Count a synthesis request against the local tally."""
        if characters <= 0:
            return
        self._roll_over()
        self.local_used += characters
        self._notify()

    @callback
    def async_set_api_used(self, used: int) -> None:
        """Store the figure read from the usage API."""
        self._roll_over()
        self.api_used = used
        self._notify()

    @callback
    def _roll_over(self) -> None:
        """Start a new bucket when the month changed."""
        if (period := current_period()) != self.period:
            _LOGGER.debug(
                "Cartesia usage period rolled from %s to %s", self.period, period
            )
            self.period = period
            self.local_used = 0
            self.api_used = None

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
