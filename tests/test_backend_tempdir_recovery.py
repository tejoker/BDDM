"""Regression tests for backend-tempdir lifecycle recovery in prove_arxiv_batch.

When lean_dojo's `LeanGitRepo.__post_init__` / `get_traced_repo_path` creates
a temporary directory via `tempfile.TemporaryDirectory()`, the dir is deleted
on context-exit but cached Repo/TracedRepo references can outlive the
cleanup.  Subsequent operations that touch the cached handle then raise
`FileNotFoundError: [Errno 2] No such file or directory: '/tmp/tmpXXXXXXXX'`.

Without recovery, our top-level `except Exception` handler in `prove_one`
recorded the raw FNFE as the row's `error_message`, poisoning every
subsequent theorem in the same paper run with the dead /tmp/tmpXXX path.
The ledger contained 32 UNRESOLVED rows fingerprinted with two specific
paths (`/tmp/tmpxyqetja3` and `/tmp/tmp75hm2zpk`) before this fix.

The fix:
  1. Detect FNFEs whose path matches the default `tempfile.NamedTemporaryFile`
     pattern (`/tmp/tmpXXXXXXXX`).
  2. Clear lean_dojo's `functools.cache` memos plus our `_SNAPSHOT_CACHE` so
     the next backend open re-materialises a fresh tempdir.
  3. Rewrite the error message to a stable classifier so the ledger does
     not carry a dead per-run path across theorems.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from prove_arxiv_batch import (  # noqa: E402
    _DEFAULT_TMPFILE_RE,
    _classify_backend_tempdir_failure,
    _is_backend_tempdir_failure,
    _reset_backend_caches,
)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

def test_detector_matches_default_tempfile_pattern() -> None:
    """The canonical Python tempfile name format must match — that's the
    fingerprint that appeared 32 times in the ledger."""
    err = "[Errno 2] No such file or directory: '/tmp/tmpxyqetja3'"
    assert _is_backend_tempdir_failure(err)
    err2 = "[Errno 2] No such file or directory: '/tmp/tmp75hm2zpk'"
    assert _is_backend_tempdir_failure(err2)


def test_detector_matches_modern_default_tempfile_pattern() -> None:
    """Python 3.12 tempfile names are 8 chars from
    `tempfile._RandomNameSequence.characters` (lowercase + digits + underscore).
    Cover that exact shape so the detector matches whatever the interpreter
    produces."""
    for name in ("tmp12345678", "tmpabcdefgh", "tmp_abc_de", "tmpxyqetja3"):
        err = f"[Errno 2] No such file or directory: '/tmp/{name}'"
        assert _is_backend_tempdir_failure(err), f"missed: {name}"


def test_detector_rejects_named_tempdirs() -> None:
    """Our snapshot tempdirs use explicit prefixes — they must NOT trigger
    the detector (we want to know if THOSE went missing, that's a different
    contract violation)."""
    not_default = "[Errno 2] No such file or directory: '/tmp/desol-dojo-snapshot-abc/repo'"
    assert not _is_backend_tempdir_failure(not_default)
    not_default2 = "[Errno 2] No such file or directory: '/tmp/desol_worker0_xyz'"
    assert not _is_backend_tempdir_failure(not_default2)
    not_default3 = "[Errno 2] No such file or directory: '/tmp/desol_iv_aaa'"
    assert not _is_backend_tempdir_failure(not_default3)


def test_detector_rejects_non_tmp_paths() -> None:
    """Paths outside /tmp (e.g. workspace files the translator wrote) must
    not be reclassified — those are real translation/translation-fidelity
    failures that should propagate truthfully."""
    err = "[Errno 2] No such file or directory: '/home/projectx/BDDM/output/2604.21663.lean'"
    assert not _is_backend_tempdir_failure(err)


def test_detector_rejects_unrelated_errors() -> None:
    """Tactic / proof-search errors are unrelated and must not be coerced
    into the backend-tempdir classifier."""
    assert not _is_backend_tempdir_failure("simp made no progress")
    assert not _is_backend_tempdir_failure("unknown identifier 'foo'")
    assert not _is_backend_tempdir_failure("")
    assert not _is_backend_tempdir_failure("REPL did not respond within 30s")


def test_detector_rejects_fnfe_with_named_prefix_in_tmp() -> None:
    """An FNFE on `/tmp/something_else` is not a default-tempfile failure;
    we only want to clear caches when lean_dojo's auto-generated tempdir is
    the culprit."""
    err = "[Errno 2] No such file or directory: '/tmp/leandojo-cache-aaa'"
    assert not _is_backend_tempdir_failure(err)
    # Mixed-case in the suffix is not what `tempfile._RandomNameSequence`
    # emits today — its alphabet is lowercase + digits + underscore.
    # If a future CPython broadens the alphabet, this test will fail
    # noisily and prompt a deliberate regex update rather than letting an
    # arbitrary `/tmp/tmpFOO` slip through silently.
    err = "[Errno 2] No such file or directory: '/tmp/tmpABCDEFGH'"
    assert not _is_backend_tempdir_failure(err)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def test_classifier_emits_stable_string() -> None:
    """The classifier output must NOT include the per-run /tmp path so the
    ledger doesn't carry a dead fingerprint across theorems."""
    out = _classify_backend_tempdir_failure(
        "[Errno 2] No such file or directory: '/tmp/tmpxyqetja3'"
    )
    assert "/tmp/tmp" not in out, "classifier must strip the dead tmppath"
    assert "backend_tempdir_unavailable" in out


# ---------------------------------------------------------------------------
# Cache reset
# ---------------------------------------------------------------------------

def test_reset_backend_caches_is_noop_safe() -> None:
    """`_reset_backend_caches` must succeed even when lean_dojo is not
    importable or its caches aren't populated."""
    # Just calling it should never raise.
    _reset_backend_caches()
    _reset_backend_caches()  # idempotent


def test_reset_backend_caches_clears_snapshot_cache() -> None:
    """When prove_with_ponder._SNAPSHOT_CACHE has entries, reset must drop
    them so the next backend-open re-materialises the snapshot."""
    try:
        from prove_with_ponder import _SNAPSHOT_CACHE
    except Exception:
        import pytest
        pytest.skip("prove_with_ponder not importable in this environment")
    _SNAPSHOT_CACHE["sentinel"] = (Path("/tmp/x"), Path("/tmp/x/repo"))
    assert "sentinel" in _SNAPSHOT_CACHE
    _reset_backend_caches()
    assert "sentinel" not in _SNAPSHOT_CACHE


# ---------------------------------------------------------------------------
# Regex sanity
# ---------------------------------------------------------------------------

def test_tmpfile_regex_anchors() -> None:
    """The regex must require the `tmp` prefix and a sane character set, so
    arbitrary `/tmp/foo` paths don't accidentally satisfy the detector."""
    # 8-char default tempfile name with terminating quote (the shape we see
    # in real FileNotFoundError repr strings).
    assert _DEFAULT_TMPFILE_RE.search("/tmp/tmpabcdef12'")
    # No `tmp` prefix between the slashes — not a default tempfile name.
    assert not _DEFAULT_TMPFILE_RE.search("/tmp/abcdef12'")
    # Too short to be a default tempfile name.
    assert not _DEFAULT_TMPFILE_RE.search("/tmp/tmpAB'")


# ---------------------------------------------------------------------------
# prove_with_ponder side: stale lean_dojo cache recovery
# ---------------------------------------------------------------------------

def test_prove_with_ponder_stale_cache_detector_matches_disk_cache_fnfe() -> None:
    """`_is_stale_leandojo_cache_error` must recognise the disk-cache flavour
    of the bug — a FileNotFoundError naming a `~/.cache/lean_dojo/repos/...`
    path. Without recovery, this leaves every subsequent test/process
    failing on the same partially-populated cache entry."""
    try:
        from prove_with_ponder import _is_stale_leandojo_cache_error
    except Exception:
        import pytest
        pytest.skip("prove_with_ponder not importable in this environment")
    msg = (
        "[Errno 2] No such file or directory: "
        "'/home/projectx/.cache/lean_dojo/repos/gitpython-repo-abcdef/repo/lean-toolchain'"
    )
    assert _is_stale_leandojo_cache_error(FileNotFoundError(msg))


def test_prove_with_ponder_stale_cache_detector_matches_default_tmpfile_fnfe() -> None:
    """Same module must also catch the `/tmp/tmpXXXX` flavour (the original
    pattern that produced 32 spurious ledger rows)."""
    try:
        from prove_with_ponder import _is_stale_leandojo_cache_error
    except Exception:
        import pytest
        pytest.skip("prove_with_ponder not importable in this environment")
    assert _is_stale_leandojo_cache_error(
        FileNotFoundError("[Errno 2] No such file or directory: '/tmp/tmpxyqetja3'")
    )


def test_prove_with_ponder_stale_cache_detector_rejects_unrelated_fnfe() -> None:
    """An FNFE on a workspace file (not a backend-internal path) must not
    trigger cache-clearing — those are real translation/translation-fidelity
    errors that should propagate truthfully."""
    try:
        from prove_with_ponder import _is_stale_leandojo_cache_error
    except Exception:
        import pytest
        pytest.skip("prove_with_ponder not importable in this environment")
    assert not _is_stale_leandojo_cache_error(
        FileNotFoundError("[Errno 2] No such file or directory: '/home/x/repo/Main.lean'")
    )
    assert not _is_stale_leandojo_cache_error(RuntimeError("unrelated"))


def test_prove_with_ponder_purge_stale_disk_cache_entry(tmp_path) -> None:
    """`_purge_stale_leandojo_disk_cache_entry` must remove the partially-
    populated `<cache_dir>/repos/<entry>/` directory named by the
    FileNotFoundError, leaving the rest of the cache intact so the next
    backend open re-traces cleanly without nuking unrelated entries."""
    try:
        from prove_with_ponder import _purge_stale_leandojo_disk_cache_entry
    except Exception:
        import pytest
        pytest.skip("prove_with_ponder not importable in this environment")
    fake_home = tmp_path / "home"
    cache_root = fake_home / ".cache" / "lean_dojo" / "repos"
    cache_root.mkdir(parents=True, exist_ok=True)
    bad_entry = cache_root / "gitpython-repo-abcdef"
    bad_entry.mkdir()
    (bad_entry / "repo").mkdir()
    # NOTE: `lean-toolchain` is intentionally MISSING — that's exactly the
    # state we want to recover from.
    other_entry = cache_root / "github-lean4-deadbeef"
    other_entry.mkdir()
    (other_entry / "preserved.txt").write_text("keep me", encoding="utf-8")
    err = FileNotFoundError(
        f"[Errno 2] No such file or directory: '{bad_entry}/repo/lean-toolchain'"
    )
    _purge_stale_leandojo_disk_cache_entry(err)
    assert not bad_entry.exists(), "stale cache entry must be purged"
    assert other_entry.exists(), "unrelated cache entries must be preserved"
    assert (other_entry / "preserved.txt").exists()


def test_prove_with_ponder_purge_is_noop_for_unrelated_errors(tmp_path) -> None:
    """The purger must be a no-op when the error message doesn't name a
    lean_dojo cache path — we never want to remove arbitrary disk content."""
    try:
        from prove_with_ponder import _purge_stale_leandojo_disk_cache_entry
    except Exception:
        import pytest
        pytest.skip("prove_with_ponder not importable in this environment")
    untouchable = tmp_path / "important.txt"
    untouchable.write_text("preserve", encoding="utf-8")
    _purge_stale_leandojo_disk_cache_entry(FileNotFoundError("nothing of interest"))
    _purge_stale_leandojo_disk_cache_entry(RuntimeError(str(untouchable)))
    assert untouchable.exists()


def test_clear_leandojo_functools_caches_is_noop_safe() -> None:
    """`_clear_leandojo_functools_caches` must succeed even when lean_dojo
    is not importable; it is a defensive cleanup, never a hard requirement."""
    try:
        from prove_with_ponder import _clear_leandojo_functools_caches
    except Exception:
        import pytest
        pytest.skip("prove_with_ponder not importable in this environment")
    _clear_leandojo_functools_caches()
    _clear_leandojo_functools_caches()
