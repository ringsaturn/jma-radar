"""Tests for dataset construction and file output."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from PIL import Image

from jma_radar.io import DBZ_DISCLAIMER, DISCLAIMER, to_dataset, write_netcdf, write_png
from jma_radar.mosaic import LatLonGrid

BASETIME = "20260916010500"


def _grid() -> LatLonGrid:
    lat = np.array([40.0, 39.0, 38.0])
    lon = np.array([135.0, 136.0])
    levels = np.array([[0, 1], [2, 3], [8, 9]], dtype=np.uint8)
    return LatLonGrid(lat=lat, lon=lon, levels=levels)


def test_to_dataset_structure() -> None:
    dataset = to_dataset(_grid(), basetime=BASETIME, validtime=BASETIME, zoom=6)
    # mm/h is the primary product; dBZ is opt-in.
    assert set(dataset.data_vars) == {"level", "rain_rate"}
    assert dataset["level"].dtype == np.uint8
    assert dataset["rain_rate"].dtype == np.float32
    assert dataset["rain_rate"].dims == ("lat", "lon")
    assert dataset["rain_rate"].attrs["units"] == "mm h-1"
    assert dataset.attrs["crs"] == "EPSG:4326"
    assert dataset.attrs["basetime"] == "2026-09-16T01:05:00Z"
    assert dataset.attrs["zoom"] == 6
    assert dataset.attrs["disclaimer"] == DISCLAIMER
    assert "Pseudo reflectivity" not in dataset.attrs["disclaimer"]
    assert dataset["level"].attrs["flag_meanings"].split()[0] == "no_data"
    assert list(dataset["level"].attrs["flag_values"]) == list(range(10))


def test_rain_rate_values() -> None:
    dataset = to_dataset(_grid(), basetime=BASETIME, validtime=BASETIME, zoom=6)
    rain = dataset["rain_rate"].values
    assert np.isnan(rain[0, 0])  # level 0, no data
    assert rain[0, 1] == 0.0  # level 1, no precipitation
    assert rain[1, 0] == 0.5
    assert rain[1, 1] == 3.0
    assert rain[2, 1] == 100.0


def test_dbz_opt_in() -> None:
    dataset = to_dataset(_grid(), basetime=BASETIME, validtime=BASETIME, zoom=6, dbz=True)
    assert set(dataset.data_vars) == {"cref", "level", "rain_rate"}
    assert dataset["cref"].dtype == np.float32
    assert dataset.attrs["disclaimer"] == DBZ_DISCLAIMER
    cref = dataset["cref"].values
    assert np.isnan(cref[0, 0])  # level 0, no data
    assert np.isnan(cref[0, 1])  # level 1, 0 mm/h
    assert cref[1, 1] == pytest.approx(30.64, abs=0.01)


def test_custom_zr_parameters_recorded() -> None:
    dataset = to_dataset(
        _grid(), basetime=BASETIME, validtime=BASETIME, zoom=6, dbz=True, zr_a=300, zr_b=1.4
    )
    assert dataset["cref"].attrs["zr_a"] == pytest.approx(300.0)
    assert dataset["cref"].attrs["zr_b"] == pytest.approx(1.4)
    assert dataset["cref"].values[1, 1] == pytest.approx(31.45, abs=0.05)


def test_netcdf_round_trip(tmp_path: Path) -> None:
    dataset = to_dataset(_grid(), basetime=BASETIME, validtime=BASETIME, zoom=6, dbz=True)
    path = write_netcdf(dataset, tmp_path / "hrpns.nc")
    assert path.is_file()
    with xr.open_dataset(path) as loaded:
        assert loaded["rain_rate"].dtype == np.float32
        assert loaded["cref"].dtype == np.float32
        assert loaded["level"].dtype == np.uint8
        assert np.isnan(loaded["rain_rate"].values[0, 0])
        assert loaded["rain_rate"].values[1, 1] == 3.0
        assert np.isnan(loaded["cref"].values[0, 0])
        assert loaded["cref"].values[1, 1] == pytest.approx(30.64, abs=0.01)
        assert loaded.attrs["product"].startswith("JMA High-resolution")
        assert loaded.attrs["validtime"] == "2026-09-16T01:05:00Z"
        assert loaded["lat"].values[0] > loaded["lat"].values[-1]
        assert np.array_equal(loaded["level"].values, _grid().levels)


def test_write_png(tmp_path: Path) -> None:
    path = write_png(_grid(), tmp_path / "preview.png")
    with Image.open(path) as image:
        assert image.mode == "RGBA"
        rgba = np.asarray(image)
    assert rgba.shape == (3, 2, 4)
    assert rgba[0, 0, 3] == 0  # no data is transparent
    assert rgba[0, 1, 3] == 0  # 0 mm/h is transparent
    assert tuple(rgba[1, 1]) == (160, 210, 255, 255)  # level 3
    assert tuple(rgba[2, 1]) == (180, 0, 104, 255)  # level 9


def test_write_png_with_background(tmp_path: Path) -> None:
    path = write_png(_grid(), tmp_path / "preview_bg.png", background=(255, 255, 255))
    with Image.open(path) as image:
        rgba = np.asarray(image)
    assert tuple(rgba[0, 0]) == (255, 255, 255, 255)


def test_write_geotiff(tmp_path: Path) -> None:
    rasterio = pytest.importorskip("rasterio")
    dataset = to_dataset(_grid(), basetime=BASETIME, validtime=BASETIME, zoom=6)
    from jma_radar.io import write_geotiff

    path = write_geotiff(dataset, tmp_path / "hrpns.tif")
    with rasterio.open(path) as src:
        assert src.count == 1
        assert src.crs.to_string() == "EPSG:4326"
        assert src.width == 2
        assert src.height == 3
        data = src.read(1)
    assert np.isnan(data[0, 0])
    assert data[1, 1] == 3.0  # rain_rate band by default
