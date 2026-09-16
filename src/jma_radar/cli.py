"""Command line interface for ``jma-radar``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from . import __version__, fetch_grid
from .client import DEFAULT_CONCURRENCY, JmaTileClient
from .constants import DEFAULT_ELEMENT, DEFAULT_ZR_A, DEFAULT_ZR_B, VALID_ZOOMS
from .io import to_dataset, write_geotiff, write_netcdf, write_png
from .tiles import domain_tile_range

app = typer.Typer(
    name="jma-radar",
    help=(
        "Build precipitation-intensity (mm/h) grids from JMA high-resolution "
        "precipitation nowcast tiles, optionally with a pseudo-dBZ field."
    ),
    no_args_is_help=True,
    add_completion=False,
)

logger = logging.getLogger("jma_radar")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs one INFO line per request, which would drown the progress bar.
    logging.getLogger("httpx").setLevel(logging.DEBUG if verbose else logging.WARNING)


def _parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    """Parse a ``W,S,E,N`` bounding box string."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise typer.BadParameter("bbox must be 'W,S,E,N'")
    try:
        west, south, east, north = (float(part) for part in parts)
    except ValueError as error:
        raise typer.BadParameter(f"bbox must contain four numbers: {error}") from error
    return west, south, east, north


def _parse_zr(value: str) -> tuple[float, float]:
    """Parse a ``a,b`` Z-R parameter string."""
    parts = value.split(",")
    if len(parts) != 2:
        raise typer.BadParameter("zr must be 'a,b', e.g. '200,1.6'")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError as error:
        raise typer.BadParameter(f"zr must contain two numbers: {error}") from error


@app.command()
def times(
    kind: Annotated[
        str, typer.Option(help="targetTimes list: N1 (analysis), N2 (forecast), N3.")
    ] = "N1",
    limit: Annotated[int, typer.Option(help="Maximum number of entries to print.")] = 10,
    element: Annotated[
        str, typer.Option(help="Only list entries carrying this element.")
    ] = DEFAULT_ELEMENT,
) -> None:
    """List available nowcast times (UTC)."""
    if kind not in ("N1", "N2", "N3"):
        raise typer.BadParameter("kind must be one of N1, N2, N3")
    with JmaTileClient() as client:
        entries = client.fetch_target_times(kind)  # type: ignore[arg-type]
    entries = [entry for entry in entries if entry.has_element(element)]
    typer.echo(f"{'basetime':<16}{'validtime':<16}{'lead':>6}  elements")
    for entry in entries[:limit]:
        typer.echo(
            f"{entry.basetime:<16}{entry.validtime:<16}{entry.lead_minutes:>4}m  "
            f"{','.join(entry.elements)}"
        )


@app.command()
def fetch(
    zoom: Annotated[int, typer.Option(help=f"Tile zoom level; one of {list(VALID_ZOOMS)}.")] = 8,
    time: Annotated[str, typer.Option(help="basetime YYYYMMDDHHMMSS, or 'latest'.")] = "latest",
    valid: Annotated[
        str | None, typer.Option(help="validtime YYYYMMDDHHMMSS for forecasts.")
    ] = None,
    bbox: Annotated[str | None, typer.Option(help="Subset as 'W,S,E,N' in degrees.")] = None,
    out: Annotated[
        Path | None, typer.Option(help="Output path; defaults to hrpns_{validtime}.<ext>.")
    ] = None,
    fmt: Annotated[str, typer.Option("--format", help="netcdf, geotiff or png.")] = "netcdf",
    cache_dir: Annotated[
        Path | None, typer.Option(help="Directory used to cache raw tiles.")
    ] = None,
    concurrency: Annotated[
        int, typer.Option(help="Parallel tile downloads.")
    ] = DEFAULT_CONCURRENCY,
    dbz: Annotated[
        bool, typer.Option("--dbz", help="Also write a pseudo-reflectivity 'cref' (dBZ) field.")
    ] = False,
    zr: Annotated[
        str, typer.Option(help="Z-R parameters 'a,b' for Z = a*R**b (only with --dbz).")
    ] = f"{DEFAULT_ZR_A:g},{DEFAULT_ZR_B:g}",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging.")] = False,
) -> None:
    """Download one nowcast frame and write it to a file."""
    _configure_logging(verbose)
    if zoom not in VALID_ZOOMS:
        raise typer.BadParameter(f"zoom must be one of {list(VALID_ZOOMS)}")
    if fmt not in ("netcdf", "geotiff", "png"):
        raise typer.BadParameter("format must be netcdf, geotiff or png")
    box = _parse_bbox(bbox)
    zr_a, zr_b = _parse_zr(zr)

    tile_range = domain_tile_range(zoom)
    logger.info("downloading %d tiles at zoom %d", tile_range.count, zoom)

    with typer.progressbar(length=tile_range.count, label="tiles") as bar:
        state = {"done": 0}

        def progress(done: int, total: int) -> None:
            bar.update(done - state["done"])
            state["done"] = done

        with JmaTileClient(concurrency=concurrency, cache_dir=cache_dir) as client:
            grid, target = fetch_grid(
                zoom,
                time=time,
                valid=valid,
                bbox=box,
                client=client,
                progress=progress,
            )

    suffix = {"netcdf": ".nc", "geotiff": ".tif", "png": ".png"}[fmt]
    path = out or Path(f"hrpns_{target.validtime}{suffix}")

    if fmt == "png":
        write_png(grid, path, background=(255, 255, 255))
    else:
        dataset = to_dataset(
            grid,
            basetime=target.basetime,
            validtime=target.validtime,
            zoom=zoom,
            dbz=dbz,
            zr_a=zr_a,
            zr_b=zr_b,
        )
        if fmt == "netcdf":
            write_netcdf(dataset, path)
        else:
            write_geotiff(dataset, path)

    valid_count = int((grid.levels > 0).sum())
    typer.echo(
        f"wrote {path} shape={grid.shape} basetime={target.basetime} "
        f"validtime={target.validtime} observed_cells={valid_count}"
    )


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
