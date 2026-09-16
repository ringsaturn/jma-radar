"""Tests for the level / Z-R tables."""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
import pytest

from jma_radar.levels import (
    LEVEL_BOUNDS,
    level_to_dbz,
    level_to_dbz_array,
    level_to_rain_rate,
    level_to_rain_rate_array,
    rain_rate_to_dbz,
)


def test_level_to_dbz_reference_values() -> None:
    expected = {2: 18.2, 3: 30.6, 4: 37.0, 5: 41.8, 6: 45.4, 7: 48.6, 8: 52.0, 9: 55.0}
    for level, value in expected.items():
        assert level_to_dbz(level) == pytest.approx(value, abs=0.05)


def test_nodata_and_zero_are_nan() -> None:
    assert math.isnan(level_to_dbz(0))
    assert math.isnan(level_to_dbz(1))
    assert math.isnan(level_to_rain_rate(0))
    assert level_to_rain_rate(1) == 0.0


def test_monotonic() -> None:
    values = [level_to_dbz(level) for level in range(2, 10)]
    assert all(b > a for a, b in pairwise(values))
    rates = [level_to_rain_rate(level) for level in range(1, 10)]
    assert all(b > a for a, b in pairwise(rates))


def test_boundary_dbz_values() -> None:
    # Values quoted in the plan for the class boundaries.
    expected = {1.0: 23.0, 5.0: 34.2, 10.0: 39.0, 20.0: 43.8, 30.0: 46.6, 50.0: 50.2, 80.0: 53.5}
    for rate, dbz in expected.items():
        assert rain_rate_to_dbz(rate) == pytest.approx(dbz, abs=0.05)


def test_custom_zr_parameters() -> None:
    default = level_to_dbz(3)
    custom = level_to_dbz(3, a=300.0, b=1.4)
    assert custom != pytest.approx(default)
    assert custom == pytest.approx(10 * math.log10(300.0) + 14 * math.log10(3.0))


def test_level_bounds_table() -> None:
    assert LEVEL_BOUNDS[2] == (0.25, 1.0)
    assert LEVEL_BOUNDS[9][1] == math.inf


def test_vectorised_helpers() -> None:
    levels = np.array([[0, 1, 2], [3, 8, 9]], dtype=np.uint8)
    rates = level_to_rain_rate_array(levels)
    assert rates.shape == levels.shape
    assert np.isnan(rates[0, 0])
    assert rates[0, 1] == 0.0
    assert rates[1, 2] == 100.0
    dbz = level_to_dbz_array(levels)
    assert np.isnan(dbz[0, 0]) and np.isnan(dbz[0, 1])
    assert dbz[1, 0] == pytest.approx(30.64, abs=0.01)


def test_invalid_level() -> None:
    with pytest.raises(ValueError):
        level_to_dbz(10)
    with pytest.raises(ValueError):
        level_to_rain_rate(-1)
    with pytest.raises(ValueError):
        level_to_dbz_array(np.array([12], dtype=np.uint8))
