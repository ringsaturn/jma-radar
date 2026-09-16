"""Web Mercator XYZ tile mathematics for the JMA nowcast domain."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from .constants import (
    DOMAIN_MAX_LAT,
    DOMAIN_MAX_LON,
    DOMAIN_MIN_LAT,
    DOMAIN_MIN_LON,
    TILE_SIZE,
    VALID_ZOOMS,
)

__all__ = [
    "VALID_ZOOMS",
    "TileRange",
    "check_zoom",
    "domain_tile_range",
    "lonlat_to_pixel",
    "lonlat_to_tile",
    "tile_bounds",
    "tile_to_lonlat",
]

#: Maximum absolute latitude representable in Web Mercator.
MAX_MERCATOR_LAT = 85.0511287798066


@dataclass(frozen=True, slots=True)
class TileRange:
    """Inclusive tile index range at a given zoom level."""

    z: int
    x_min: int
    x_max: int
    y_min: int
    y_max: int

    @property
    def nx(self) -> int:
        """Number of tile columns."""
        return self.x_max - self.x_min + 1

    @property
    def ny(self) -> int:
        """Number of tile rows."""
        return self.y_max - self.y_min + 1

    @property
    def count(self) -> int:
        """Total number of tiles in the range."""
        return self.nx * self.ny

    def __iter__(self) -> Iterator[tuple[int, int]]:
        """Iterate over ``(x, y)`` tile indices, row major."""
        return iter(
            (x, y)
            for y in range(self.y_min, self.y_max + 1)
            for x in range(self.x_min, self.x_max + 1)
        )

    def __len__(self) -> int:
        return self.count


def check_zoom(z: int) -> int:
    """Validate that ``z`` is one of the zoom levels that actually carry data.

    Raises:
        ValueError: if ``z`` is not in :data:`VALID_ZOOMS`.
    """
    if z not in VALID_ZOOMS:
        raise ValueError(f"zoom {z} has no hrpns data; valid zooms are {list(VALID_ZOOMS)}")
    return z


def lonlat_to_pixel(lon: float, lat: float, z: int) -> tuple[float, float]:
    """Convert lon/lat (degrees) to global Web Mercator pixel coordinates."""
    n = float(TILE_SIZE << z)
    lat = min(max(lat, -MAX_MERCATOR_LAT), MAX_MERCATOR_LAT)
    px = (lon + 180.0) / 360.0 * n
    sin_lat = math.sin(math.radians(lat))
    py = (0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * n
    return px, py


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    """Convert lon/lat (degrees) to the containing tile indices ``(x, y)``."""
    px, py = lonlat_to_pixel(lon, lat, z)
    max_index = (1 << z) - 1
    x = min(max(math.floor(px / TILE_SIZE), 0), max_index)
    y = min(max(math.floor(py / TILE_SIZE), 0), max_index)
    return x, y


def tile_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    """Convert (possibly fractional) tile coordinates to the lon/lat of their corner."""
    n = float(1 << z)
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lon, lat


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """Return the geographic bounds of a tile as ``(west, south, east, north)``."""
    west, north = tile_to_lonlat(x, y, z)
    east, south = tile_to_lonlat(x + 1, y + 1, z)
    return west, south, east, north


def domain_tile_range(z: int) -> TileRange:
    """Return the tile range covering the JMA nowcast domain at zoom ``z``.

    The domain is lat 20-48 N, lon 118-150 E as declared by
    ``nowc.properties.xml``.
    """
    check_zoom(z)
    x_min, y_min = lonlat_to_tile(DOMAIN_MIN_LON, DOMAIN_MAX_LAT, z)
    x_max, y_max = lonlat_to_tile(DOMAIN_MAX_LON, DOMAIN_MIN_LAT, z)
    return TileRange(z=z, x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max)
