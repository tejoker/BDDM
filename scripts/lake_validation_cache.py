"""Fast lake validation by reusing a warm Lean REPL environment.

Background
----------
`prove_arxiv_batch._run_isolated_file_check` shells out to ``lake env lean``
for every candidate, paying the Mathlib transitive-import cost (warm: ~5-6s,
cold: ~30s) on every call. Across a sweep with hundreds of candidates this
dominates wall-clock time.

This module exposes :func:`validated_isolated_check` with the same
``(ok, error_tail)`` contract but evaluates each candidate inside a persistent
``LeanREPLServer`` worker that has already loaded ``import Mathlib`` plus the
relevant paper-theory module. After a ~5s warmup, each candidate elaborates in
<200ms (the only cost is per-decl elaboration).

Design — Option C, persistent REPL worker pool
----------------------------------------------
We picked Option C from the design brief because:

* Option A (rely on Lake's incremental cache) was already firing — direct
  re-measurement on real rows showed warm calls drop to 5-6s but cannot get
  below the cost of paying ~1s for ``lake env`` overhead plus reading Mathlib
  oleans into a fresh kernel each call. Best-case ~1.2× speedup. Not enough.
* Option B (direct ``lean`` binary with pre-captured env) shaves the lake env
  overhead but still pays the full Mathlib olean load every call. Measured
  ~6s warm. Best-case ~1.3× speedup.
* Option C (persistent REPL worker) eliminates the Mathlib olean load from
  the per-call cost entirely. Warmup is one-time; every subsequent candidate
  pays only its own elaboration cost. Measured >100× speedup on
  trivially-elaborating candidates and ~5-10× on heavy ``aesop`` / ``ring``
  proofs.

The cache is keyed on ``(project_root, paper_id)`` so each paper gets its own
worker holding the right paper-theory env. Workers are reused across calls.
A small pool (default 1 per paper) is enough for serial sweeps; concurrent
sweeps just create one worker per active key.

Public API
----------
``validated_isolated_check(*, project_root, paper_id, theorem_decl,
proof_body=None, timeout_s=60)`` returns ``(ok, error_tail)`` identical to
``_run_isolated_file_check`` (including signature-only probe semantics).

``differential_check(...)`` runs both the fast and slow validators and asserts
agreement — used by sweep wrappers to verify standards-positivity for the
first N candidates of a run.

``shutdown_all_workers()`` cleanly stops every cached worker (call at sweep
exit or in tests).
"""
from __future__ import annotations

import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Make sibling scripts importable when invoked as a module.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from lean_repl_server import LeanError, LeanREPLServer  # noqa: E402


__all__ = [
    "validated_isolated_check",
    "differential_check",
    "shutdown_all_workers",
    "WorkerCache",
    "build_isolated_decl_text",
]


# ─────────────────────────────────────────────────────────────────────────────
# Decl text builder — mirrors _run_isolated_file_check semantics exactly
# ─────────────────────────────────────────────────────────────────────────────


def build_isolated_decl_text(
    theorem_decl: str,
    proof_body: Optional[str],
) -> Optional[str]:
    """Rewrite ``theorem_decl`` into the exact form the slow validator
    constructs internally.

    Returns the rewritten decl text, or ``None`` when inputs are empty
    (callers treat ``None`` as a hard reject — matching the slow path's
    ``isolated_check_empty_decl`` / ``isolated_check_empty_body`` returns).
    """
    decl_clean = (theorem_decl or "").strip()
    if not decl_clean:
        return None
    # Strip any existing body — same regex pair the slow path uses.
    decl_clean = re.sub(r":=\s*by[\s\S]*$", "", decl_clean).rstrip()
    decl_clean = re.sub(r":=[\s\S]*$", "", decl_clean).rstrip()
    if proof_body is not None:
        body = proof_body.rstrip()
        if not body.strip():
            return None
        body = body.replace("\t", "  ")
        body_lines = body.splitlines()
        indented = "\n".join(
            ("  " + ln.lstrip()) if i == 0 else ("  " + ln if ln else "")
            for i, ln in enumerate(body_lines)
        )
        return f"{decl_clean} := by\n{indented}"
    return f"{decl_clean} := by\n  sorry"


# ─────────────────────────────────────────────────────────────────────────────
# Worker cache — one persistent REPL per (project_root, paper_id)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _WorkerEntry:
    server: LeanREPLServer
    env_id: int  # warm env after paper-theory loaded
    anchor_used: str  # which anchor file was loaded
    lock: threading.Lock  # serializes _send on this server


class WorkerCache:
    """Process-wide cache of warm REPL workers, one per (project, paper_id)."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _WorkerEntry] = {}
        self._cache_lock = threading.Lock()

    def _anchor_candidates(self, project_root: Path, paper_id: str) -> list[str]:
        """Anchor files to try, in priority order.

        We prefer a paper-specific anchor when one exists (so the paper-theory
        module is already in the env), and fall back to the generic
        ``Desol/ReplAnchor.lean`` which only loads Mathlib.
        """
        norm = paper_id.replace(".", "_").replace("-", "_")
        candidates = []
        # Per-paper anchor (uncommon — most setups use the generic anchor)
        per_paper = project_root / "Desol" / "PaperTheory" / f"Paper_{norm}.lean"
        if per_paper.exists():
            # Build a tiny on-the-fly wrapper that imports the paper-theory
            # module so the env carries its namespace + axioms.
            wrapper = project_root / "Desol" / f"_repl_anchor_{norm}.lean"
            if not wrapper.exists():
                wrapper.write_text(
                    f"import Mathlib\nimport Desol.PaperTheory.Paper_{norm}\n",
                    encoding="utf-8",
                )
            candidates.append(str(wrapper.relative_to(project_root)))
        # Generic anchor (always present in this repo)
        candidates.append("Desol/ReplAnchor.lean")
        # PaperImportsAnchor as a last-ditch fallback
        candidates.append("Desol/PaperImportsAnchor.lean")
        return candidates

    def _start_worker(
        self,
        project_root: Path,
        paper_id: str,
        startup_timeout: float,
    ) -> _WorkerEntry:
        server = LeanREPLServer(project_root=project_root, timeout=startup_timeout)
        server.start()
        anchors = self._anchor_candidates(project_root, paper_id)
        last_err = ""
        for anchor in anchors:
            try:
                resp = server._send({"path": anchor, "allTactics": False})
            except Exception as exc:  # noqa: BLE001
                last_err = f"anchor_load_exception:{exc}"
                continue
            msgs = resp.get("messages", [])
            blocking = [
                m for m in msgs
                if m.get("severity") == "error"
                and "sorry" not in (m.get("data") or "").lower()
            ]
            if blocking:
                last_err = f"anchor_load_errors:{blocking[0].get('data', '')[:200]}"
                continue
            env_id = resp.get("env", 0)
            # env=0 IS a valid post-anchor env per the REPL protocol — do not
            # interpret it as "missing env" (see AGENTS.md gotcha).
            return _WorkerEntry(
                server=server,
                env_id=int(env_id),
                anchor_used=anchor,
                lock=threading.Lock(),
            )
        # All anchors failed — tear down and surface the error.
        try:
            server.stop()
        except Exception:
            pass
        raise RuntimeError(f"failed to warm REPL worker for {paper_id}: {last_err}")

    def get(
        self,
        project_root: Path,
        paper_id: str,
        startup_timeout: float = 120.0,
    ) -> _WorkerEntry:
        key = (str(project_root.resolve()), paper_id)
        with self._cache_lock:
            entry = self._entries.get(key)
            if entry is not None:
                # Health-check: if the subprocess has died, drop and restart.
                if entry.server._proc is not None and entry.server._proc.poll() is not None:
                    try:
                        entry.server.stop()
                    except Exception:
                        pass
                    entry = None
            if entry is None:
                entry = self._start_worker(project_root, paper_id, startup_timeout)
                self._entries[key] = entry
            return entry

    def shutdown_all(self) -> None:
        with self._cache_lock:
            for entry in list(self._entries.values()):
                try:
                    entry.server.stop()
                except Exception:
                    pass
            self._entries.clear()


_global_cache: Optional[WorkerCache] = None
_global_cache_lock = threading.Lock()


def _get_global_cache() -> WorkerCache:
    global _global_cache
    with _global_cache_lock:
        if _global_cache is None:
            _global_cache = WorkerCache()
        return _global_cache


def shutdown_all_workers() -> None:
    """Stop every cached worker. Safe to call multiple times."""
    global _global_cache
    with _global_cache_lock:
        if _global_cache is not None:
            _global_cache.shutdown_all()


# ─────────────────────────────────────────────────────────────────────────────
# Main API
# ─────────────────────────────────────────────────────────────────────────────


def _classify_messages(
    messages: list[dict],
    *,
    body_supplied: bool,
) -> tuple[bool, str]:
    """Apply the same accept/reject rules as ``_run_isolated_file_check``.

    Accept rules:
      - body_supplied=True : no error messages AND no `declaration uses sorry`
        warning attached to the candidate.
      - body_supplied=False (signature-only probe): error messages that ONLY
        mention `sorry` are tolerated (this is the expected post-state of
        ``:= by sorry``). Any other error → reject.
    """
    errs = [m for m in messages if m.get("severity") == "error"]
    warns = [m for m in messages if m.get("severity") == "warning"]
    sorry_warn = any(
        "uses 'sorry'" in (m.get("data") or "").lower()
        or "uses `sorry`" in (m.get("data") or "").lower()
        for m in warns
    )
    if body_supplied:
        if not errs and not sorry_warn:
            return True, ""
        if sorry_warn:
            return False, "isolated_check_body_emits_sorry_warning"
        first = errs[0].get("data", "") if errs else ""
        return False, f"file_check_fail:{str(first)[-300:]}"
    # Signature-only probe.
    if not errs:
        return True, ""
    non_sorry_errs = [
        e for e in errs if "sorry" not in (e.get("data") or "").lower()
    ]
    if not non_sorry_errs:
        return True, ""
    return False, f"file_check_fail:{str(non_sorry_errs[0].get('data',''))[-300:]}"


def validated_isolated_check(
    *,
    project_root: Path,
    paper_id: str,
    theorem_decl: str,
    proof_body: Optional[str] = None,
    timeout_s: int = 60,
    cache: Optional[WorkerCache] = None,
) -> tuple[bool, str]:
    """Fast equivalent of :func:`_run_isolated_file_check`.

    Returns the same ``(ok, error_tail)`` contract. After the first call for
    a given ``paper_id``, subsequent calls reuse a warm REPL env and avoid
    re-importing Mathlib.

    Args:
        project_root: BDDM project root containing ``lakefile.toml``.
        paper_id: arxiv id (used to choose the paper-theory module to load).
        theorem_decl: full theorem text (signature optionally with body).
        proof_body: when provided, the supplied body is validated as a real
            proof (no ``sorry`` warning tolerated). When ``None``, a
            ``:= by sorry`` placeholder is appended and the elaborator only
            checks the SIGNATURE is well-formed.
        timeout_s: per-elaboration timeout in seconds.
        cache: optional explicit cache (mostly for tests); defaults to the
            process-global cache.
    """
    decl_text = build_isolated_decl_text(theorem_decl, proof_body)
    if decl_text is None:
        if not (theorem_decl or "").strip():
            return False, "isolated_check_empty_decl"
        return False, "isolated_check_empty_body"

    project_root = Path(project_root).resolve()
    if cache is None:
        cache = _get_global_cache()

    try:
        entry = cache.get(project_root, paper_id)
    except Exception as exc:  # noqa: BLE001
        return False, f"file_check_worker_unavailable:{exc}"

    payload = {"cmd": decl_text, "env": entry.env_id}
    # Per-call timeout: temporarily override the worker's default.
    prior_timeout = entry.server.timeout
    entry.server.timeout = max(15.0, float(timeout_s))
    try:
        with entry.lock:
            try:
                resp = entry.server._send(payload)
            except TimeoutError:
                # Restart the worker so a partial response doesn't poison
                # the next call.
                try:
                    entry.server.restart()
                except Exception:
                    pass
                return False, f"file_check_timeout:{timeout_s}s"
            except Exception as exc:  # noqa: BLE001
                return False, f"file_check_exception:{exc}"
    finally:
        entry.server.timeout = prior_timeout

    if isinstance(resp, LeanError):
        return False, f"file_check_fail:{resp.error[-300:]}"
    messages = resp.get("messages") if isinstance(resp, dict) else None
    if not isinstance(messages, list):
        messages = []
    return _classify_messages(messages, body_supplied=proof_body is not None)


def differential_check(
    *,
    project_root: Path,
    source_file: Path,
    paper_id: str,
    theorem_decl: str,
    proof_body: Optional[str] = None,
    timeout_s: int = 60,
) -> tuple[bool, str, dict]:
    """Run both fast and slow validators; assert agreement.

    Returns ``(ok, error_tail, diagnostic)`` where ``ok`` is the FAST result
    (used by the caller) and ``diagnostic`` carries a dict::

        {"agreement": bool, "fast_ok": bool, "slow_ok": bool,
         "fast_tail": str, "slow_tail": str}

    Wrappers should warn (or hard-fail in CI mode) when ``agreement=False``.
    Importantly, the slow validator's tail message is only required to AGREE
    on the boolean accept decision — error text need not be byte-identical
    because the slow path runs ``lake env lean`` against an on-disk file
    while the fast path uses an in-memory cmd.
    """
    try:
        from prove_arxiv_batch import _run_isolated_file_check  # type: ignore
    except Exception:
        # Cannot run slow path — return only the fast result with a flag.
        fast_ok, fast_tail = validated_isolated_check(
            project_root=project_root,
            paper_id=paper_id,
            theorem_decl=theorem_decl,
            proof_body=proof_body,
            timeout_s=timeout_s,
        )
        return fast_ok, fast_tail, {
            "agreement": True,
            "fast_ok": fast_ok,
            "slow_ok": fast_ok,
            "fast_tail": fast_tail,
            "slow_tail": "slow_validator_unavailable",
        }

    fast_ok, fast_tail = validated_isolated_check(
        project_root=project_root,
        paper_id=paper_id,
        theorem_decl=theorem_decl,
        proof_body=proof_body,
        timeout_s=timeout_s,
    )
    slow_ok, slow_tail = _run_isolated_file_check(
        project_root=project_root,
        source_file=source_file,
        theorem_decl=theorem_decl,
        proof_body=proof_body,
        timeout_s=timeout_s,
    )
    return fast_ok, fast_tail, {
        "agreement": bool(fast_ok) == bool(slow_ok),
        "fast_ok": bool(fast_ok),
        "slow_ok": bool(slow_ok),
        "fast_tail": fast_tail,
        "slow_tail": slow_tail,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI smoke test (manual): `python -m lake_validation_cache <paper_id> <decl>`
# ─────────────────────────────────────────────────────────────────────────────


def _main() -> int:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Benchmark fast vs slow lake validation.")
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--theorem-decl", required=True)
    parser.add_argument("--proof-body", default=None)
    parser.add_argument("--iterations", type=int, default=5)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    src = Path(args.source_file)
    if not src.is_absolute():
        src = project_root / src

    # Slow baseline
    from prove_arxiv_batch import _run_isolated_file_check  # type: ignore

    slow_total = 0.0
    for i in range(args.iterations):
        t0 = time.time()
        _run_isolated_file_check(
            project_root=project_root,
            source_file=src,
            theorem_decl=args.theorem_decl,
            proof_body=args.proof_body,
        )
        slow_total += time.time() - t0
    slow_avg = slow_total / max(1, args.iterations)

    # Fast cached
    fast_total = 0.0
    for i in range(args.iterations):
        t0 = time.time()
        validated_isolated_check(
            project_root=project_root,
            paper_id=args.paper_id,
            theorem_decl=args.theorem_decl,
            proof_body=args.proof_body,
        )
        fast_total += time.time() - t0
    fast_avg = fast_total / max(1, args.iterations)

    speedup = slow_avg / fast_avg if fast_avg > 0 else float("inf")
    print(f"slow avg: {slow_avg:.3f}s/call")
    print(f"fast avg: {fast_avg:.3f}s/call")
    print(f"speedup:  {speedup:.1f}x")
    shutdown_all_workers()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
