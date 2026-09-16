# jma-radar

Build a **precipitation intensity (mm/h)** grid over Japan from the map tiles behind
the JMA "雨雲の動き" (rain cloud movement) page, and save it as NetCDF, GeoTIFF or a
quick-look PNG. An approximate reflectivity (dBZ) field can be added on request.

- Package name: `jma-radar` — import name: `jma_radar`
- Data source: 気象庁 高解像度降水ナウキャスト (`hrpns`) XYZ tiles,
  <https://www.jma.go.jp/bosai/en_nowc/>
- Output convention is kept close to the sibling project `nmc-radar-spider`
  (variables on `lat`/`lon`, latitude decreasing north → south).

## What the data is

JMA publishes these tiles as **precipitation intensity classes in mm/h**, the same
quantity shown in the web page legend. This package keeps that native unit as the
primary product:

1. decodes the class of every pixel (`level`, 0–9),
2. writes a representative rain rate per class as `rain_rate` (mm/h),
3. resamples from Web Mercator to a regular lat/lon grid with nearest neighbour.

Because the input is a class, `rain_rate` is quantised to nine discrete values. The class
bounds and the representative values are recorded in the variable attributes, so you can
re-map them to your own convention from the `level` field.

## Optional pseudo-dBZ (`--dbz`)

JMA does **not** publish radar reflectivity in these tiles. If you pass `--dbz` (CLI) or
`dbz=True` (library), a `cref` variable is added by converting the representative rain
rate with a Marshall–Palmer style relation `Z = a·R^b` (default `a = 200`, `b = 1.6`,
so `dBZ = 23.01 + 16·log10(R)`).

JMA internally uses several Z–R relations and rain-gauge calibration that cannot be
reversed from the tiles, so `cref` only *approximates* composite reflectivity. A
disclaimer is written into the NetCDF attributes whenever `cref` is present.
**Do not treat it as an observed reflectivity product.**

Areas with no data and areas with 0 mm/h are both `NaN` in `cref`; use `level` to tell
them apart (`level == 0` is no data, `level == 1` is a valid "no rain" observation).

## Palette / level table

Tiles are 4-bit palette PNGs with a fixed 10-entry palette. Decoding matches the palette
**colours** (not the raw index order) so a palette reshuffle by JMA cannot silently
corrupt the output; an unknown colour raises `PaletteError`.

| level | RGB | intensity (mm/h) | `rain_rate` (representative) | dBZ, only with `--dbz` |
|---|---|---|---|---|
| 0 | (255,255,255), transparent | no data / outside domain | — | NaN |
| 1 | (255,255,255), transparent | 0 | 0 | NaN |
| 2 | (242,242,255) | 0.25 – 1 | 0.5 | 18.2 |
| 3 | (160,210,255) | 1 – 5 | 3 | 30.6 |
| 4 | (33,140,255) | 5 – 10 | 7.5 | 37.0 |
| 5 | (0,65,255) | 10 – 20 | 15 | 41.8 |
| 6 | (250,245,0) | 20 – 30 | 25 | 45.4 |
| 7 | (255,153,0) | 30 – 50 | 40 | 48.6 |
| 8 | (255,40,0) | 50 – 80 | 65 | 52.0 |
| 9 | (180,0,104) | ≥ 80 | 100 | 55.0 |

Levels 0 and 1 share the same colour and transparency, so they are distinguished by
palette index. The JMA web page clips display at 0.25 mm/h.

Class boundaries in dBZ (Marshall–Palmer), for reference:

| R (mm/h) | 1 | 5 | 10 | 20 | 30 | 50 | 80 |
|---|---|---|---|---|---|---|---|
| dBZ | 23.0 | 34.2 | 39.0 | 43.8 | 46.6 | 50.2 | 53.5 |

## URL scheme

Times must always be read from the `targetTimes` listings first — guessing tile URLs
produces CDN-cached 404s.

```
https://www.jma.go.jp/bosai/jmatile/data/nowc/targetTimes_N1.json   # analyses, basetime == validtime
https://www.jma.go.jp/bosai/jmatile/data/nowc/targetTimes_N2.json   # forecasts, +5 … +60 min
https://www.jma.go.jp/bosai/jmatile/data/nowc/{basetime}/none/{validtime}/surf/hrpns/{z}/{x}/{y}.png
```

Timestamps are UTC, formatted `YYYYMMDDHHMMSS`. Tiles are standard Web Mercator
(EPSG:3857) 256×256 XYZ tiles with `y` increasing southwards.

Only **even** zooms carry data (`zoomUse="even"`, `minZoom=4`, `maxNativeZoom=10`):

| zoom | x range | y range | tiles | approx. resolution | output grid |
|---|---|---|---|---|---|
| 4 | 13–14 | 5–7 | 6 | ~9 km | 1/5° × 1/7.5° |
| 6 | 52–58 | 22–28 | 49 | ~2 km | 1/20° × 1/30° |
| 8 | 211–234 | 88–113 | 624 | ~600 m | 1/80° × 1/120° |
| 10 | 847–938 | 355–453 | 9108 | ~150 m | 1/320° × 1/480° |

Odd zooms and out-of-domain tiles return a 334-byte fully transparent RGBA PNG with
HTTP 200 (not a 404); these decode to level 0 everywhere. The analysis domain is
lat 20–48 °N, lon 118–150 °E.

## Install

This package is **not published on PyPI** for now. Install it straight from the
repository:

```bash
uv add git+https://github.com/ringsaturn/jma-radar
# or: pip install git+https://github.com/ringsaturn/jma-radar

# with optional extras (rasterio for GeoTIFF, matplotlib for plotting)
uv add 'jma-radar[geotiff,plot] @ git+https://github.com/ringsaturn/jma-radar'
```

Or from a local checkout:

```bash
git clone https://github.com/ringsaturn/jma-radar
cd jma-radar
uv sync --all-extras          # or: pip install -e '.[geotiff,plot]'
uv run jma-radar --help
```

Development:

```bash
uv sync --all-extras
uv run pytest
```

## CLI

```bash
# List available nowcast times (UTC)
jma-radar times --kind N1 --limit 10
jma-radar times --kind N2            # forecast validtimes, +5 .. +60 min

# Latest analysis over the whole domain -> NetCDF (rain_rate in mm/h + level)
jma-radar fetch --zoom 8 --out hrpns.nc

# Also include the pseudo-reflectivity field, optionally with a custom Z-R relation
jma-radar fetch --zoom 8 --dbz --out hrpns_dbz.nc
jma-radar fetch --zoom 8 --dbz --zr 300,1.4 --out hrpns_dbz.nc

# A specific frame, a sub-area, a tile cache
jma-radar fetch --zoom 6 \
    --time 20260916012000 \
    --bbox 128,30,146,46 \
    --cache-dir ~/.cache/jma-radar \
    --concurrency 6 \
    --out hrpns_kanto.nc

# A +30 min forecast frame
jma-radar fetch --zoom 6 --time 20260916012000 --valid 20260916015000

# Other formats
jma-radar fetch --zoom 6 --format png     --out preview.png
jma-radar fetch --zoom 6 --format geotiff --out hrpns.tif  # rain_rate band; needs the 'geotiff' extra
```

`--zoom` accepts 4, 6, 8 or 10 only; the default is 8. Without `--out`, the file is
named `hrpns_{validtime}.{nc,tif,png}`.

## Library

```python
import jma_radar

# One call: download the latest frame and get an xarray Dataset
ds = jma_radar.fetch_dataset(zoom=6)
print(ds.rain_rate.shape, ds.attrs["validtime"])
jma_radar.write_netcdf(ds, "hrpns.nc")

# With the optional pseudo-dBZ field
ds = jma_radar.fetch_dataset(zoom=6, dbz=True, zr=(200.0, 1.6))
print(float(ds.cref.max()))

# Or drive the steps yourself
from jma_radar import JmaTileClient, assemble_mosaic, domain_tile_range, to_latlon_grid

with JmaTileClient(cache_dir="~/.cache/jma-radar", concurrency=6) as client:
    target = client.latest_target_time()  # never guess URLs
    tile_range = domain_tile_range(6)
    tiles = client.fetch_tiles(target.basetime, target.validtime, tile_range)

mosaic = assemble_mosaic(tiles, tile_range)  # Web Mercator level array
grid = to_latlon_grid(mosaic, tile_range)  # nearest-neighbour -> lat/lon
ds = jma_radar.to_dataset(grid, basetime=target.basetime, validtime=target.validtime, zoom=6)
jma_radar.write_png(grid, "preview.png")  # quick-look, JMA colours
```

Useful pieces: `decode_tile`, `level_to_dbz`, `level_to_rain_rate`, `rain_rate_to_dbz`,
`make_grid`, `tile_bounds`, `parse_target_times`, `LEVEL_COLORS`, `LEVEL_LABELS`.

## NetCDF output

Dimensions `lat` (decreasing, north → south) and `lon`; coordinates are cell centres.

| variable | dtype | units | notes |
|---|---|---|---|
| `rain_rate` | float32 | mm h-1 | primary product; class representative value; `NaN` for no data, `0` for no rain; attrs `class_bounds`, `class_representative_rain_rate` |
| `level` | uint8 | 1 | `flag_values` 0…9, `flag_meanings` per the table above; 0 = no data |
| `cref` | float32 | dBZ | only with `--dbz` / `dbz=True`; `NaN` for no data **and** for 0 mm/h; attrs `zr_a`, `zr_b` |

Global attributes: `title`, `product`, `source` (URL template), `element`, `basetime`,
`validtime` (ISO 8601 UTC), `zoom`, `crs` (`EPSG:4326`), `Conventions`, `institution`,
`disclaimer`, `created`.

Resampling from Web Mercator to the lat/lon grid is **nearest neighbour** on purpose:
the values are discrete classes and must not be averaged.

## Polite usage

The JMA tile service is a free public service on a CDN. Please:

- keep the default concurrency (6, never more than ~8) and the default retry/backoff;
- send an identifying `User-Agent` (the client does by default);
- use `--cache-dir` so repeated runs on the same frame hit the disk cache —
  frames are immutable and `cache-control: max-age=86400`;
- do not poll in a tight loop: analyses are published every 5 minutes;
- prefer the lowest zoom that satisfies your need (z10 is 9108 tiles per frame).

## Not implemented / notes

- `hrpns_nd` (the "no data" GeoJSON mask) is parsed by `jma_radar.nodata.nodata_rings`
  but not rasterised; tile level 0 already marks unobserved pixels.
- `colordepth:{thin,normal,deep}` only changes the web page opacity; the tile payloads
  are identical, so it is ignored.
- Other nowcast elements (`thns` lightning, `trns` tornado, `slmcs` discharge points)
  are out of scope.

## References

- <https://www.jma.go.jp/bosai/en_nowc/> and its `nowc.properties.xml`
- <https://qiita.com/e_toyoda/items/7a293313a725c4d306c0> (URL structure; do not guess URLs)
- <https://github.com/kikuchan/jmamap>, <https://github.com/Kanahiro/jma-utils>
- 気象庁 測候時報 81 巻 p.55 (high-resolution precipitation nowcast algorithm)

## License

The code in this repository is released under the [MIT License](LICENSE).

### Data source and attribution

This package does not bundle any weather data. Everything it downloads comes
from the Japan Meteorological Agency (JMA, 気象庁) website and remains subject
to the [JMA website terms of use](https://www.jma.go.jp/jma/kishou/info/coment.html)
(気象庁ホームページ利用規約), which follow the Japanese Government Standard
Terms of Use v2.0 and are compatible with CC BY 4.0.

If you publish, redistribute, or display anything derived from the output of
this package, you must credit the source, for example:

- English: `Source: Japan Meteorological Agency website`
- Japanese: `出典：気象庁ホームページ`

and note that the data has been processed (e.g. "regridded by jma-radar" or
"converted to pseudo dBZ by jma-radar") when you present a modified form. The `source` and `disclaimer`
attributes written into every NetCDF file exist to help with this.

The `rain_rate` values are class representative values, not the exact rates JMA
measured, and the optional dBZ values are an approximation derived from those
classes, not JMA radar reflectivity. Do not present either as official JMA
measurements, and do not use them for safety-critical decisions. Please also keep request rates modest; this is a public service, not
an API intended for bulk mirroring.
