"""Access to the JMA ``targetTimes`` listings."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["TargetTime", "TimeKind", "latest", "parse_target_times", "parse_time"]

TimeKind = Literal["N1", "N2", "N3"]

_TIME_FORMAT = "%Y%m%d%H%M%S"


def parse_time(value: str) -> dt.datetime:
    """Parse a JMA ``YYYYMMDDHHMMSS`` timestamp as an aware UTC datetime."""
    return dt.datetime.strptime(value, _TIME_FORMAT).replace(tzinfo=dt.UTC)


def format_time(value: dt.datetime) -> str:
    """Format a datetime as a JMA ``YYYYMMDDHHMMSS`` UTC timestamp."""
    return value.astimezone(dt.UTC).strftime(_TIME_FORMAT)


@dataclass(frozen=True, slots=True)
class TargetTime:
    """One entry of a ``targetTimes_*.json`` document."""

    basetime: str
    validtime: str
    elements: tuple[str, ...]

    @property
    def basetime_dt(self) -> dt.datetime:
        """``basetime`` as an aware UTC datetime."""
        return parse_time(self.basetime)

    @property
    def validtime_dt(self) -> dt.datetime:
        """``validtime`` as an aware UTC datetime."""
        return parse_time(self.validtime)

    @property
    def is_forecast(self) -> bool:
        """``True`` when the entry is a forecast (``validtime > basetime``)."""
        return self.validtime > self.basetime

    @property
    def lead_minutes(self) -> int:
        """Forecast lead time in minutes (0 for analyses)."""
        return int((self.validtime_dt - self.basetime_dt).total_seconds() // 60)

    def has_element(self, element: str) -> bool:
        """Whether the entry advertises ``element``."""
        return element in self.elements


def _entry_from_json(item: dict[str, Any]) -> TargetTime:
    return TargetTime(
        basetime=str(item["basetime"]),
        validtime=str(item["validtime"]),
        elements=tuple(str(element) for element in item.get("elements", ())),
    )


def parse_target_times(payload: str | bytes) -> list[TargetTime]:
    """Parse a ``targetTimes_*.json`` document into :class:`TargetTime` entries."""
    data = json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("targetTimes payload must be a JSON array")
    return [_entry_from_json(item) for item in data]


def latest(entries: list[TargetTime], element: str | None = None) -> TargetTime:
    """Return the most recent entry, optionally restricted to ``element``.

    ``targetTimes`` files are sorted newest first, but this function sorts
    explicitly so the result does not depend on server ordering.

    Raises:
        ValueError: if no entry matches.
    """
    candidates = [e for e in entries if element is None or e.has_element(element)]
    if not candidates:
        raise ValueError(f"no targetTimes entry for element {element!r}")
    return max(candidates, key=lambda e: (e.basetime, e.validtime))
