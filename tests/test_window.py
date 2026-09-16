"""Tests for windows of analyses: listing selection, the frame cache and the
series file."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from pytest_httpx import HTTPXMock

from jma_radar.client import JmaTileClient, tile_url
from jma_radar.constants import TARGET_TIMES_URL_TEMPLATE
from jma_radar.io import to_series_dataset, write_netcdf
from jma_radar.tiles import domain_tile_range
from jma_radar.times import TargetTime, parse_target_times
from jma_radar.window import GridSpec, fetch_window, parse_start, window_target_times


def _entries(fixtures_dir: Path) -> list[TargetTime]:
    return parse_target_times((fixtures_dir / "targetTimes_N1.json").read_bytes())


def test_parse_start_accepts_run_hours_and_stamps() -> None:
    expected = dt.datetime(2026, 9, 15, 23, tzinfo=dt.UTC)
    assert parse_start("2026091523") == expected
    assert parse_start("202609152300") == expected
    assert parse_start("20260915230000") == expected
    assert parse_start(dt.datetime(2026, 9, 15, 23)) == expected
    assert parse_start(expected) == expected


def test_window_target_times_selects_the_analyses_in_range(fixtures_dir: Path) -> None:
    entries = _entries(fixtures_dir)
    # The fixture lists 22:10 through 01:10 the next day, newest first.
    chosen = window_target_times(entries, parse_start("2026091523"), 3)
    assert chosen[0].validtime == "20260915230000"
    assert chosen[-1].validtime == "20260916011000"
    assert [entry.validtime for entry in chosen] == sorted(entry.validtime for entry in chosen)
    assert len(chosen) == 27
    # A window past the listing's end takes what is there; one before it,
    # nothing.
    assert len(window_target_times(entries, parse_start("2026091601"), 3)) == 3
    assert window_target_times(entries, parse_start("2026091510"), 3) == []
    with pytest.raises(ValueError):
        window_target_times(entries, parse_start("2026091523"), 0)


def test_window_target_times_skips_forecasts_and_other_elements() -> None:
    analysis = TargetTime("20260916010000", "20260916010000", ("hrpns", "hrpns_nd"))
    forecast = TargetTime("20260916010000", "20260916013000", ("hrpns",))
    other = TargetTime("20260916010500", "20260916010500", ("thns",))
    chosen = window_target_times([forecast, other, analysis], parse_start("2026091601"), 1)
    assert chosen == [analysis]


def test_grid_spec_key_is_stable_and_distinct() -> None:
    a = GridSpec(zoom=8, dlon=0.01, dlat=0.01, bbox=(121.0, 20.5, 149.0, 45.5), method="max")
    same = GridSpec(zoom=8, dlon=0.01, dlat=0.01, bbox=(121.0, 20.5, 149.0, 45.5), method="max")
    nearest = GridSpec(
        zoom=8, dlon=0.01, dlat=0.01, bbox=(121.0, 20.5, 149.0, 45.5), method="nearest"
    )
    assert a.key == same.key
    assert a.key != nearest.key
    assert len(a.key) == 10


def _mock_window(httpx_mock: HTTPXMock, fixtures_dir: Path, validtimes: list[str]) -> None:
    listing = [
        {"basetime": validtime, "validtime": validtime, "elements": ["hrpns", "hrpns_nd"]}
        for validtime in reversed(validtimes)
    ]
    httpx_mock.add_response(
        url=TARGET_TIMES_URL_TEMPLATE.format(kind="N1"), content=json.dumps(listing).encode()
    )
    tile = (fixtures_dir / "hrpns" / "20260916010500_z4_x13_y6.png").read_bytes()
    for validtime in validtimes:
        for x, y in domain_tile_range(4):
            httpx_mock.add_response(url=tile_url(validtime, validtime, 4, x, y), content=tile)


def test_fetch_window_caches_frames_and_reuses_them(
    httpx_mock: HTTPXMock, fixtures_dir: Path, tmp_path: Path
) -> None:
    validtimes = ["20260916010000", "20260916010500", "20260916011000"]
    _mock_window(httpx_mock, fixtures_dir, validtimes)
    frames_dir = tmp_path / "frames"
    with JmaTileClient() as client:
        frames, spec = fetch_window(
            "2026091601",
            1,
            zoom=4,
            step=0.5,
            bbox=(130.0, 30.0, 140.0, 40.0),
            method="max",
            frames_dir=frames_dir,
            client=client,
        )
    assert [frame.validtime for frame in frames] == validtimes
    assert spec == GridSpec(
        zoom=4, dlon=0.5, dlat=0.5, bbox=(130.0, 30.0, 140.0, 40.0), method="max"
    )
    assert all(frame.grid.shape == (20, 20) for frame in frames)
    cached = sorted(path.name for path in (frames_dir / spec.key).glob("*.nc"))
    assert cached == [f"hrpns_{validtime}.nc" for validtime in validtimes]
    assert json.loads((frames_dir / spec.key / "grid.json").read_text())["method"] == "max"

    # A second window overlapping the first fetches only the listing, not the
    # tiles: every frame comes off the cache.
    httpx_mock.reset()
    listing = [
        {"basetime": validtime, "validtime": validtime, "elements": ["hrpns"]}
        for validtime in validtimes
    ]
    httpx_mock.add_response(
        url=TARGET_TIMES_URL_TEMPLATE.format(kind="N1"), content=json.dumps(listing).encode()
    )
    with JmaTileClient() as client:
        again, _ = fetch_window(
            "2026091601",
            1,
            zoom=4,
            step=0.5,
            bbox=(130.0, 30.0, 140.0, 40.0),
            method="max",
            frames_dir=frames_dir,
            client=client,
        )
    assert len(httpx_mock.get_requests()) == 1
    for before, after in zip(frames, again, strict=True):
        np.testing.assert_array_equal(before.grid.levels, after.grid.levels)
        np.testing.assert_array_equal(before.grid.lat, after.grid.lat)


def test_fetch_window_without_cache_fetches_every_frame(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    validtimes = ["20260916010000", "20260916010500"]
    _mock_window(httpx_mock, fixtures_dir, validtimes)
    frames, _ = fetch_window("2026091601", 1, zoom=4)
    assert [frame.path for frame in frames] == [None, None]
    assert len(httpx_mock.get_requests()) == 1 + 2 * domain_tile_range(4).count


def test_fetch_window_rejects_bad_steps() -> None:
    with pytest.raises(ValueError, match="step"):
        fetch_window("2026091601", 1, zoom=4, step=-0.1)
    with pytest.raises(ValueError, match="zoom"):
        fetch_window("2026091601", 1, zoom=5)


def test_series_file_round_trip(httpx_mock: HTTPXMock, fixtures_dir: Path, tmp_path: Path) -> None:
    validtimes = ["20260916010500", "20260916010000"]
    _mock_window(httpx_mock, fixtures_dir, validtimes)
    frames, _ = fetch_window("2026091601", 1, zoom=4, step=0.5, bbox=(130.0, 30.0, 140.0, 40.0))
    dataset = to_series_dataset([(frame.grid, frame.validtime) for frame in frames], zoom=4)
    # Sorted by time whatever order the frames came in.
    assert list(dataset["time"].values.astype("datetime64[s]").astype(str)) == [
        "2026-09-16T01:00:00",
        "2026-09-16T01:05:00",
    ]
    assert dataset["rain_rate"].dims == ("time", "lat", "lon")
    assert dataset["rain_rate"].attrs["units"] == "mm/h"
    assert dataset.attrs["frame_count"] == 2
    path = write_netcdf(dataset, tmp_path / "series.nc")
    with xr.open_dataset(path) as loaded:
        np.testing.assert_allclose(loaded["rain_rate"].values, dataset["rain_rate"].values)
        assert loaded["level"].dtype == np.uint8
        assert loaded["time"].encoding["units"] == "seconds since 1970-01-01T00:00:00+00:00"
    # The packing is the byte the reader unscales: representative values are
    # whole half-millimetres, no data is 255.
    with xr.open_dataset(path, mask_and_scale=False) as raw:
        assert raw["rain_rate"].dtype == np.uint8
        assert raw["rain_rate"].attrs["scale_factor"] == 0.5
        assert raw["rain_rate"].attrs["_FillValue"] == 255
        levels = raw["level"].values
        packed = raw["rain_rate"].values
        assert (packed[levels == 0] == 255).all()
        assert (packed[levels == 1] == 0).all()
        assert (packed[levels == 3] == 6).all()


def test_series_rejects_mismatched_grids_and_duplicate_times() -> None:
    from jma_radar.mosaic import LatLonGrid

    a = LatLonGrid(lat=np.array([1.0]), lon=np.array([1.0]), levels=np.zeros((1, 1), np.uint8))
    b = LatLonGrid(lat=np.array([2.0]), lon=np.array([1.0]), levels=np.zeros((1, 1), np.uint8))
    with pytest.raises(ValueError, match="same grid"):
        to_series_dataset([(a, "20260916010000"), (b, "20260916010500")], zoom=4)
    with pytest.raises(ValueError, match="two frames"):
        to_series_dataset([(a, "20260916010000"), (a, "20260916010000")], zoom=4)
    with pytest.raises(ValueError, match="at least one"):
        to_series_dataset([], zoom=4)
    with pytest.raises(ValueError, match="variable name"):
        to_series_dataset([(a, "20260916010000")], zoom=4, variable="rain rate")


def test_series_variable_can_be_named() -> None:
    from jma_radar.mosaic import LatLonGrid

    grid = LatLonGrid(lat=np.array([1.0]), lon=np.array([1.0]), levels=np.array([[3]], np.uint8))
    dataset = to_series_dataset([(grid, "20260916010000")], zoom=4, variable="prate")
    assert set(dataset.data_vars) == {"prate", "level"}
    assert dataset["prate"].attrs["units"] == "mm/h"
    assert dataset["prate"].encoding["scale_factor"] == 0.5
