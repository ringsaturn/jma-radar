"""Live smoke tests against the public JMA service.

These are skipped unless pytest is invoked with ``-m network``.
"""

from __future__ import annotations

import numpy as np
import pytest

from jma_radar import JmaTileClient, assemble_mosaic, fetch_grid, to_dataset
from jma_radar.tiles import domain_tile_range

pytestmark = pytest.mark.network


def test_target_times_live() -> None:
    with JmaTileClient() as client:
        entries = client.fetch_target_times("N1")
    assert entries
    assert all(entry.has_element("hrpns") for entry in entries)


def test_fetch_single_tile_live() -> None:
    with JmaTileClient() as client:
        target = client.latest_target_time()
        levels = client.fetch_tile(target.basetime, target.validtime, 4, 13, 6)
    assert levels.shape == (256, 256)
    assert levels.max() <= 9


def test_fetch_zoom4_frame_live() -> None:
    with JmaTileClient() as client:
        target = client.latest_target_time()
        tile_range = domain_tile_range(4)
        tiles = client.fetch_tiles(target.basetime, target.validtime, tile_range)
    assert len(tiles) == tile_range.count
    mosaic = assemble_mosaic(tiles, tile_range)
    assert mosaic.shape == (3 * 256, 2 * 256)
    assert np.any(mosaic > 0)


def test_fetch_grid_live() -> None:
    grid, target = fetch_grid(4)
    dataset = to_dataset(grid, basetime=target.basetime, validtime=target.validtime, zoom=4)
    assert dataset["rain_rate"].shape == grid.shape
    assert "cref" not in dataset
    assert int((grid.levels > 0).sum()) > 0
