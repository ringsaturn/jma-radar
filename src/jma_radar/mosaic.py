"""Assemble tiles into a mosaic and resample it to a regular lat/lon grid."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from .constants import (
    DOMAIN_MAX_LAT,
    DOMAIN_MAX_LON,
    DOMAIN_MIN_LAT,
    DOMAIN_MIN_LON,
    GRID_STEPS,
    TILE_SIZE,
)
from .tiles import MAX_MERCATOR_LAT, TileRange, check_zoom

__all__ = ["LatLonGrid", "assemble_mosaic", "grid_steps", "make_grid", "to_latlon_grid"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LatLonGrid:
    """A regular lat/lon grid of precipitation levels.

    Latitude is stored decreasing (north to south) to match the tile layout and
    the sibling ``nmc-radar-spider`` output convention.
    """

    lat: np.ndarray
    lon: np.ndarray
    levels: np.ndarray

    @property
    def shape(self) -> tuple[int, int]:
        """``(nlat, nlon)`` shape of the level array."""
        return (self.lat.size, self.lon.size)


def assemble_mosaic(
    tiles: Mapping[tuple[int, int], np.ndarray], tile_range: TileRange
) -> np.ndarray:
    """Stitch decoded tiles into one Web Mercator level array.

    Missing tiles are filled with level 0 ("no data").

    Returns:
        ``uint8`` array of shape ``(ny * 256, nx * 256)``.
    """
    mosaic = np.zeros((tile_range.ny * TILE_SIZE, tile_range.nx * TILE_SIZE), dtype=np.uint8)
    for (x, y), levels in tiles.items():
        if not (tile_range.x_min <= x <= tile_range.x_max):
            raise ValueError(f"tile x={x} is outside {tile_range}")
        if not (tile_range.y_min <= y <= tile_range.y_max):
            raise ValueError(f"tile y={y} is outside {tile_range}")
        if levels.shape != (TILE_SIZE, TILE_SIZE):
            raise ValueError(f"tile ({x}, {y}) has shape {levels.shape}")
        row = (y - tile_range.y_min) * TILE_SIZE
        col = (x - tile_range.x_min) * TILE_SIZE
        mosaic[row : row + TILE_SIZE, col : col + TILE_SIZE] = levels
    return mosaic


def grid_steps(z: int, dlon: float | None = None, dlat: float | None = None) -> tuple[float, float]:
    """Return the output grid spacing in degrees for zoom ``z``."""
    check_zoom(z)
    default_lon, default_lat = GRID_STEPS[z]
    return (dlon or default_lon, dlat or default_lat)


def make_grid(
    z: int,
    bbox: tuple[float, float, float, float] | None = None,
    dlon: float | None = None,
    dlat: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the output coordinate vectors ``(lat, lon)`` for zoom ``z``.

    Coordinates are cell centres; ``lat`` decreases from north to south.

    Args:
        z: tile zoom level, used to pick the default spacing.
        bbox: optional ``(west, south, east, north)`` subset of the domain.
        dlon: longitude spacing override in degrees.
        dlat: latitude spacing override in degrees.
    """
    step_lon, step_lat = grid_steps(z, dlon, dlat)
    west, south, east, north = bbox or (
        DOMAIN_MIN_LON,
        DOMAIN_MIN_LAT,
        DOMAIN_MAX_LON,
        DOMAIN_MAX_LAT,
    )
    if east <= west or north <= south:
        raise ValueError(f"invalid bbox {(west, south, east, north)}")
    nlon = max(1, round((east - west) / step_lon))
    nlat = max(1, round((north - south) / step_lat))
    lon = west + (np.arange(nlon, dtype=np.float64) + 0.5) * step_lon
    lat = north - (np.arange(nlat, dtype=np.float64) + 0.5) * step_lat
    return lat, lon


def to_latlon_grid(
    mosaic: np.ndarray,
    tile_range: TileRange,
    bbox: tuple[float, float, float, float] | None = None,
    dlon: float | None = None,
    dlat: float | None = None,
) -> LatLonGrid:
    """Nearest-neighbour resample a Web Mercator mosaic onto a lat/lon grid.

    Nearest neighbour (rather than an interpolating kernel) is deliberate: the
    values are discrete precipitation classes and must not be averaged.
    """
    z = tile_range.z
    lat, lon = make_grid(z, bbox=bbox, dlon=dlon, dlat=dlat)

    n_pixels = float(TILE_SIZE << z)
    # Longitude -> global pixel column.
    px = (lon + 180.0) / 360.0 * n_pixels
    # Latitude -> global pixel row (Web Mercator).
    clipped = np.clip(lat, -MAX_MERCATOR_LAT, MAX_MERCATOR_LAT)
    sin_lat = np.sin(np.radians(clipped))
    py = (0.5 - np.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * np.pi)) * n_pixels

    col = np.floor(px).astype(np.int64) - tile_range.x_min * TILE_SIZE
    row = np.floor(py).astype(np.int64) - tile_range.y_min * TILE_SIZE

    height, width = mosaic.shape
    col_valid = (col >= 0) & (col < width)
    row_valid = (row >= 0) & (row < height)
    safe_col = np.clip(col, 0, max(width - 1, 0))
    safe_row = np.clip(row, 0, max(height - 1, 0))

    levels = mosaic[np.ix_(safe_row, safe_col)]
    outside = ~(row_valid[:, None] & col_valid[None, :])
    if outside.any():
        levels = levels.copy()
        levels[outside] = 0
        logger.debug("%d grid cells fall outside the mosaic", int(outside.sum()))
    return LatLonGrid(lat=lat, lon=lon, levels=levels.astype(np.uint8, copy=False))
