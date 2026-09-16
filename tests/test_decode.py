"""Tests for tile decoding, driven by recorded JMA tiles."""

from __future__ import annotations

import io
from collections import Counter
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from jma_radar.constants import LEVEL_COLORS
from jma_radar.decode import PaletteError, decode_tile, empty_tile, is_empty_tile

Z6_HISTOGRAM = {
    0: 13195,
    1: 16449,
    2: 7812,
    3: 16467,
    4: 5333,
    5: 4668,
    6: 1014,
    7: 420,
    8: 152,
    9: 26,
}


def _histogram(levels: np.ndarray) -> dict[int, int]:
    return dict(sorted(Counter(levels.ravel().tolist()).items()))


def test_decode_z6_histogram(fixtures_dir: Path) -> None:
    data = (fixtures_dir / "hrpns" / "20260916010500_z6_x56_y25.png").read_bytes()
    levels = decode_tile(data)
    assert levels.shape == (256, 256)
    assert levels.dtype == np.uint8
    assert _histogram(levels) == Z6_HISTOGRAM


def test_decode_empty_rgba_tile(fixtures_dir: Path) -> None:
    data = (fixtures_dir / "hrpns" / "20260916010500_z4_x14_y7_empty_rgba.png").read_bytes()
    assert is_empty_tile(data)
    levels = decode_tile(data)
    assert (levels == 0).all()
    assert np.array_equal(levels, empty_tile())


def test_mostly_empty_palette_tile(fixtures_dir: Path) -> None:
    """The sparse z4 tile is dominated by no-data, with only a few rain pixels."""
    data = (fixtures_dir / "hrpns" / "20260916010500_z4_x14_y5_allzero.png").read_bytes()
    levels = decode_tile(data)
    assert not is_empty_tile(data)
    assert int((levels == 0).sum()) > 0.99 * levels.size
    assert levels.max() <= 5


@pytest.mark.parametrize(
    "name",
    [
        "20260916010500_z4_x13_y6.png",
        "20260916010500_z4_x14_y6.png",
        "20260916010500_z6_x56_y25.png",
        "20260916010500_z8_x226_y101.png",
        "20260916010500_z10_x906_y404.png",
    ],
)
def test_all_fixtures_decode_in_range(fixtures_dir: Path, name: str) -> None:
    levels = decode_tile((fixtures_dir / "hrpns" / name).read_bytes())
    assert levels.shape == (256, 256)
    assert levels.min() >= 0
    assert levels.max() <= 9


def test_palette_order_independent(fixtures_dir: Path) -> None:
    """Shuffling palette entries 2..9 must not change the decoded levels."""
    path = fixtures_dir / "hrpns" / "20260916010500_z6_x56_y25.png"
    with Image.open(path) as image:
        indices = np.asarray(image, dtype=np.uint8)
    expected = decode_tile(path.read_bytes())

    # Reverse palette entries 2..9 and remap the indices accordingly.
    order = [0, 1, *range(9, 1, -1)]
    palette: list[int] = []
    for level in order:
        palette.extend(LEVEL_COLORS[level])
    remap = np.zeros(256, dtype=np.uint8)
    for new_index, level in enumerate(order):
        remap[level] = new_index

    shuffled = Image.fromarray(remap[indices], mode="P")
    shuffled.putpalette(palette)
    buffer = io.BytesIO()
    shuffled.save(buffer, format="PNG")
    assert np.array_equal(decode_tile(buffer.getvalue()), expected)


def test_unknown_palette_colour_raises() -> None:
    indices = np.zeros((256, 256), dtype=np.uint8)
    indices[0, 0] = 2
    image = Image.fromarray(indices, mode="P")
    image.putpalette([255, 255, 255, 255, 255, 255, 1, 2, 3])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    with pytest.raises(PaletteError, match="unknown JMA palette colour"):
        decode_tile(buffer.getvalue())


def test_unknown_rgba_colour_raises() -> None:
    rgba = np.zeros((256, 256, 4), dtype=np.uint8)
    rgba[0, 0] = (1, 2, 3, 255)
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG")
    with pytest.raises(PaletteError, match="unknown JMA colour"):
        decode_tile(buffer.getvalue())


def test_rgba_tile_with_known_colours() -> None:
    rgba = np.zeros((256, 256, 4), dtype=np.uint8)
    rgba[10, 10] = (*LEVEL_COLORS[4], 255)
    rgba[20, 20] = (*LEVEL_COLORS[9], 255)
    buffer = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buffer, format="PNG")
    levels = decode_tile(buffer.getvalue())
    assert levels[10, 10] == 4
    assert levels[20, 20] == 9
    assert levels[0, 0] == 0


def test_wrong_size_raises() -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (128, 128)).save(buffer, format="PNG")
    with pytest.raises(ValueError, match="256x256"):
        decode_tile(buffer.getvalue())


def test_is_empty_tile_false_for_palette(fixtures_dir: Path) -> None:
    data = (fixtures_dir / "hrpns" / "20260916010500_z6_x56_y25.png").read_bytes()
    assert not is_empty_tile(data)
