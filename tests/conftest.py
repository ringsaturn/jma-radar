"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Directory containing the recorded JMA samples."""
    return FIXTURES


def tile_bytes(name: str) -> bytes:
    """Read one recorded tile PNG."""
    return (FIXTURES / "hrpns" / name).read_bytes()
