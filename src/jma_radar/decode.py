"""Decode JMA nowcast PNG tiles into precipitation level arrays."""

from __future__ import annotations

import io
import logging
from typing import Final

import numpy as np
from PIL import Image

from .constants import LEVEL_COLORS, MAX_LEVEL, TILE_SIZE

__all__ = ["PaletteError", "decode_tile", "empty_tile", "is_empty_tile"]

logger = logging.getLogger(__name__)


class PaletteError(ValueError):
    """Raised when a tile uses a palette that is not the known JMA palette."""


#: Colour -> level lookup for the unambiguous classes (level >= 2).
_COLOR_TO_LEVEL: Final[dict[tuple[int, int, int], int]] = {
    color: level for level, color in enumerate(LEVEL_COLORS) if level >= 2
}

#: Levels 0 and 1 share this colour; they are distinguished by palette index.
_TRANSPARENT_COLOR: Final[tuple[int, int, int]] = LEVEL_COLORS[0]


def empty_tile() -> np.ndarray:
    """Return a 256x256 all-"no data" level array."""
    return np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint8)


def is_empty_tile(data: bytes) -> bool:
    """Return ``True`` if the PNG payload is a fully transparent RGBA tile.

    JMA serves such tiles (HTTP 200, ~334 bytes) for odd zooms and for tiles
    outside the analysis domain.
    """
    with Image.open(io.BytesIO(data)) as image:
        if image.mode != "RGBA":
            return False
        alpha = np.asarray(image.convert("RGBA"))[:, :, 3]
    return bool((alpha == 0).all())


def _palette_index_to_level(image: Image.Image) -> np.ndarray:
    """Build a palette-index -> level lookup table for a palette mode image.

    The mapping is derived from the palette *colours*, so a reordered palette
    still decodes correctly. Indices 0 and 1 carry the same (transparent white)
    colour and are therefore resolved positionally: 0 = no data, 1 = 0 mm/h.

    Raises:
        PaletteError: if the palette contains an unknown colour.
    """
    palette = image.getpalette()
    if palette is None:  # pragma: no cover - defensive, mode "P" always has one
        raise PaletteError("palette mode image without a palette")
    n_entries = len(palette) // 3
    lut = np.zeros(256, dtype=np.uint8)
    used = set(np.unique(np.asarray(image)).tolist())
    for index in range(n_entries):
        color = (palette[index * 3], palette[index * 3 + 1], palette[index * 3 + 2])
        if index < 2 and color == _TRANSPARENT_COLOR:
            lut[index] = index
            continue
        level = _COLOR_TO_LEVEL.get(color)
        if level is None:
            if index not in used:
                # Unused padding entry (e.g. zero filled tail); ignore it.
                logger.debug("ignoring unused palette entry %d with colour %s", index, color)
                continue
            raise PaletteError(
                f"unknown JMA palette colour {color} at index {index}; "
                "the tile palette may have changed"
            )
        lut[index] = level
    unknown = {value for value in used if value >= n_entries}
    if unknown:
        raise PaletteError(f"tile uses palette indices outside the palette: {sorted(unknown)}")
    return lut


def decode_tile(data: bytes) -> np.ndarray:
    """Decode a tile PNG into a ``(256, 256)`` ``uint8`` array of levels.

    Level 0 means "no data" (outside the domain or not observed) and level 1
    means "0 mm/h"; levels 2..9 are the JMA precipitation intensity classes.

    Raises:
        PaletteError: if the tile palette is not the known JMA palette.
        ValueError: if the payload is not a 256x256 image.
    """
    with Image.open(io.BytesIO(data)) as image:
        if image.size != (TILE_SIZE, TILE_SIZE):
            raise ValueError(f"expected a {TILE_SIZE}x{TILE_SIZE} tile, got {image.size}")
        if image.mode == "P":
            lut = _palette_index_to_level(image)
            indices = np.asarray(image, dtype=np.uint8)
            return lut[indices]
        rgba = np.asarray(image.convert("RGBA"))

    alpha = rgba[:, :, 3]
    if bool((alpha == 0).all()):
        return empty_tile()
    return _decode_rgba(rgba)


def _decode_rgba(rgba: np.ndarray) -> np.ndarray:
    """Decode a true-colour tile by matching every distinct colour."""
    levels = np.zeros(rgba.shape[:2], dtype=np.uint8)
    opaque = rgba[:, :, 3] > 0
    colors = rgba[:, :, :3]
    flat = colors.reshape(-1, 3)
    unique_colors = np.unique(flat[opaque.reshape(-1)], axis=0)
    for color in unique_colors:
        key = (int(color[0]), int(color[1]), int(color[2]))
        level = _COLOR_TO_LEVEL.get(key)
        if level is None:
            raise PaletteError(
                f"unknown JMA colour {key} in RGBA tile; the palette may have changed"
            )
        match = opaque & (colors == color).all(axis=-1)
        levels[match] = level
    if levels.max() > MAX_LEVEL:  # pragma: no cover - defensive
        raise PaletteError("decoded level out of range")
    return levels
