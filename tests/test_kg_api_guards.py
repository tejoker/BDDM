from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def _reload_kg_api(**env_overrides: str):
    for key, value in env_overrides.items():
        os.environ[key] = value
    if "kg_api" in sys.modules:
        del sys.modules["kg_api"]
    return importlib.import_module("kg_api")


def test_rate_limit_allows_first_hit() -> None:
    kg_api = _reload_kg_api(DESOL_RATE_LIMIT_PER_MIN="1")
    ok, retry = kg_api._check_rate_limit("test-client")
    assert ok is True
    assert retry == 0


def test_rate_limit_blocks_second_hit_within_window() -> None:
    kg_api = _reload_kg_api(DESOL_RATE_LIMIT_PER_MIN="1")
    ok1, _ = kg_api._check_rate_limit("client2")
    ok2, retry = kg_api._check_rate_limit("client2")
    assert ok1 is True
    assert ok2 is False
    assert retry >= 1


def test_rate_limit_recovers_after_window_prune() -> None:
    kg_api = _reload_kg_api(DESOL_RATE_LIMIT_PER_MIN="1")
    key = "client3"
    ok, _ = kg_api._check_rate_limit(key)
    assert ok is True
    # Force a stale timestamp to simulate old window entries.
    with kg_api._rate_lock:
        kg_api._rate_windows[key] = [time.time() - 120]
    ok2, retry2 = kg_api._check_rate_limit(key)
    assert ok2 is True
    assert retry2 == 0

