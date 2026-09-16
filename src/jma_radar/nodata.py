"""Helpers around the ``hrpns_nd`` "no data" GeoJSON mask.

The mask is a single polygon whose outer ring covers the whole globe and whose
holes are the areas where radar data exists. Its vertices are spaced on the native
JMA 250 m analysis grid (1/320 deg in longitude, 1/480 deg in latitude).

Applying the mask is optional: tiles already encode unobserved areas as level 0,
so the first release only exposes parsing helpers.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

__all__ = ["NODATA_GRID_DLAT", "NODATA_GRID_DLON", "nodata_mask", "nodata_rings"]

#: Native analysis grid spacing implied by the spacing of the mask vertices.
NODATA_GRID_DLON = 1.0 / 320.0
NODATA_GRID_DLAT = 1.0 / 480.0


def nodata_rings(payload: str | bytes) -> list[list[tuple[float, float]]]:
    """Return every linear ring of the mask as a list of ``(lon, lat)`` pairs.

    The first ring of each polygon is the outer ring (the whole globe); the
    remaining rings are the holes, i.e. the areas that *do* have data.
    """
    data: dict[str, Any] = json.loads(payload)
    rings: list[list[tuple[float, float]]] = []
    for feature in data.get("features", []):
        geometry = feature.get("geometry") or {}
        geom_type = geometry.get("type")
        if geom_type == "Polygon":
            polygons = [geometry.get("coordinates", [])]
        elif geom_type == "MultiPolygon":
            polygons = list(geometry.get("coordinates", []))
        else:
            continue
        for polygon in polygons:
            for ring in polygon:
                rings.append([(float(pt[0]), float(pt[1])) for pt in ring])
    return rings


def nodata_mask(
    payload: str | bytes, lat: np.ndarray, lon: np.ndarray
) -> np.ndarray:  # pragma: no cover - not implemented yet
    """Rasterise the mask onto a lat/lon grid.

    Not implemented in this release; tile level 0 already marks unobserved
    pixels. Implementing it requires a point-in-polygon pass (``shapely`` or a
    scanline fill) over ~10^5 vertices.

    Raises:
        NotImplementedError: always.
    """
    raise NotImplementedError(
        "hrpns_nd rasterisation is not implemented; tile level 0 already marks no-data pixels"
    )
