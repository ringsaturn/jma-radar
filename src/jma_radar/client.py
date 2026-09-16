"""HTTP client for the JMA nowcast tile service."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType

import httpx
import numpy as np

from .constants import (
    DEFAULT_ELEMENT,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    NODATA_GEOJSON_URL_TEMPLATE,
    TARGET_TIMES_URL_TEMPLATE,
    TILE_URL_TEMPLATE,
)
from .decode import decode_tile, empty_tile
from .tiles import TileRange, check_zoom
from .times import TargetTime, TimeKind, latest, parse_target_times

__all__ = ["DEFAULT_CONCURRENCY", "JmaTileClient", "tile_url"]

logger = logging.getLogger(__name__)

#: Default number of parallel tile downloads. Stay polite: JMA is a public service.
DEFAULT_CONCURRENCY = 6

#: Default number of attempts per request.
DEFAULT_RETRIES = 3


def tile_url(
    basetime: str,
    validtime: str,
    z: int,
    x: int,
    y: int,
    element: str = DEFAULT_ELEMENT,
) -> str:
    """Build a tile URL. ``basetime``/``validtime`` must come from ``targetTimes``."""
    return TILE_URL_TEMPLATE.format(
        basetime=basetime, validtime=validtime, element=element, z=z, x=x, y=y
    )


class JmaTileClient:
    """Thin, polite ``httpx`` wrapper around the JMA nowcast tile endpoints.

    The client never guesses URLs: callers are expected to obtain
    ``basetime``/``validtime`` from :meth:`fetch_target_times`.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = 0.5,
        cache_dir: str | Path | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        client: httpx.Client | None = None,
    ) -> None:
        self.retries = max(1, retries)
        self.backoff = backoff
        self.concurrency = max(1, concurrency)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "*/*"},
            timeout=timeout,
            follow_redirects=True,
        )

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        """Close the underlying HTTP client if this instance owns it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> JmaTileClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- low level ---------------------------------------------------------
    def _get(self, url: str) -> httpx.Response:
        """GET ``url`` with retries and exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self._client.get(url)
            except httpx.HTTPError as error:  # network level failure
                last_error = error
                logger.warning(
                    "request failed (%s/%s) %s: %s", attempt + 1, self.retries, url, error
                )
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                logger.warning(
                    "server error (%s/%s) %s: %s",
                    attempt + 1,
                    self.retries,
                    url,
                    response.status_code,
                )
            if attempt + 1 < self.retries:
                time.sleep(self.backoff * (2**attempt))
        assert last_error is not None
        raise last_error

    # -- target times ------------------------------------------------------
    def fetch_target_times(self, kind: TimeKind = "N1") -> list[TargetTime]:
        """Fetch and parse ``targetTimes_{kind}.json``."""
        response = self._get(TARGET_TIMES_URL_TEMPLATE.format(kind=kind))
        response.raise_for_status()
        return parse_target_times(response.content)

    def latest_target_time(
        self, kind: TimeKind = "N1", element: str = DEFAULT_ELEMENT
    ) -> TargetTime:
        """Fetch ``targetTimes`` and return the newest entry carrying ``element``."""
        return latest(self.fetch_target_times(kind), element=element)

    def resolve_target_time(
        self,
        *,
        time: str | None = None,
        valid: str | None = None,
        kind: TimeKind | None = None,
        element: str = DEFAULT_ELEMENT,
    ) -> TargetTime:
        """Resolve a user request to a concrete ``targetTimes`` entry.

        Args:
            time: ``basetime`` as ``YYYYMMDDHHMMSS``, or ``None``/``"latest"``.
            valid: optional ``validtime`` filter (forecast lead selection).
            kind: which ``targetTimes`` document to consult; ``None`` searches
                the analysis list (``N1``) first and then the forecast list
                (``N2``).
            element: element the entry must advertise.

        Raises:
            ValueError: if nothing matches the request.
        """
        kinds: tuple[TimeKind, ...] = (kind,) if kind is not None else ("N1", "N2")
        for candidate in kinds:
            entries = [e for e in self.fetch_target_times(candidate) if e.has_element(element)]
            if valid is not None:
                entries = [e for e in entries if e.validtime == valid]
            if time is not None and time != "latest":
                entries = [e for e in entries if e.basetime == time]
            if entries:
                return latest(entries)
        raise ValueError(
            f"no targetTimes entry in {list(kinds)} for basetime={time!r} "
            f"validtime={valid!r} element={element!r}"
        )

    # -- tiles -------------------------------------------------------------
    def _cache_path(
        self, basetime: str, validtime: str, z: int, x: int, y: int, element: str
    ) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / element / basetime / validtime / str(z) / str(x) / f"{y}.png"

    def fetch_tile_bytes(
        self,
        basetime: str,
        validtime: str,
        z: int,
        x: int,
        y: int,
        element: str = DEFAULT_ELEMENT,
    ) -> bytes | None:
        """Fetch one tile, returning ``None`` when the tile does not exist (404)."""
        path = self._cache_path(basetime, validtime, z, x, y, element)
        if path is not None and path.is_file():
            return path.read_bytes()
        url = tile_url(basetime, validtime, z, x, y, element=element)
        response = self._get(url)
        if response.status_code == 404:
            logger.debug("tile missing: %s", url)
            return None
        response.raise_for_status()
        data = response.content
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return data

    def fetch_tile(
        self,
        basetime: str,
        validtime: str,
        z: int,
        x: int,
        y: int,
        element: str = DEFAULT_ELEMENT,
    ) -> np.ndarray:
        """Fetch and decode one tile into a ``(256, 256)`` level array."""
        data = self.fetch_tile_bytes(basetime, validtime, z, x, y, element=element)
        if data is None:
            return empty_tile()
        return decode_tile(data)

    def fetch_tiles(
        self,
        basetime: str,
        validtime: str,
        tile_range: TileRange,
        element: str = DEFAULT_ELEMENT,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[tuple[int, int], np.ndarray]:
        """Fetch every tile of ``tile_range`` concurrently.

        Returns:
            Mapping of ``(x, y)`` to decoded ``(256, 256)`` level arrays.
        """
        check_zoom(tile_range.z)
        coords: list[tuple[int, int]] = list(tile_range)
        total = len(coords)
        results: dict[tuple[int, int], np.ndarray] = {}

        def worker(coord: tuple[int, int]) -> tuple[tuple[int, int], np.ndarray]:
            x, y = coord
            return coord, self.fetch_tile(basetime, validtime, tile_range.z, x, y, element=element)

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for done, (coord, levels) in enumerate(pool.map(worker, coords), start=1):
                results[coord] = levels
                if progress is not None:
                    progress(done, total)
        return results

    def fetch_nodata_geojson(self, basetime: str, validtime: str) -> bytes:
        """Fetch the raw ``hrpns_nd`` GeoJSON payload (gzip handled by httpx)."""
        response = self._get(
            NODATA_GEOJSON_URL_TEMPLATE.format(basetime=basetime, validtime=validtime)
        )
        response.raise_for_status()
        return response.content
