"""Static constants describing the JMA nowcast tile service and its palette."""

from __future__ import annotations

from typing import Final

__all__ = [
    "BASE_URL",
    "DEFAULT_ELEMENT",
    "DEFAULT_TIMEOUT",
    "DEFAULT_USER_AGENT",
    "DEFAULT_ZR_A",
    "DEFAULT_ZR_B",
    "DOMAIN_MAX_LAT",
    "DOMAIN_MAX_LON",
    "DOMAIN_MIN_LAT",
    "DOMAIN_MIN_LON",
    "FLAG_MEANINGS",
    "GRID_STEPS",
    "LEVEL_COLORS",
    "LEVEL_LABELS",
    "MAX_LEVEL",
    "NODATA_GEOJSON_URL_TEMPLATE",
    "PRODUCT_NAME",
    "TARGET_TIMES_URL_TEMPLATE",
    "TILE_SIZE",
    "TILE_URL_TEMPLATE",
    "VALID_ZOOMS",
]

#: Root of the JMA nowcast tile service.
BASE_URL: Final = "https://www.jma.go.jp/bosai/jmatile/data/nowc"

#: Tile URL template. ``basetime``/``validtime`` must come from ``targetTimes``.
TILE_URL_TEMPLATE: Final = BASE_URL + "/{basetime}/none/{validtime}/surf/{element}/{z}/{x}/{y}.png"

#: ``targetTimes`` URL template, ``kind`` is one of ``N1``, ``N2``, ``N3``.
TARGET_TIMES_URL_TEMPLATE: Final = BASE_URL + "/targetTimes_{kind}.json"

#: The "no data" mask is a GeoJSON document rather than a tile set.
NODATA_GEOJSON_URL_TEMPLATE: Final = (
    BASE_URL + "/{basetime}/none/{validtime}/surf/hrpns_nd/data.geojson"
)

#: High-resolution precipitation nowcast element id.
DEFAULT_ELEMENT: Final = "hrpns"

PRODUCT_NAME: Final = "JMA High-resolution Precipitation Nowcast (hrpns)"

#: Tiles are standard 256x256 Web Mercator (EPSG:3857) XYZ tiles.
TILE_SIZE: Final = 256

#: Only even zooms in this range actually carry data (``zoomUse="even"``,
#: ``minZoom=4``, ``maxNativeZoom=10``). Odd zooms return fully transparent RGBA.
VALID_ZOOMS: Final[tuple[int, ...]] = (4, 6, 8, 10)

# Data area declared by ``nowc.properties.xml``.
DOMAIN_MIN_LAT: Final = 20.0
DOMAIN_MAX_LAT: Final = 48.0
DOMAIN_MIN_LON: Final = 118.0
DOMAIN_MAX_LON: Final = 150.0

#: Highest precipitation class index.
MAX_LEVEL: Final = 9

#: Palette RGB triplets, indexed by precipitation level.
#:
#: Levels 0 and 1 share the same RGB value and are both transparent in the
#: ``tRNS`` chunk; they are told apart by palette index only (0 = no data,
#: 1 = 0 mm/h). Levels >= 2 have unique colours and are matched by colour.
LEVEL_COLORS: Final[tuple[tuple[int, int, int], ...]] = (
    (255, 255, 255),  # 0 no data / outside domain
    (255, 255, 255),  # 1 no precipitation (0 mm/h)
    (242, 242, 255),  # 2 [0.25, 1)
    (160, 210, 255),  # 3 [1, 5)
    (33, 140, 255),  # 4 [5, 10)
    (0, 65, 255),  # 5 [10, 20)
    (250, 245, 0),  # 6 [20, 30)
    (255, 153, 0),  # 7 [30, 50)
    (255, 40, 0),  # 8 [50, 80)
    (180, 0, 104),  # 9 >= 80
)

#: Human readable class labels (mm/h).
LEVEL_LABELS: Final[tuple[str, ...]] = (
    "no data",
    "0 mm/h",
    "0.25-1 mm/h",
    "1-5 mm/h",
    "5-10 mm/h",
    "10-20 mm/h",
    "20-30 mm/h",
    "30-50 mm/h",
    "50-80 mm/h",
    ">= 80 mm/h",
)

#: CF ``flag_meanings`` tokens for the ``level`` variable.
FLAG_MEANINGS: Final[tuple[str, ...]] = (
    "no_data",
    "no_precipitation",
    "0.25_to_1_mm_per_hour",
    "1_to_5_mm_per_hour",
    "5_to_10_mm_per_hour",
    "10_to_20_mm_per_hour",
    "20_to_30_mm_per_hour",
    "30_to_50_mm_per_hour",
    "50_to_80_mm_per_hour",
    "80_or_more_mm_per_hour",
)

#: Marshall-Palmer Z-R relation: ``Z = a * R ** b``.
DEFAULT_ZR_A: Final = 200.0
DEFAULT_ZR_B: Final = 1.6

#: Output grid spacing (degrees) per zoom, as ``(dlon, dlat)``.
#:
#: The native JMA analysis grid is 1/320 deg in longitude and 1/480 deg in
#: latitude; coarser zooms use integer multiples of that spacing.
GRID_STEPS: Final[dict[int, tuple[float, float]]] = {
    4: (1.0 / 5.0, 1.0 / 7.5),
    6: (1.0 / 20.0, 1.0 / 30.0),
    8: (1.0 / 80.0, 1.0 / 120.0),
    10: (1.0 / 320.0, 1.0 / 480.0),
}

DEFAULT_USER_AGENT: Final = (
    "jma-radar/0.2.0 (+https://github.com/ringsaturn/jma-radar) python-httpx"
)

DEFAULT_TIMEOUT: Final = 20.0
