"""Tests for the HTTP client, using mocked JMA endpoints."""

from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pytest
from pytest_httpx import HTTPXMock

from jma_radar.client import JmaTileClient, tile_url
from jma_radar.constants import TARGET_TIMES_URL_TEMPLATE
from jma_radar.tiles import TileRange

BASETIME = "20260916010500"


def test_tile_url() -> None:
    assert tile_url(BASETIME, BASETIME, 6, 56, 25) == (
        "https://www.jma.go.jp/bosai/jmatile/data/nowc/20260916010500/none/"
        "20260916010500/surf/hrpns/6/56/25.png"
    )


@pytest.fixture
def tile_png(fixtures_dir: Path) -> bytes:
    return (fixtures_dir / "hrpns" / "20260916010500_z6_x56_y25.png").read_bytes()


def test_fetch_target_times(httpx_mock: HTTPXMock, fixtures_dir: Path) -> None:
    httpx_mock.add_response(
        url=TARGET_TIMES_URL_TEMPLATE.format(kind="N1"),
        content=(fixtures_dir / "targetTimes_N1.json").read_bytes(),
    )
    with JmaTileClient() as client:
        entries = client.fetch_target_times("N1")
    assert entries[0].basetime == "20260916011000"


def test_resolve_target_time_falls_back_to_n2(httpx_mock: HTTPXMock, fixtures_dir: Path) -> None:
    httpx_mock.add_response(
        url=TARGET_TIMES_URL_TEMPLATE.format(kind="N1"),
        content=(fixtures_dir / "targetTimes_N1.json").read_bytes(),
    )
    httpx_mock.add_response(
        url=TARGET_TIMES_URL_TEMPLATE.format(kind="N2"),
        content=(fixtures_dir / "targetTimes_N2.json").read_bytes(),
    )
    with JmaTileClient() as client:
        target = client.resolve_target_time(valid="20260916020500")
    assert target.basetime == "20260916010500"
    assert target.lead_minutes == 60


def test_resolve_target_time_not_found(httpx_mock: HTTPXMock) -> None:
    for kind in ("N1", "N2"):
        httpx_mock.add_response(url=TARGET_TIMES_URL_TEMPLATE.format(kind=kind), content=b"[]")
    with JmaTileClient() as client, pytest.raises(ValueError, match="no targetTimes entry"):
        client.resolve_target_time(time="20200101000000")


def test_user_agent_is_sent(httpx_mock: HTTPXMock, tile_png: bytes) -> None:
    httpx_mock.add_response(url=tile_url(BASETIME, BASETIME, 6, 56, 25), content=tile_png)
    with JmaTileClient() as client:
        client.fetch_tile(BASETIME, BASETIME, 6, 56, 25)
    request = httpx_mock.get_requests()[0]
    assert "jma-radar" in request.headers["User-Agent"]


def test_missing_tile_is_nodata(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=tile_url(BASETIME, BASETIME, 6, 56, 25), status_code=404)
    with JmaTileClient(retries=1) as client:
        levels = client.fetch_tile(BASETIME, BASETIME, 6, 56, 25)
    assert (levels == 0).all()


def test_retry_then_success(httpx_mock: HTTPXMock, tile_png: bytes) -> None:
    url = tile_url(BASETIME, BASETIME, 6, 56, 25)
    httpx_mock.add_response(url=url, status_code=503)
    httpx_mock.add_response(url=url, content=tile_png)
    with JmaTileClient(backoff=0.0) as client:
        levels = client.fetch_tile(BASETIME, BASETIME, 6, 56, 25)
    assert levels.max() == 9
    assert len(httpx_mock.get_requests()) == 2


def test_retry_exhausted_raises(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=tile_url(BASETIME, BASETIME, 6, 56, 25), status_code=500, is_reusable=True
    )
    with JmaTileClient(backoff=0.0, retries=2) as client, pytest.raises(httpx.HTTPStatusError):
        client.fetch_tile(BASETIME, BASETIME, 6, 56, 25)


def test_disk_cache(httpx_mock: HTTPXMock, tile_png: bytes, tmp_path: Path) -> None:
    httpx_mock.add_response(url=tile_url(BASETIME, BASETIME, 6, 56, 25), content=tile_png)
    with JmaTileClient(cache_dir=tmp_path) as client:
        first = client.fetch_tile(BASETIME, BASETIME, 6, 56, 25)
        second = client.fetch_tile(BASETIME, BASETIME, 6, 56, 25)
    assert np.array_equal(first, second)
    assert len(httpx_mock.get_requests()) == 1
    cached = tmp_path / "hrpns" / BASETIME / BASETIME / "6" / "56" / "25.png"
    assert cached.is_file()


def test_fetch_tiles_concurrent(httpx_mock: HTTPXMock, tile_png: bytes) -> None:
    tile_range = TileRange(z=6, x_min=56, x_max=57, y_min=25, y_max=25)
    for x in (56, 57):
        httpx_mock.add_response(url=tile_url(BASETIME, BASETIME, 6, x, 25), content=tile_png)
    seen: list[tuple[int, int]] = []
    with JmaTileClient(concurrency=2) as client:
        tiles = client.fetch_tiles(
            BASETIME, BASETIME, tile_range, progress=lambda d, t: seen.append((d, t))
        )
    assert set(tiles) == {(56, 25), (57, 25)}
    assert seen == [(1, 2), (2, 2)]


def test_fetch_tiles_rejects_bad_zoom(httpx_mock: HTTPXMock) -> None:
    with JmaTileClient() as client, pytest.raises(ValueError, match="no hrpns data"):
        client.fetch_tiles(BASETIME, BASETIME, TileRange(z=5, x_min=0, x_max=0, y_min=0, y_max=0))
