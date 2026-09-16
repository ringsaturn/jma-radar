"""A window of analyses as one series: the shape a rolling publish reads.

The ``targetTimes_N1`` listing names the last three hours of five-minute
analyses. A *window* is the analyses whose ``basetime`` falls between a
start hour and ``hours`` past it (inclusive), each brought onto one lat/lon
grid and stacked along ``time`` (:func:`jma_radar.io.to_series_dataset`).

Frames are cached one file each (the ordinary single-frame NetCDF) under a
directory keyed by the grid they were resampled onto, so a window rebuilt
every few minutes downloads only the frame the listing gained since the
last build, and a window that overlaps the previous one shares its files.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .client import DEFAULT_CONCURRENCY, JmaTileClient
from .constants import DEFAULT_ELEMENT
from .io import read_levels, to_dataset, write_netcdf
from .mosaic import LatLonGrid, ResampleMethod, assemble_mosaic, grid_steps, to_latlon_grid
from .tiles import check_zoom, domain_tile_range
from .times import TargetTime, parse_time

__all__ = ["GridSpec", "WindowFrame", "fetch_window", "parse_start", "window_target_times"]

logger = logging.getLogger(__name__)


def parse_start(value: str | dt.datetime) -> dt.datetime:
    """A window start as an aware UTC datetime.

    Accepts a datetime, a JMA ``YYYYMMDDHHMMSS`` stamp, or the ``YYYYMMDDHH``
    run-hour form a publisher names a window by.
    """
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)
    text = value.strip()
    if len(text) == 10 and text.isdigit():
        text += "0000"
    if len(text) == 12 and text.isdigit():
        text += "00"
    return parse_time(text)


@dataclass(frozen=True, slots=True)
class GridSpec:
    """The grid every frame of a window lands on: what the frame cache is
    keyed by, since a frame resampled onto another grid is another file."""

    zoom: int
    dlon: float
    dlat: float
    bbox: tuple[float, float, float, float] | None
    method: ResampleMethod

    @property
    def key(self) -> str:
        """A short stable id of the grid, for the cache directory name."""
        payload = json.dumps(
            {
                "zoom": self.zoom,
                "dlon": self.dlon,
                "dlat": self.dlat,
                "bbox": list(self.bbox) if self.bbox else None,
                "method": self.method,
            },
            sort_keys=True,
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]

    def to_json(self) -> dict[str, object]:
        return {
            "zoom": self.zoom,
            "dlon": self.dlon,
            "dlat": self.dlat,
            "bbox": list(self.bbox) if self.bbox else None,
            "method": self.method,
        }


@dataclass(frozen=True, slots=True)
class WindowFrame:
    """One analysis of a window: its listing entry, its grid, and the cache
    file it was read from or written to (``None`` without a cache)."""

    target: TargetTime
    grid: LatLonGrid
    path: Path | None

    @property
    def validtime(self) -> str:
        return self.target.validtime


def window_target_times(
    entries: list[TargetTime],
    start: dt.datetime,
    hours: int,
    element: str = DEFAULT_ELEMENT,
) -> list[TargetTime]:
    """The analyses of a window, oldest first: every listing entry carrying
    ``element`` whose ``basetime`` (equal to its ``validtime``, an analysis)
    lies in ``[start, start + hours]``. Forecast entries are skipped."""
    if hours < 1:
        raise ValueError("a window must be at least an hour long")
    start = parse_start(start)
    end = start + dt.timedelta(hours=hours)
    chosen = {
        entry.validtime: entry
        for entry in entries
        if entry.has_element(element)
        and not entry.is_forecast
        and start <= entry.validtime_dt <= end
    }
    return [chosen[key] for key in sorted(chosen)]


def _frame_path(frames_dir: Path, spec: GridSpec, validtime: str, element: str) -> Path:
    return frames_dir / spec.key / f"{element}_{validtime}.nc"


def _write_grid_record(frames_dir: Path, spec: GridSpec) -> None:
    record = frames_dir / spec.key / "grid.json"
    if not record.exists():
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps(spec.to_json(), indent=2) + "\n", encoding="utf-8")


def fetch_window(
    start: str | dt.datetime,
    hours: int,
    *,
    zoom: int = 8,
    step: float | tuple[float, float] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    method: ResampleMethod = "nearest",
    frames_dir: str | Path | None = None,
    element: str = DEFAULT_ELEMENT,
    concurrency: int = DEFAULT_CONCURRENCY,
    cache_dir: str | Path | None = None,
    client: JmaTileClient | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[list[WindowFrame], GridSpec]:
    """Fetch every analysis of a window onto one grid.

    Args:
        start: the window's first hour (``YYYYMMDDHH``, a JMA stamp, or a
            datetime).
        hours: the window's length; analyses through ``start + hours`` are
            taken, inclusive.
        zoom: tile zoom level, one of :data:`~jma_radar.constants.VALID_ZOOMS`.
        step: output grid spacing in degrees, one number for a square grid
            or ``(dlon, dlat)``; the zoom's native spacing by default.
        bbox: optional ``(west, south, east, north)`` subset.
        method: ``nearest`` or ``max`` (:data:`~jma_radar.mosaic.ResampleMethod`).
        frames_dir: directory to cache one file per frame in; a frame
            already there is read rather than fetched.
        element: tile element id, ``hrpns`` by default.
        concurrency: parallel tile downloads.
        cache_dir: optional raw tile cache handed to the client.
        client: an existing client to reuse.
        progress: optional ``(done, total)`` callback over the window's
            frames (a frame served from the cache counts as done at once).

    Returns:
        The frames, oldest first, and the grid they are on. An empty list
        means the listing holds no analysis in the window.
    """
    check_zoom(zoom)
    if step is None:
        dlon, dlat = grid_steps(zoom)
    elif isinstance(step, (int, float)):
        dlon = dlat = float(step)
    else:
        dlon, dlat = (float(step[0]), float(step[1]))
    if dlon <= 0 or dlat <= 0:
        raise ValueError(f"grid step must be positive, got {(dlon, dlat)}")
    spec = GridSpec(zoom=zoom, dlon=dlon, dlat=dlat, bbox=bbox, method=method)
    cache = Path(frames_dir).expanduser() if frames_dir is not None else None

    owned = client is None
    active = client or JmaTileClient(concurrency=concurrency, cache_dir=cache_dir)
    try:
        listed = active.fetch_target_times("N1")
        targets = window_target_times(listed, parse_start(start), hours, element=element)
        logger.info(
            "window %s +%d h: %d of %d listed analyses", start, hours, len(targets), len(listed)
        )
        tile_range = domain_tile_range(zoom)
        frames: list[WindowFrame] = []
        for index, target in enumerate(targets, start=1):
            path = (
                _frame_path(cache, spec, target.validtime, element) if cache is not None else None
            )
            if path is not None and path.is_file():
                logger.debug("frame %s from cache %s", target.validtime, path)
                grid = read_levels(path)
            else:
                tiles = active.fetch_tiles(
                    target.basetime, target.validtime, tile_range, element=element
                )
                mosaic = assemble_mosaic(tiles, tile_range)
                grid = to_latlon_grid(
                    mosaic, tile_range, bbox=bbox, dlon=dlon, dlat=dlat, method=method
                )
                if path is not None:
                    _write_grid_record(cache, spec)  # type: ignore[arg-type]
                    dataset = to_dataset(
                        grid,
                        basetime=target.basetime,
                        validtime=target.validtime,
                        zoom=zoom,
                        element=element,
                    )
                    dataset.attrs["resampling"] = method
                    write_netcdf(dataset, path)
            frames.append(WindowFrame(target=target, grid=grid, path=path))
            if progress is not None:
                progress(index, len(targets))
        return frames, spec
    finally:
        if owned:
            active.close()
