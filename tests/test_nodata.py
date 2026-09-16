"""Tests for the hrpns_nd mask helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jma_radar.nodata import NODATA_GRID_DLAT, NODATA_GRID_DLON, nodata_mask, nodata_rings


def test_nodata_rings(fixtures_dir: Path) -> None:
    payload = (fixtures_dir / "hrpns_nd_20260916010500.geojson").read_bytes()
    rings = nodata_rings(payload)
    # One outer ring covering the globe plus at least one hole (the observed area).
    assert len(rings) >= 2
    outer = np.array(rings[0])
    assert outer[:, 0].max() - outer[:, 0].min() >= 360.0
    assert outer[:, 1].max() >= 85.0
    assert outer[:, 1].min() <= -85.0


def test_nodata_ring_vertices_follow_the_native_grid(fixtures_dir: Path) -> None:
    """Hole vertices are spaced on the 1/320 deg x 1/480 deg JMA analysis grid."""
    payload = (fixtures_dir / "hrpns_nd_20260916010500.geojson").read_bytes()
    hole = np.array(nodata_rings(payload)[1])
    assert hole.shape[0] > 1000
    for column, step in ((0, NODATA_GRID_DLON), (1, NODATA_GRID_DLAT)):
        spacing = np.diff(np.unique(hole[:, column])) / step
        assert np.abs(spacing - np.round(spacing)).max() < 1e-3
        assert spacing.min() >= 1.0 - 1e-3


def test_nodata_mask_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        nodata_mask(b'{"features": []}', np.array([0.0]), np.array([0.0]))
