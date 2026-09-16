"""Assemble tiles into a mosaic and resample it to a regular lat/lon grid."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

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

__all__ = [
    "LatLonGrid",
    "ResampleMethod",
    "assemble_mosaic",
    "grid_steps",
    "make_grid",
    "to_latlon_grid",
]

#: How a Web Mercator mosaic is brought onto the lat/lon grid.
#:
#: ``nearest`` takes the pixel under each cell centre. ``max`` takes the
#: highest level among the pixels whose centres fall inside the cell: levels
#: are ordered by intensity (0 no data < 1 no rain < 2 ... < 9), so the
#: maximum is the strongest class the cell contains, which is how a radar
#: product is thinned — a nearest-neighbour pick drops convective cores at
#: random once the cell is wider than a pixel.
ResampleMethod = Literal["nearest", "max"]

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


def _pixel_columns(lon: np.ndarray, z: int) -> np.ndarray:
    """Longitudes (degrees) to global Web Mercator pixel columns."""
    return (np.asarray(lon, dtype=np.float64) + 180.0) / 360.0 * float(TILE_SIZE << z)


def _pixel_rows(lat: np.ndarray, z: int) -> np.ndarray:
    """Latitudes (degrees) to global Web Mercator pixel rows."""
    clipped = np.clip(np.asarray(lat, dtype=np.float64), -MAX_MERCATOR_LAT, MAX_MERCATOR_LAT)
    sin_lat = np.sin(np.radians(clipped))
    return (0.5 - np.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * np.pi)) * float(TILE_SIZE << z)


def _segment_starts(edges: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    """The first mosaic pixel of every cell, from the cells' edge coordinates
    in local pixel units, plus a mask of the cells that lie entirely off the
    mosaic.

    A pixel ``c`` covers ``[c, c + 1)`` and belongs to the cell whose span
    holds its centre, so the first pixel of a cell whose edge is at ``e`` is
    ``ceil(e - 0.5)``. Starts are clipped to ``[0, size]``; ``size`` is a
    padding index (see :func:`_block_max`) so a cell starting past the end
    reduces over the padding alone.
    """
    starts = np.ceil(edges - 0.5).astype(np.int64)
    outside = (starts[:-1] >= size) | (starts[1:] <= 0)
    return np.clip(starts[:-1], 0, size), outside


def _block_max(mosaic: np.ndarray, row_edges: np.ndarray, col_edges: np.ndarray) -> np.ndarray:
    """The maximum level under every cell of a grid whose cell edges are
    given in local mosaic pixel units (rows increasing southwards, columns
    increasing eastwards).

    ``np.maximum.reduceat`` reduces the run of pixels from one cell's first
    pixel to the next cell's; a cell narrower than a pixel (an empty run)
    yields the single pixel at its start, which is the nearest pick. The
    mosaic is padded with one no-data row and column so a cell starting at
    the edge has something to reduce over, and cells wholly outside the
    mosaic are set to no data afterwards.
    """
    height, width = mosaic.shape
    padded = np.zeros((height + 1, width + 1), dtype=mosaic.dtype)
    padded[:height, :width] = mosaic
    row_starts, rows_outside = _segment_starts(row_edges, height)
    col_starts, cols_outside = _segment_starts(col_edges, width)
    rows = np.maximum.reduceat(padded, row_starts, axis=0)
    levels = np.maximum.reduceat(rows, col_starts, axis=1)
    outside = rows_outside[:, None] | cols_outside[None, :]
    if outside.any():
        levels[outside] = 0
        logger.debug("%d grid cells fall outside the mosaic", int(outside.sum()))
    return levels


def to_latlon_grid(
    mosaic: np.ndarray,
    tile_range: TileRange,
    bbox: tuple[float, float, float, float] | None = None,
    dlon: float | None = None,
    dlat: float | None = None,
    method: ResampleMethod = "nearest",
) -> LatLonGrid:
    """Resample a Web Mercator mosaic onto a regular lat/lon grid.

    ``method`` is ``nearest`` (the pixel under each cell centre) or ``max``
    (the strongest class among the pixels in each cell, see
    :data:`ResampleMethod`). Neither interpolates: the values are discrete
    precipitation classes and must not be averaged.
    """
    z = tile_range.z
    lat, lon = make_grid(z, bbox=bbox, dlon=dlon, dlat=dlat)
    height, width = mosaic.shape

    if method == "max":
        step_lon, step_lat = grid_steps(z, dlon, dlat)
        lon_edges = np.concatenate([lon - step_lon / 2.0, [lon[-1] + step_lon / 2.0]])
        lat_edges = np.concatenate([lat + step_lat / 2.0, [lat[-1] - step_lat / 2.0]])
        col_edges = _pixel_columns(lon_edges, z) - tile_range.x_min * TILE_SIZE
        row_edges = _pixel_rows(lat_edges, z) - tile_range.y_min * TILE_SIZE
        levels = _block_max(mosaic, row_edges, col_edges)
        return LatLonGrid(lat=lat, lon=lon, levels=levels.astype(np.uint8, copy=False))
    if method != "nearest":
        raise ValueError(f"unknown resampling method {method!r}; use 'nearest' or 'max'")

    col = np.floor(_pixel_columns(lon, z)).astype(np.int64) - tile_range.x_min * TILE_SIZE
    row = np.floor(_pixel_rows(lat, z)).astype(np.int64) - tile_range.y_min * TILE_SIZE

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
