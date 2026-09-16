"""Tests for the command line interface."""

from __future__ import annotations

from pathlib import Path

import pytest
import xarray as xr
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from jma_radar.cli import _parse_bbox, _parse_zr, app
from jma_radar.client import tile_url
from jma_radar.constants import TARGET_TIMES_URL_TEMPLATE
from jma_radar.tiles import domain_tile_range

runner = CliRunner()
BASETIME = "20260916011000"


def test_parse_helpers() -> None:
    assert _parse_bbox("130,30,140,40") == (130.0, 30.0, 140.0, 40.0)
    assert _parse_bbox(None) is None
    assert _parse_zr("200,1.6") == (200.0, 1.6)
    for bad in ("130,30,140", "a,b,c,d"):
        with pytest.raises(Exception, match="bbox"):
            _parse_bbox(bad)
    for bad_zr in ("200", "a,b"):
        with pytest.raises(Exception, match="zr"):
            _parse_zr(bad_zr)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_times(httpx_mock: HTTPXMock, fixtures_dir: Path) -> None:
    httpx_mock.add_response(
        url=TARGET_TIMES_URL_TEMPLATE.format(kind="N1"),
        content=(fixtures_dir / "targetTimes_N1.json").read_bytes(),
    )
    result = runner.invoke(app, ["times", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert "20260916011000" in result.output
    assert len(result.output.strip().splitlines()) == 4  # header + 3 rows


def test_times_rejects_bad_kind() -> None:
    result = runner.invoke(app, ["times", "--kind", "N9"])
    assert result.exit_code != 0


def test_fetch_rejects_bad_zoom() -> None:
    assert runner.invoke(app, ["fetch", "--zoom", "5"]).exit_code != 0
    assert runner.invoke(app, ["fetch", "--zoom", "4", "--format", "jpeg"]).exit_code != 0


def _mock_frame(httpx_mock: HTTPXMock, fixtures_dir: Path, zoom: int) -> None:
    httpx_mock.add_response(
        url=TARGET_TIMES_URL_TEMPLATE.format(kind="N1"),
        content=(fixtures_dir / "targetTimes_N1.json").read_bytes(),
    )
    tile = (fixtures_dir / "hrpns" / "20260916010500_z4_x13_y6.png").read_bytes()
    tile_range = domain_tile_range(zoom)
    for x, y in tile_range:
        httpx_mock.add_response(url=tile_url(BASETIME, BASETIME, zoom, x, y), content=tile)


def test_fetch_netcdf(httpx_mock: HTTPXMock, fixtures_dir: Path, tmp_path: Path) -> None:
    _mock_frame(httpx_mock, fixtures_dir, 4)
    out = tmp_path / "hrpns.nc"
    result = runner.invoke(
        app, ["fetch", "--zoom", "4", "--out", str(out), "--bbox", "130,30,140,40"]
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    with xr.open_dataset(out) as dataset:
        assert set(dataset.data_vars) == {"level", "rain_rate"}
        assert dataset.attrs["zoom"] == 4
        assert dataset.attrs["basetime"] == "2026-09-16T01:10:00Z"
        assert dataset["lat"].values[0] > dataset["lat"].values[-1]
        assert float(dataset["lon"].values[0]) > 130.0
        assert float(dataset["lon"].values[-1]) < 140.0


def test_fetch_png(httpx_mock: HTTPXMock, fixtures_dir: Path, tmp_path: Path) -> None:
    _mock_frame(httpx_mock, fixtures_dir, 4)
    out = tmp_path / "preview.png"
    result = runner.invoke(app, ["fetch", "--zoom", "4", "--format", "png", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.is_file()


def test_fetch_netcdf_with_dbz(httpx_mock: HTTPXMock, fixtures_dir: Path, tmp_path: Path) -> None:
    _mock_frame(httpx_mock, fixtures_dir, 4)
    out = tmp_path / "hrpns_dbz.nc"
    result = runner.invoke(app, ["fetch", "--zoom", "4", "--out", str(out), "--dbz"])
    assert result.exit_code == 0, result.output
    with xr.open_dataset(out) as dataset:
        assert set(dataset.data_vars) == {"cref", "level", "rain_rate"}
        assert dataset["cref"].attrs["units"] == "dBZ"
