"""Tests for the targetTimes parsing helpers."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from jma_radar.times import TargetTime, format_time, latest, parse_target_times, parse_time


def test_parse_time_is_utc() -> None:
    parsed = parse_time("20260916010500")
    assert parsed == dt.datetime(2026, 9, 16, 1, 5, tzinfo=dt.UTC)
    assert format_time(parsed) == "20260916010500"


def test_parse_n1(fixtures_dir: Path) -> None:
    entries = parse_target_times((fixtures_dir / "targetTimes_N1.json").read_bytes())
    assert entries
    assert all(entry.basetime == entry.validtime for entry in entries)
    assert all(not entry.is_forecast for entry in entries)
    assert all(entry.has_element("hrpns") for entry in entries)
    newest = latest(entries, element="hrpns")
    assert newest.basetime == "20260916011000"
    assert newest.lead_minutes == 0


def test_parse_n2(fixtures_dir: Path) -> None:
    entries = parse_target_times((fixtures_dir / "targetTimes_N2.json").read_bytes())
    assert entries
    assert all(entry.is_forecast for entry in entries)
    leads = sorted(entry.lead_minutes for entry in entries)
    assert leads[0] == 5
    assert leads[-1] == 60
    assert len({entry.basetime for entry in entries}) == 1


def test_latest_requires_match() -> None:
    entries = [TargetTime("20260916010500", "20260916010500", ("thns",))]
    with pytest.raises(ValueError, match="no targetTimes entry"):
        latest(entries, element="hrpns")


def test_latest_ignores_server_order() -> None:
    entries = [
        TargetTime("20260916010000", "20260916010000", ("hrpns",)),
        TargetTime("20260916011000", "20260916011000", ("hrpns",)),
        TargetTime("20260916010500", "20260916010500", ("hrpns",)),
    ]
    assert latest(entries).basetime == "20260916011000"


def test_parse_rejects_non_array() -> None:
    with pytest.raises(ValueError, match="JSON array"):
        parse_target_times(b'{"basetime": "20260916010500"}')
