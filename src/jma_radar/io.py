"""Build xarray datasets and write NetCDF / GeoTIFF / PNG output."""

from __future__ import annotations

import datetime as dt
import logging
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
    "to_dataset",
    "write_geotiff",
    "write_netcdf",
    "write_png",
]

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
                "units": "mm h-1",
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


def write_netcdf(dataset: xr.Dataset, path: str | Path, *, compress: bool = True) -> Path:
    """Write ``dataset`` to a NetCDF4 file and return the path."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    encoding: dict[str, dict[str, Any]] = {}
    if compress:
        for name in dataset.data_vars:
            encoding[str(name)] = {"zlib": True, "complevel": 4}
    dataset.to_netcdf(out, format="NETCDF4", encoding=encoding)
    logger.info("wrote %s", out)
    return out


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
