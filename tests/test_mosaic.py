"""Tests for mosaicking and lat/lon resampling."""

from __future__ import annotations

import numpy as np
import pytest

from jma_radar.mosaic import assemble_mosaic, grid_steps, make_grid, to_latlon_grid
from jma_radar.tiles import TileRange, domain_tile_range, tile_bounds


def _constant_tile(value: int) -> np.ndarray:
    return np.full((256, 256), value, dtype=np.uint8)


def test_assemble_2x2() -> None:
    tile_range = TileRange(z=6, x_min=52, x_max=53, y_min=22, y_max=23)
    tiles = {
        (52, 22): _constant_tile(1),
        (53, 22): _constant_tile(2),
        (52, 23): _constant_tile(3),
        (53, 23): _constant_tile(4),
    }
    mosaic = assemble_mosaic(tiles, tile_range)
    assert mosaic.shape == (512, 512)
    assert mosaic[0, 0] == 1  # north-west
    assert mosaic[0, 511] == 2  # north-east
    assert mosaic[511, 0] == 3  # south-west
    assert mosaic[511, 511] == 4  # south-east


def test_assemble_missing_tile_is_nodata() -> None:
    tile_range = TileRange(z=6, x_min=52, x_max=53, y_min=22, y_max=22)
    mosaic = assemble_mosaic({(52, 22): _constant_tile(5)}, tile_range)
    assert (mosaic[:, :256] == 5).all()
    assert (mosaic[:, 256:] == 0).all()


def test_assemble_rejects_out_of_range_tiles() -> None:
    tile_range = TileRange(z=6, x_min=52, x_max=52, y_min=22, y_max=22)
    with pytest.raises(ValueError):
        assemble_mosaic({(99, 22): _constant_tile(1)}, tile_range)
    with pytest.raises(ValueError):
        assemble_mosaic({(52, 99): _constant_tile(1)}, tile_range)


def test_make_grid_is_north_to_south() -> None:
    lat, lon = make_grid(6)
    assert lat[0] > lat[-1]
    assert lon[0] < lon[-1]
    dlon, dlat = grid_steps(6)
    assert lat[0] == pytest.approx(48.0 - dlat / 2)
    assert lat[-1] == pytest.approx(20.0 + dlat / 2)
    assert lon[0] == pytest.approx(118.0 + dlon / 2)
    assert lon[-1] == pytest.approx(150.0 - dlon / 2)
    assert lat.size == 840
    assert lon.size == 640


def test_make_grid_bbox_and_overrides() -> None:
    lat, lon = make_grid(6, bbox=(130.0, 30.0, 140.0, 40.0), dlon=0.1, dlat=0.1)
    assert lon.size == 100
    assert lat.size == 100
    assert lat[0] == pytest.approx(39.95)
    with pytest.raises(ValueError):
        make_grid(6, bbox=(140.0, 30.0, 130.0, 40.0))


def test_to_latlon_grid_orientation() -> None:
    """A tile whose northern half is class 3 must land in the northern rows."""
    tile_range = TileRange(z=6, x_min=52, x_max=53, y_min=22, y_max=23)
    north = np.zeros((256, 256), dtype=np.uint8)
    north[:] = 3
    south = np.zeros((256, 256), dtype=np.uint8)
    south[:] = 5
    tiles = {
        (52, 22): north,
        (53, 22): north,
        (52, 23): south,
        (53, 23): south,
    }
    mosaic = assemble_mosaic(tiles, tile_range)
    west, south_lat, _, north_lat = tile_bounds(6, 52, 22)
    _, _, east2, _ = tile_bounds(6, 53, 22)
    _, south2, _, _ = tile_bounds(6, 52, 23)
    grid = to_latlon_grid(
        mosaic,
        tile_range,
        bbox=(west + 0.01, south2 + 0.01, east2 - 0.01, north_lat - 0.01),
        dlon=0.05,
        dlat=0.05,
    )
    assert grid.lat[0] > grid.lat[-1]
    assert grid.levels[0, 0] == 3
    assert grid.levels[-1, -1] == 5
    # The class boundary must sit near the shared tile edge.
    boundary_row = int(np.argmax(grid.levels[:, 0] == 5))
    boundary_lat = grid.lat[boundary_row]
    assert abs(boundary_lat - south_lat) < 0.1
    assert grid.shape == (grid.lat.size, grid.lon.size)


def test_to_latlon_grid_longitude_orientation() -> None:
    """A tile whose western half is class 2 must land in the western columns."""
    tile_range = TileRange(z=6, x_min=52, x_max=53, y_min=22, y_max=22)
    tiles = {(52, 22): _constant_tile(2), (53, 22): _constant_tile(7)}
    mosaic = assemble_mosaic(tiles, tile_range)
    west, _, _, north = tile_bounds(6, 52, 22)
    _, south, east, _ = tile_bounds(6, 53, 22)
    grid = to_latlon_grid(
        mosaic,
        tile_range,
        bbox=(west + 0.01, south + 0.01, east - 0.01, north - 0.01),
        dlon=0.05,
        dlat=0.05,
    )
    assert grid.levels[0, 0] == 2
    assert grid.levels[0, -1] == 7


def test_to_latlon_grid_outside_is_nodata() -> None:
    tile_range = domain_tile_range(4)
    mosaic = np.full((tile_range.ny * 256, tile_range.nx * 256), 3, dtype=np.uint8)
    # Request a box far outside the mosaic; every cell must be no-data.
    grid = to_latlon_grid(mosaic, tile_range, bbox=(0.0, 0.0, 10.0, 10.0), dlon=1.0, dlat=1.0)
    assert (grid.levels == 0).all()
