"""Build precipitation-intensity grids from JMA precipitation nowcast tiles.

The Japan Meteorological Agency publishes its "high-resolution precipitation
nowcast" (``hrpns``) as XYZ map tiles whose palette encodes precipitation
intensity *classes* in mm/h. This package downloads those tiles, decodes the
classes and mosaics them onto a regular lat/lon grid, keeping JMA's native
unit (mm/h) as the primary product.

Optionally (``dbz=True`` / ``--dbz``) an approximate reflectivity field
``cref`` is added through a Z-R relation. It is a *pseudo* reflectivity, not
an observation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import xarray as xr

from .client import DEFAULT_CONCURRENCY, JmaTileClient, tile_url
from .constants import (
    BASE_URL,
    DEFAULT_ELEMENT,
    DEFAULT_ZR_A,
    DEFAULT_ZR_B,
    LEVEL_COLORS,
    LEVEL_LABELS,
    PRODUCT_NAME,
    VALID_ZOOMS,
)
from .decode import PaletteError, decode_tile, is_empty_tile
from .io import (
    DBZ_DISCLAIMER,
    DISCLAIMER,
    RAIN_RATE_UNITS,
    read_levels,
    to_dataset,
    to_series_dataset,
    write_geotiff,
    write_netcdf,
    write_png,
    write_series,
)
from .levels import (
    LEVEL_BOUNDS,
    LEVEL_REPRESENTATIVE_RAIN_RATE,
    level_to_dbz,
    level_to_rain_rate,
    rain_rate_to_dbz,
)
from .mosaic import LatLonGrid, ResampleMethod, assemble_mosaic, make_grid, to_latlon_grid
from .tiles import TileRange, domain_tile_range, tile_bounds
from .times import TargetTime, latest, parse_target_times, parse_time
from .window import GridSpec, WindowFrame, fetch_window, parse_start, window_target_times

__version__ = "0.2.0"

__all__ = [
    "BASE_URL",
    "DBZ_DISCLAIMER",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_ELEMENT",
    "DEFAULT_ZR_A",
    "DEFAULT_ZR_B",
    "DISCLAIMER",
    "LEVEL_BOUNDS",
    "LEVEL_COLORS",
    "LEVEL_LABELS",
    "LEVEL_REPRESENTATIVE_RAIN_RATE",
    "PRODUCT_NAME",
    "RAIN_RATE_UNITS",
    "VALID_ZOOMS",
    "GridSpec",
    "JmaTileClient",
    "LatLonGrid",
    "PaletteError",
    "ResampleMethod",
    "TargetTime",
    "TileRange",
    "WindowFrame",
    "__version__",
    "assemble_mosaic",
    "decode_tile",
    "domain_tile_range",
    "fetch_dataset",
    "fetch_grid",
    "fetch_window",
    "is_empty_tile",
    "latest",
    "level_to_dbz",
    "level_to_rain_rate",
    "make_grid",
    "parse_start",
    "parse_target_times",
    "parse_time",
    "rain_rate_to_dbz",
    "read_levels",
    "tile_bounds",
    "tile_url",
    "to_dataset",
    "to_latlon_grid",
    "to_series_dataset",
    "window_target_times",
    "write_geotiff",
    "write_netcdf",
    "write_png",
    "write_series",
]

logger = logging.getLogger(__name__)


def fetch_grid(
    zoom: int = 8,
    *,
    time: str | None = None,
    valid: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    dlon: float | None = None,
    dlat: float | None = None,
    method: ResampleMethod = "nearest",
    concurrency: int = DEFAULT_CONCURRENCY,
    cache_dir: str | Path | None = None,
    element: str = DEFAULT_ELEMENT,
    client: JmaTileClient | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[LatLonGrid, TargetTime]:
    """Download one nowcast frame and resample it to a lat/lon grid.

    Args:
        zoom: tile zoom level, one of :data:`VALID_ZOOMS`.
        time: ``basetime`` as ``YYYYMMDDHHMMSS``, or ``None``/``"latest"``.
        valid: optional ``validtime`` (use ``N2`` times for forecasts).
        bbox: optional ``(west, south, east, north)`` subset.
        dlon: longitude spacing override in degrees (the zoom's own by default).
        dlat: latitude spacing override in degrees.
        method: ``nearest`` or ``max`` (:data:`ResampleMethod`).
        concurrency: number of parallel tile downloads.
        cache_dir: optional directory used to cache raw tiles on disk.
        element: tile element id, ``hrpns`` by default.
        client: an existing client to reuse; a temporary one is created otherwise.
        progress: optional ``(done, total)`` callback.

    Returns:
        The resampled grid and the resolved :class:`TargetTime`.
    """
    owned = client is None
    active = client or JmaTileClient(concurrency=concurrency, cache_dir=cache_dir)
    try:
        target = active.resolve_target_time(time=time, valid=valid, element=element)
        tile_range = domain_tile_range(zoom)
        tiles = active.fetch_tiles(
            target.basetime, target.validtime, tile_range, element=element, progress=progress
        )
        mosaic = assemble_mosaic(tiles, tile_range)
        grid = to_latlon_grid(mosaic, tile_range, bbox=bbox, dlon=dlon, dlat=dlat, method=method)
        return grid, target
    finally:
        if owned:
            active.close()


def fetch_dataset(
    zoom: int = 8,
    *,
    time: str | None = None,
    valid: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    dlon: float | None = None,
    dlat: float | None = None,
    method: ResampleMethod = "nearest",
    concurrency: int = DEFAULT_CONCURRENCY,
    cache_dir: str | Path | None = None,
    element: str = DEFAULT_ELEMENT,
    dbz: bool = False,
    zr: tuple[float, float] = (DEFAULT_ZR_A, DEFAULT_ZR_B),
    client: JmaTileClient | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> xr.Dataset:
    """Download one nowcast frame and return it as an :class:`xarray.Dataset`.

    The dataset holds ``rain_rate`` (mm/h) and ``level``; pass ``dbz=True`` to
    also include the pseudo-reflectivity ``cref`` field computed with ``zr``.
    """
    grid, target = fetch_grid(
        zoom,
        time=time,
        valid=valid,
        bbox=bbox,
        dlon=dlon,
        dlat=dlat,
        method=method,
        concurrency=concurrency,
        cache_dir=cache_dir,
        element=element,
        client=client,
        progress=progress,
    )
    return to_dataset(
        grid,
        basetime=target.basetime,
        validtime=target.validtime,
        zoom=zoom,
        dbz=dbz,
        zr_a=zr[0],
        zr_b=zr[1],
        element=element,
    )
