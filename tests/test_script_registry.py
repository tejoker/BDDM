from __future__ import annotations

from pathlib import Path

from script_registry import (
    OFFICIAL_PIPELINE_SCRIPTS,
    SCRIPT_REGISTRY,
    VALID_CATEGORIES,
    VALID_TIERS,
    unregistered_scripts,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_script_registry_covers_every_top_level_script() -> None:
    assert unregistered_scripts(PROJECT_ROOT / "scripts") == []


def test_script_registry_rows_are_valid() -> None:
    for name, row in SCRIPT_REGISTRY.items():
        assert name.endswith(".py")
        assert row["tier"] in VALID_TIERS
        assert row["category"] in VALID_CATEGORIES
        assert row["summary"].strip()


def test_official_pipeline_surface_is_intentionally_small() -> None:
    official = {
        name
        for name, row in SCRIPT_REGISTRY.items()
        if row["tier"] == "official_pipeline"
    }

    assert official == OFFICIAL_PIPELINE_SCRIPTS
