"""Build xarray datasets and write NetCDF / GeoTIFF / PNG output."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

from .constants import (
    DEFAULT_ZR_A,
    DEFAULT_ZR_B,
    FLAG_MEANINGS,
    LEVEL_COLORS,
    LEVEL_LABELS,
    MAX_LEVEL,
    PRODUCT_NAME,
    TILE_URL_TEMPLATE,
)
from .levels import (
    LEVEL_REPRESENTATIVE_RAIN_RATE,
    level_to_dbz_array,
    level_to_rain_rate_array,
)
from .mosaic import LatLonGrid
from .times import parse_time

__all__ = [
    "DBZ_DISCLAIMER",
    "DISCLAIMER",
    "RAIN_RATE_UNITS",
    "TIME_UNITS",
    "read_levels",
    "to_dataset",
    "to_series_dataset",
    "write_geotiff",
    "write_netcdf",
    "write_png",
]

#: The unit ``rain_rate`` is written in. ``mm/h`` and ``mm h-1`` are the same
#: udunits quantity; the slash form is what downstream readers that compare
#: unit strings (Xue's observation ingest) expect.
RAIN_RATE_UNITS = "mm/h"

#: The ``time`` coordinate of a series file, CF style, on the Unix epoch.
TIME_UNITS = "seconds since 1970-01-01T00:00:00+00:00"

#: Packing of ``rain_rate`` in a series file: the class representative values
#: times two are all whole numbers (1, 6, 15, 30, 50, 80, 130, 200), so a
#: byte with ``scale_factor`` 0.5 stores them exactly, with 255 as the fill.
_SERIES_RAIN_RATE_ENCODING: dict[str, Any] = {
    "dtype": "uint8",
    "scale_factor": 0.5,
    "add_offset": 0.0,
    "_FillValue": 255,
}

#: Encoding keys carried from a variable into ``to_netcdf``; the rest
#: (``source``, ``original_shape`` ...) is bookkeeping xarray adds on read.
_ENCODING_KEYS = frozenset(
    {"dtype", "scale_factor", "add_offset", "_FillValue", "units", "calendar", "chunksizes"}
)

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Derived product. JMA publishes precipitation intensity as classes (mm/h) in map tiles; "
    "rain_rate holds a representative value for each class, not a measured rate."
)

DBZ_DISCLAIMER = (
    "Pseudo reflectivity. JMA publishes precipitation intensity classes (mm/h), not radar "
    "reflectivity. The dBZ values here were derived from the class representative rain rate "
    "through a Marshall-Palmer style Z-R relation (Z = a * R**b) and therefore only "
    "approximate the true composite reflectivity. Do not use them as observed reflectivity."
)


def _iso(timestamp: str) -> str:
    """Convert a JMA ``YYYYMMDDHHMMSS`` timestamp to ISO 8601 UTC."""
    return parse_time(timestamp).isoformat().replace("+00:00", "Z")


def to_dataset(
    grid: LatLonGrid,
    *,
    basetime: str,
    validtime: str,
    zoom: int,
    dbz: bool = False,
    zr_a: float = DEFAULT_ZR_A,
    zr_b: float = DEFAULT_ZR_B,
    element: str = "hrpns",
) -> xr.Dataset:
    """Build an :class:`xarray.Dataset` from a resampled level grid.

    Variables:
        ``rain_rate``: class representative precipitation intensity (mm/h),
        ``0`` for no precipitation, ``NaN`` for no data. This is the primary
        product and stays in JMA's native unit.
        ``level``: raw JMA precipitation class, 0 = no data.
        ``cref``: only when ``dbz=True``; pseudo composite reflectivity (dBZ)
        from a Z-R relation, ``NaN`` for both no-data and 0 mm/h cells.
    """
    levels = np.asarray(grid.levels, dtype=np.uint8)
    rain_rate = level_to_rain_rate_array(levels).astype(np.float32)

    coords = {
        "lat": (
            "lat",
            np.asarray(grid.lat, dtype=np.float64),
            {
                "units": "degrees_north",
                "standard_name": "latitude",
                "long_name": "latitude of grid cell centre",
                "axis": "Y",
            },
        ),
        "lon": (
            "lon",
            np.asarray(grid.lon, dtype=np.float64),
            {
                "units": "degrees_east",
                "standard_name": "longitude",
                "long_name": "longitude of grid cell centre",
                "axis": "X",
            },
        ),
    }

    representative = ", ".join(
        "nan" if np.isnan(value) else f"{value:g}" for value in LEVEL_REPRESENTATIVE_RAIN_RATE
    )

    bounds = "; ".join(f"{level}: {label}" for level, label in enumerate(LEVEL_LABELS))
    data_vars: dict[str, Any] = {
        "rain_rate": (
            ("lat", "lon"),
            rain_rate,
            {
                "units": RAIN_RATE_UNITS,
                "standard_name": "rainfall_rate",
                "long_name": "Precipitation intensity (class representative value)",
                "_FillValue": np.float32(np.nan),
                "class_bounds": bounds,
                "class_representative_rain_rate": representative,
                "comment": DISCLAIMER,
            },
        ),
        "level": (
            ("lat", "lon"),
            levels,
            {
                "long_name": "JMA HRPNS precipitation intensity class",
                "units": "1",
                "flag_values": np.arange(MAX_LEVEL + 1, dtype=np.uint8),
                "flag_meanings": " ".join(FLAG_MEANINGS),
                "comment": "0 means no data (outside domain or not observed)",
            },
        ),
    }
    if dbz:
        cref = level_to_dbz_array(levels, a=zr_a, b=zr_b).astype(np.float32)
        data_vars["cref"] = (
            ("lat", "lon"),
            cref,
            {
                "units": "dBZ",
                "long_name": (
                    "Pseudo composite reflectivity derived from JMA HRPNS "
                    "precipitation intensity class"
                ),
                "_FillValue": np.float32(np.nan),
                "zr_a": np.float32(zr_a),
                "zr_b": np.float32(zr_b),
                "class_representative_rain_rate": representative,
                "comment": DBZ_DISCLAIMER,
            },
        )

    dataset = xr.Dataset(
        data_vars=data_vars,
        coords=coords,
        attrs={
            "title": "Precipitation intensity from JMA high-resolution precipitation nowcast tiles",
            "product": PRODUCT_NAME,
            "source": TILE_URL_TEMPLATE,
            "element": element,
            "basetime": _iso(basetime),
            "validtime": _iso(validtime),
            "zoom": zoom,
            "crs": "EPSG:4326",
            "Conventions": "CF-1.8",
            "institution": "Japan Meteorological Agency (tiles); jma-radar (derived product)",
            "disclaimer": DBZ_DISCLAIMER if dbz else DISCLAIMER,
            "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        },
    )
    return dataset


def to_series_dataset(
    frames: Sequence[tuple[LatLonGrid, str]],
    *,
    zoom: int,
    element: str = "hrpns",
    method: str = "nearest",
) -> xr.Dataset:
    """Build one :class:`xarray.Dataset` holding a series of analyses.

    ``frames`` are ``(grid, validtime)`` pairs on one grid, in any order;
    the result is sorted by time. Variables are ``rain_rate(time, lat, lon)``
    in mm/h — packed as a byte with ``scale_factor`` 0.5 and fill 255, which
    holds every class representative value exactly — and
    ``level(time, lat, lon)``. The ``time`` coordinate is encoded as seconds
    since the Unix epoch. This is the shape a series reader (Xue's
    observation ingest, GDAL's NetCDF driver) takes: one band per time,
    one subdataset per variable.
    """
    if not frames:
        raise ValueError("a series needs at least one frame")
    ordered = sorted(frames, key=lambda item: item[1])
    first, _ = ordered[0]
    for grid, validtime in ordered[1:]:
        if grid.shape != first.shape or not (
            np.array_equal(grid.lat, first.lat) and np.array_equal(grid.lon, first.lon)
        ):
            raise ValueError(f"frame {validtime} is not on the same grid as {ordered[0][1]}")
    validtimes = [validtime for _, validtime in ordered]
    if len(set(validtimes)) != len(validtimes):
        raise ValueError("a series cannot hold two frames at one time")

    levels = np.stack([np.asarray(grid.levels, dtype=np.uint8) for grid, _ in ordered])
    rain_rate = level_to_rain_rate_array(levels).astype(np.float32)
    # Whole seconds since the epoch, written as such rather than left to
    # xarray's datetime encoding, so the units attribute reads exactly as
    # TIME_UNITS (xarray would shorten it) — the CF form a series reader parses.
    times = np.array(
        [int(parse_time(validtime).timestamp()) for validtime in validtimes], dtype=np.int64
    )

    single = to_dataset(
        first, basetime=validtimes[0], validtime=validtimes[0], zoom=zoom, element=element
    )
    rain_attrs = dict(single["rain_rate"].attrs)
    rain_attrs.pop("_FillValue", None)
    level_attrs = dict(single["level"].attrs)

    dataset = xr.Dataset(
        data_vars={
            "rain_rate": (("time", "lat", "lon"), rain_rate, rain_attrs),
            "level": (("time", "lat", "lon"), levels, level_attrs),
        },
        coords={
            "time": (
                "time",
                times,
                {
                    "standard_name": "time",
                    "long_name": "analysis time",
                    "axis": "T",
                    "units": TIME_UNITS,
                    "calendar": "standard",
                },
            ),
            "lat": single["lat"],
            "lon": single["lon"],
        },
        attrs={
            **{k: v for k, v in single.attrs.items() if k not in {"basetime", "validtime"}},
            "title": ("Precipitation intensity series from JMA precipitation nowcast tiles"),
            "first_validtime": _iso(validtimes[0]),
            "last_validtime": _iso(validtimes[-1]),
            "frame_count": len(validtimes),
            "resampling": method,
        },
    )
    nlat, nlon = first.shape
    dataset["rain_rate"].encoding = {**_SERIES_RAIN_RATE_ENCODING, "chunksizes": (1, nlat, nlon)}
    dataset["level"].encoding = {"dtype": "uint8", "chunksizes": (1, nlat, nlon)}
    dataset["time"].encoding = {"dtype": "int64"}
    return dataset


def _netcdf_encoding(dataset: xr.Dataset, *, compress: bool) -> dict[str, dict[str, Any]]:
    """Per-variable ``to_netcdf`` encodings: what each variable already
    carries in ``.encoding`` (the packing a series declares), plus deflate
    on the data variables."""
    encoding: dict[str, dict[str, Any]] = {}
    for name, variable in dataset.variables.items():
        kept = {key: value for key, value in variable.encoding.items() if key in _ENCODING_KEYS}
        if compress and name in dataset.data_vars:
            kept.update({"zlib": True, "complevel": 4})
        if kept:
            encoding[str(name)] = kept
    return encoding


def write_netcdf(dataset: xr.Dataset, path: str | Path, *, compress: bool = True) -> Path:
    """Write ``dataset`` to a NetCDF4 file and return the path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_netcdf(out, format="NETCDF4", encoding=_netcdf_encoding(dataset, compress=compress))
    logger.info("wrote %s", out)
    return out


def read_levels(path: str | Path) -> LatLonGrid:
    """Read the level grid back out of a single-frame NetCDF file written by
    :func:`write_netcdf` — what a frame cache holds."""
    with xr.open_dataset(path, decode_times=False) as dataset:
        level = dataset["level"]
        if level.dims != ("lat", "lon"):
            raise ValueError(f"{path} is not a single-frame file: level has dims {level.dims}")
        return LatLonGrid(
            lat=np.asarray(dataset["lat"].values, dtype=np.float64),
            lon=np.asarray(dataset["lon"].values, dtype=np.float64),
            levels=np.asarray(level.values, dtype=np.uint8),
        )


def level_rgba(levels: np.ndarray) -> np.ndarray:
    """Render a level array as an RGBA image using the JMA palette.

    Levels 0 (no data) and 1 (0 mm/h) are fully transparent.
    """
    palette = np.zeros((MAX_LEVEL + 1, 4), dtype=np.uint8)
    for level, color in enumerate(LEVEL_COLORS):
        palette[level, :3] = color
        palette[level, 3] = 0 if level < 2 else 255
    return palette[np.asarray(levels, dtype=np.intp)]


def write_png(
    grid: LatLonGrid, path: str | Path, *, background: tuple[int, int, int] | None = None
) -> Path:
    """Write a quick-look PNG of the level grid using the JMA palette.

    Args:
        grid: the resampled grid to render.
        path: output file path.
        background: optional opaque RGB background; ``None`` keeps transparency.
    """
    from PIL import Image

    rgba = level_rgba(grid.levels)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(rgba, mode="RGBA")
    if background is not None:
        canvas = Image.new("RGBA", image.size, (*background, 255))
        canvas.alpha_composite(image)
        image = canvas
    image.save(out)
    logger.info("wrote %s", out)
    return out


def write_geotiff(dataset: xr.Dataset, path: str | Path, *, variable: str = "rain_rate") -> Path:
    """Write one dataset variable to a GeoTIFF (requires the ``geotiff`` extra).

    Raises:
        ImportError: if ``rasterio`` is not installed.
    """
    try:
        import rasterio
        from rasterio.transform import from_origin
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise ImportError(
            "write_geotiff requires rasterio; install with `pip install 'jma-radar[geotiff]'`"
        ) from error

    data = dataset[variable].values
    lat = dataset["lat"].values
    lon = dataset["lon"].values
    if lat.size < 2 or lon.size < 2:
        raise ValueError("a GeoTIFF needs at least a 2x2 grid")
    dlon = float(lon[1] - lon[0])
    dlat = float(lat[0] - lat[1])
    transform = from_origin(float(lon[0]) - dlon / 2.0, float(lat[0]) + dlat / 2.0, dlon, dlat)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    dtype = "float32" if data.dtype.kind == "f" else "uint8"
    nodata: float | int = float("nan") if dtype == "float32" else 0
    with rasterio.open(
        out,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype=dtype,
        crs="EPSG:4326",
        transform=transform,
        nodata=nodata,
        compress="deflate",
    ) as dst:
        dst.write(data.astype(dtype), 1)
        dst.update_tags(**{k: str(v) for k, v in dataset.attrs.items()})
    logger.info("wrote %s", out)
    return out
