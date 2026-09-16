"""Tests for the tile mathematics."""

from __future__ import annotations

import pytest

from jma_radar.tiles import (
    VALID_ZOOMS,
    check_zoom,
    domain_tile_range,
    lonlat_to_tile,
    tile_bounds,
    tile_to_lonlat,
)


@pytest.mark.parametrize(
    ("z", "expected", "count"),
    [
        (4, (13, 14, 5, 7), 6),
        (6, (52, 58, 22, 28), 49),
        (8, (211, 234, 88, 113), 624),
        (10, (847, 938, 355, 453), 9108),
    ],
)
def test_domain_tile_range(z: int, expected: tuple[int, int, int, int], count: int) -> None:
    rng = domain_tile_range(z)
    assert (rng.x_min, rng.x_max, rng.y_min, rng.y_max) == expected
    assert rng.count == count
    assert len(list(rng)) == count


def test_valid_zooms() -> None:
    assert VALID_ZOOMS == (4, 6, 8, 10)
    for z in VALID_ZOOMS:
        assert check_zoom(z) == z
    for bad in (3, 5, 9, 11, 12):
        with pytest.raises(ValueError):
            check_zoom(bad)
    with pytest.raises(ValueError):
        domain_tile_range(9)


def test_tile_bounds_round_trip() -> None:
    west, south, east, north = tile_bounds(6, 56, 25)
    assert west < east
    assert south < north
    # Tile centre must fall back onto the same tile.
    assert lonlat_to_tile((west + east) / 2, (south + north) / 2, 6) == (56, 25)


def test_tile_to_lonlat_origin() -> None:
    lon, lat = tile_to_lonlat(0, 0, 4)
    assert lon == pytest.approx(-180.0)
    assert lat == pytest.approx(85.0511287798066)


def test_lonlat_to_tile_is_clamped() -> None:
    assert lonlat_to_tile(179.999999, 0.0, 4)[0] == 15
    assert lonlat_to_tile(-180.0, 89.0, 4) == (0, 0)
