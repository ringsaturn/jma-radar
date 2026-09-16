"""Precipitation class (level) tables and Z-R conversions."""

from __future__ import annotations

import math

import numpy as np

from .constants import DEFAULT_ZR_A, DEFAULT_ZR_B, MAX_LEVEL

__all__ = [
    "LEVEL_BOUNDS",
    "LEVEL_REPRESENTATIVE_RAIN_RATE",
    "level_to_dbz",
    "level_to_dbz_array",
    "level_to_rain_rate",
    "level_to_rain_rate_array",
    "rain_rate_to_dbz",
]

#: ``(lower, upper)`` mm/h bounds of every level. ``inf`` means open ended,
#: ``nan`` marks the "no data" class.
LEVEL_BOUNDS: tuple[tuple[float, float], ...] = (
    (math.nan, math.nan),  # 0 no data
    (0.0, 0.25),  # 1 no precipitation (reported as 0 mm/h)
    (0.25, 1.0),
    (1.0, 5.0),
    (5.0, 10.0),
    (10.0, 20.0),
    (20.0, 30.0),
    (30.0, 50.0),
    (50.0, 80.0),
    (80.0, math.inf),
)

#: Representative rain rate (mm/h) used for each level.
LEVEL_REPRESENTATIVE_RAIN_RATE: tuple[float, ...] = (
    math.nan,  # 0 no data
    0.0,  # 1 no precipitation
    0.5,
    3.0,
    7.5,
    15.0,
    25.0,
    40.0,
    65.0,
    100.0,
)


def _check_level(level: int) -> int:
    if not 0 <= level <= MAX_LEVEL:
        raise ValueError(f"level must be between 0 and {MAX_LEVEL}, got {level}")
    return level


def level_to_rain_rate(level: int) -> float:
    """Return the representative rain rate (mm/h) for a level.

    Level 0 (no data) maps to ``nan``; level 1 maps to ``0.0``.
    """
    _check_level(level)
    return LEVEL_REPRESENTATIVE_RAIN_RATE[level]


def rain_rate_to_dbz(rain_rate: float, a: float = DEFAULT_ZR_A, b: float = DEFAULT_ZR_B) -> float:
    """Convert a rain rate (mm/h) to reflectivity (dBZ) with ``Z = a * R ** b``.

    A rain rate of 0 (or ``nan``) yields ``nan`` because ``Z`` would be 0.
    """
    if math.isnan(rain_rate) or rain_rate <= 0.0:
        return math.nan
    return 10.0 * math.log10(a) + 10.0 * b * math.log10(rain_rate)


def level_to_dbz(level: int, a: float = DEFAULT_ZR_A, b: float = DEFAULT_ZR_B) -> float:
    """Return the pseudo reflectivity (dBZ) for a level.

    Levels 0 (no data) and 1 (0 mm/h) both yield ``nan``.
    """
    return rain_rate_to_dbz(level_to_rain_rate(level), a=a, b=b)


def level_to_rain_rate_array(levels: np.ndarray) -> np.ndarray:
    """Vectorised :func:`level_to_rain_rate` over an array of levels."""
    table = np.asarray(LEVEL_REPRESENTATIVE_RAIN_RATE, dtype=np.float32)
    idx = np.asarray(levels, dtype=np.intp)
    if idx.size and (idx.min() < 0 or idx.max() > MAX_LEVEL):
        raise ValueError(f"levels must be between 0 and {MAX_LEVEL}")
    return table[idx]


def level_to_dbz_array(
    levels: np.ndarray, a: float = DEFAULT_ZR_A, b: float = DEFAULT_ZR_B
) -> np.ndarray:
    """Vectorised :func:`level_to_dbz` over an array of levels."""
    table = np.array(
        [level_to_dbz(level, a=a, b=b) for level in range(MAX_LEVEL + 1)],
        dtype=np.float32,
    )
    idx = np.asarray(levels, dtype=np.intp)
    if idx.size and (idx.min() < 0 or idx.max() > MAX_LEVEL):
        raise ValueError(f"levels must be between 0 and {MAX_LEVEL}")
    return table[idx]
