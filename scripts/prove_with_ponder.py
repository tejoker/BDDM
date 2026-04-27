#!/usr/bin/env python3
"""Run ponder-loop tactic search directly against LeanDojo proof states."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import re
import shutil
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from dotenv import load_dotenv
try:
    from desol_config import get_config as _get_config
    _CFG = _get_config()
except (ImportError, OSError, ValueError):
    _CFG = None  # type: ignore[assignment]
from proof_backend import (
    build_backend_health_report,
    build_backend_startup_summary,
    detect_extractdata_patch_status,
    DefaultLeanDojoClient,
    BackendHealthReport,
    format_backend_startup_summary,
    LeanDojoOpenRequest,
    emit_backend_parity_event,
    load_proof_backend_flags,
    probe_leandojo_importability,
    resolve_backend_choice,
)

try:
    from mistralai import Mistral
except ImportError:
    from mistralai.client import Mistral  # type: ignore[no-redef]

try:
    if os.environ.get("DESOL_FORCE_REPL_DOJO", "0") == "1":
        raise ModuleNotFoundError("DESOL_FORCE_REPL_DOJO override")
    from lean_dojo import Dojo, LeanGitRepo, Theorem
    from lean_dojo.interaction.dojo import LeanError, ProofFinished, ProofGivenUp, TacticState

    _USE_LEAN_DOJO = True
    _LEAN_DOJO_IMPORT_ERROR = ""
except Exception as exc:
    try:
        from lean_repl_dojo import REPLDojo as Dojo
        from lean_repl_dojo import LeanError, ProofFinished, ProofGivenUp, TacticState

        LeanGitRepo = Any  # type: ignore[assignment]
        Theorem = Any  # type: ignore[assignment]
        _USE_LEAN_DOJO = False
        _DOJO_BACKEND_AVAILABLE = True
        _LEAN_DOJO_IMPORT_ERROR = str(exc)
    except ModuleNotFoundError:
        Dojo = None  # type: ignore[assignment]
        LeanGitRepo = Any  # type: ignore[assignment]
        Theorem = Any  # type: ignore[assignment]
        LeanError = RuntimeError  # type: ignore[assignment]
        ProofFinished = type("ProofFinished", (), {})  # type: ignore[assignment]
        ProofGivenUp = type("ProofGivenUp", (), {})  # type: ignore[assignment]
        TacticState = type("TacticState", (), {})  # type: ignore[assignment]
        _USE_LEAN_DOJO = False
        _DOJO_BACKEND_AVAILABLE = False
        _LEAN_DOJO_IMPORT_ERROR = str(exc)
else:
    _DOJO_BACKEND_AVAILABLE = True

from ponder_loop import (
    generate_structured_draft_candidates,
    generate_full_proof_draft,
    load_premise_context,
    repair_full_proof_draft,
    run_ponder_loop,
)
from prove_with_ponder_exec import (
    StepRecord,
    _execute_draft,
    _is_lean_error,
    _is_proof_finished,
    _is_proof_given_up,
    _is_tactic_state,
    classify_lean_error,
    extract_tactic_theorem_names,
    repair_hint_for_error_class,
    validate_lean_name,
)
from prove_with_ponder_repo import create_snapshot_repo, repo_has_commit

logger = logging.getLogger(__name__)

_SNAPSHOT_CACHE: dict[str, tuple[Path, Path]] = {}
_DRAFT_SKELETON_CACHE: dict[str, str] = {}
_SKELETON_MEMORY_PATH = Path("output/proof_memory/goal_skeletons.json")


_SUSPICIOUS_REPL_MARKERS = (
    "tactic `rfl` failed",
    "generic",
    "unknown outcome",
    "line=1; message=tactic",
)


def _normalize_draft_for_theorem_body(draft: str) -> str:
    lines = [ln.rstrip() for ln in (draft or "").splitlines() if ln.strip()]
    if lines and lines[0].strip() == "by":
        lines = lines[1:]
    return "\n".join(lines).strip()


def _replace_theorem_body_in_source(*, lean_src: str, theorem_name: str, draft: str) -> tuple[str | None, str]:
    local_name = theorem_name.split(".")[-1].strip() or theorem_name.strip()
    if not local_name:
        return None, "empty theorem name"
    decl_re = re.compile(rf"(?m)^(theorem|lemma)\s+{re.escape(local_name)}\b")
    decl_match = decl_re.search(lean_src)
    if decl_match is None:
        return None, f"declaration not found for {local_name}"
    decl_start = decl_match.start()
    by_re = re.compile(r":=\s*by\b")
    by_match = by_re.search(lean_src, decl_start)
    if by_match is None:
        return None, f"could not find ':= by' for {local_name}"
    proof_start = by_match.end()

    tail = lean_src[proof_start:]
    end_rel = None
    for m in re.finditer(r"(?m)^(theorem|lemma|def|example|axiom|namespace|section|end)\b", tail):
        if m.start() == 0:
            continue
        end_rel = m.start()
        break
    proof_end = len(lean_src) if end_rel is None else proof_start + end_rel

    body = _normalize_draft_for_theorem_body(draft)
    if not body:
        return None, "empty draft body"
    indented_body = "\n".join(f"  {ln}" for ln in body.splitlines())
    patched = f"{lean_src[:proof_start]}\n{indented_body}\n{lean_src[proof_end:]}"
    return patched, "ok"


def _verify_draft_via_file_check(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    draft: str,
    timeout_s: int = 120,
) -> tuple[bool, str]:
    target_file = file_path if file_path.is_absolute() else (project_root / file_path)
    try:
        original = target_file.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"read_target_failed:{exc}"

    patched, detail = _replace_theorem_body_in_source(
        lean_src=original,
        theorem_name=theorem_name,
        draft=draft,
    )
    if patched is None:
        return False, f"patch_failed:{detail}"

    tmp_dir = project_root / "Desol"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="_tmp_file_verify_",
        suffix=".lean",
        dir=tmp_dir,
        delete=False,
    ) as _tf:
        tmp_lean = Path(_tf.name)
        _tf.write(patched.encode())
    try:
        pass  # file written above
        proc = subprocess.run(
            ["lake", "env", "lean", str(tmp_lean)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_s)),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        out = out.strip().replace("\n", " ")
        if proc.returncode == 0 and "sorry" not in _normalize_draft_for_theorem_body(draft):
            return True, "file_verify_ok"
        return False, f"file_verify_fail_rc={proc.returncode};{out[:280]}"
    except subprocess.TimeoutExpired:
        return False, f"file_verify_timeout:{timeout_s}s"
    except Exception as exc:
        return False, f"file_verify_exception:{exc}"
    finally:
        tmp_lean.unlink(missing_ok=True)


def _should_trigger_secondary_verifier(error_feedback: str, err_class: str) -> bool:
    text = (error_feedback or "").strip().lower()
    if "blocked_non_actionable_tactic" in text or "assumption_disabled_policy" in text:
        return False
    if err_class in {"policy-blocked", "assumption-mismatch"}:
        return False
    if err_class in {"generic", "reflexivity-mismatch", "incomplete-progress"}:
        return True
    return any(marker in text for marker in _SUSPICIOUS_REPL_MARKERS)


@dataclass
class DifficultyEstimate:
    level: str
    score: float
    goals: int
    state_chars: int
    hypotheses: int


def _state_shape_key(state_pp: str) -> str:
    text = str(state_pp or "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    goal_line = ""
    for ln in lines:
        if "⊢" in ln:
            goal_line = ln
            break
    hyp_count = sum(1 for ln in lines if ":" in ln and "⊢" not in ln)
    return f"hyp={hyp_count}|goal={goal_line[:220]}"


def _deterministic_prelude(state_pp: str) -> list[str]:
    """Cheap deterministic scaffold to reduce empty/non-actionable drafts."""
    text = str(state_pp or "")
    goal = ""
    for ln in text.splitlines():
        if "⊢" in ln:
            goal = ln.split("⊢", 1)[1].split("--", 1)[0].strip()
            break
    has_imp = ("→" in goal) or ("->" in goal) or ("∀" in goal)
    prelude: list[str] = []
    if goal and has_imp:
        prelude.append("intro h")
    if goal and ("∧" in goal) and (not has_imp):
        prelude.append("constructor")
    if "∃" in goal:
        prelude.append("refine ⟨?_, ?_⟩")
    return prelude[:2]


def _goal_text_from_state_pp_local(state_pp: str) -> str:
    for ln in (state_pp or "").splitlines():
        if "⊢" in ln:
            return ln.split("⊢", 1)[1].split("--", 1)[0].strip()
    return ""


def _goal_shape_from_state_pp(state_pp: str) -> str:
    g = _goal_text_from_state_pp_local(state_pp)
    if not g:
        return "other"
    if "∃!" in g or "exists!" in g.lower():
        return "exists_unique"
    if "∀" in g or "→" in g or "->" in g:
        return "imp_forall"
    if "∧" in g:
        return "conjunction"
    if "=" in g:
        return "equality"
    return "other"


def _first_tactic_line(draft: str) -> str:
    for ln in (draft or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("--") or s in {"by", "begin", "end"}:
            continue
        return s
    return ""


def _is_first_tactic_allowed_for_shape(shape: str, tactic: str) -> bool:
    t = (tactic or "").strip().lower()
    if not t:
        return False
    if "rfl" in t:
        return False
    if shape == "conjunction":
        return t.startswith(("constructor", "refine", "aesop", "exact", "apply", "have", "show"))
    if shape == "imp_forall":
        return t.startswith(("intro", "intros", "rintro", "have", "exact", "apply", "aesop"))
    if shape == "exists_unique":
        return t.startswith(("refine", "use", "constructor", "intro", "intros", "aesop", "have"))
    if shape == "equality":
        return t.startswith(
            ("simp", "simpa", "ring_nf", "linarith", "nlinarith", "omega", "calc", "have", "exact", "apply")
        )
    return True


def _filter_structured_candidates_by_shape(state_pp: str, candidates: list[str]) -> list[str]:
    shape = _goal_shape_from_state_pp(state_pp)
    if shape == "other":
        return candidates
    kept: list[str] = []
    for cand in candidates:
        first = _first_tactic_line(cand)
        if _is_first_tactic_allowed_for_shape(shape, first):
            kept.append(cand)
    return kept if kept else candidates


def _shape_default_first_tactic(shape: str) -> str:
    if shape == "conjunction":
        return "constructor"
    if shape == "imp_forall":
        return "intro h"
    if shape == "exists_unique":
        return "refine ⟨?w, ?hex, ?uniq⟩"
    if shape == "equality":
        return "simp"
    return ""


def _enforce_first_tactic_policy(state_pp: str, draft: str) -> str:
    shape = _goal_shape_from_state_pp(state_pp)
    lines = [ln.rstrip() for ln in (draft or "").splitlines() if ln.strip()]
    # Hard policy: remove reflexivity-only and assumption-family lines from generated drafts.
    lines = [ln for ln in lines if not re.search(r"\brfl\b", ln)]
    lines = [
        ln
        for ln in lines
        if not re.search(r"\b(assumption|aesop|solve_by_elim|tauto|trivial)\b", ln)
    ]
    draft_sanitized = "\n".join(lines).strip()
    if shape == "other":
        return draft_sanitized
    first = _first_tactic_line(draft)
    if _is_first_tactic_allowed_for_shape(shape, first):
        return draft_sanitized
    default_tac = _shape_default_first_tactic(shape)
    if not default_tac:
        return draft_sanitized
    lines = [ln.rstrip() for ln in draft_sanitized.splitlines() if ln.strip()]
    if lines:
        lines = lines[1:]
    return "\n".join([default_tac, *lines]).strip()


def _seed_draft_with_cache_and_prelude(state_pp: str, draft: str) -> str:
    key = _state_shape_key(state_pp)
    normalized = (draft or "").strip()
    cached = _DRAFT_SKELETON_CACHE.get(key, "").strip()
    if (not normalized) or re.fullmatch(r"(?is)\s*by\s*sorry\s*", normalized):
        if cached:
            return cached
    prelude = _deterministic_prelude(state_pp)
    if not prelude:
        return normalized
    out_lines: list[str] = []
    if normalized:
        out_lines = [ln.rstrip() for ln in normalized.splitlines() if ln.strip()]
    # Avoid duplicating deterministic prelude lines if model already emitted them.
    existing = "\n".join(out_lines).lower()
    for tac in prelude:
        if tac.lower() not in existing:
            out_lines.insert(0, tac)
    return "\n".join(out_lines).strip()


def _load_persistent_skeleton_cache(project_root: Path) -> None:
    path = project_root / _SKELETON_MEMORY_PATH
    if not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    for k, v in raw.items():
        ks = str(k).strip()
        vs = str(v).strip()
        if ks and vs:
            _DRAFT_SKELETON_CACHE[ks] = vs


def _save_persistent_skeleton_cache(project_root: Path) -> None:
    path = project_root / _SKELETON_MEMORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep bounded memory size.
    items = list(_DRAFT_SKELETON_CACHE.items())[:500]
    payload = {k: v for k, v in items}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_leandojo_repo(project_root: Path) -> tuple[Any, Path | None]:
    """Return LeanGitRepo and optional temp dir to clean up."""
    if not _USE_LEAN_DOJO:
        return None, None

    try:
        if repo_has_commit(project_root):
            return LeanGitRepo.from_path(project_root), None
    except Exception:
        pass

    try:
        cache_key = str(project_root.resolve())
        if cache_key in _SNAPSHOT_CACHE:
            cached_tmp_root, cached_repo = _SNAPSHOT_CACHE[cache_key]
            if cached_repo.exists() and cached_tmp_root.exists():
                # Reuse cached snapshot to avoid repeated copytree+git init overhead.
                return LeanGitRepo.from_path(cached_repo), None
            _SNAPSHOT_CACHE.pop(cache_key, None)

        snapshot_repo, tmp_root = create_snapshot_repo(project_root)
        _SNAPSHOT_CACHE[cache_key] = (tmp_root, snapshot_repo)
        # Keep the cached snapshot alive for the process lifetime. Returning tmp_root
        # here caused callers to delete the cache immediately, leaving later Dojo opens
        # with stale /tmp/... paths.
        return LeanGitRepo.from_path(snapshot_repo), None
    except Exception as exc:
        raise RuntimeError(f"Failed preparing LeanDojo git repository: {exc}") from exc


def _open_dojo(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> tuple[Any, Path | None]:
    """Create the right dojo context manager for the active backend."""
    backend_flags = load_proof_backend_flags()
    backend_choice = resolve_backend_choice(
        leandojo_available=_USE_LEAN_DOJO,
        flags=backend_flags,
    )
    emit_backend_parity_event(
        backend_flags,
        "backend-open-start",
        {
            "backend": backend_choice,
            "project_root": str(project_root),
            "file_path": str(file_path),
            "theorem": theorem_name,
            "dojo_timeout": dojo_timeout,
            "phase1_enabled": backend_flags.phase1_enabled,
        },
    )

    if not _DOJO_BACKEND_AVAILABLE and backend_choice == "leandojo":
        raise RuntimeError(
            "No proof backend available: install lean_dojo or provide scripts/lean_repl_dojo.py"
        )

    if backend_choice == "leandojo":
        try:
            def _open_with_native_leandojo(request: LeanDojoOpenRequest) -> tuple[Any, Path | None]:
                repo, tmp_root = _prepare_leandojo_repo(request.project_root)
                theorem = Theorem(repo, request.file_path, request.theorem_name)
                return Dojo(theorem, timeout=request.dojo_timeout), tmp_root

            client = DefaultLeanDojoClient(
                _open_with_native_leandojo
            )
            dojo_ctx, tmp_root = client.open_dojo(
                LeanDojoOpenRequest(
                    project_root=project_root,
                    file_path=file_path,
                    theorem_name=theorem_name,
                    dojo_timeout=dojo_timeout,
                )
            )
            emit_backend_parity_event(
                backend_flags,
                "backend-open-success",
                {
                    "backend": backend_choice,
                    "tmp_snapshot": bool(tmp_root),
                },
            )
            return dojo_ctx, tmp_root
        except Exception as e:
            import traceback
            logger.warning(f"LeanDojo initialization failed: {e}\n{traceback.format_exc()}")
            emit_backend_parity_event(
                backend_flags,
                "backend-open-failure",
                {
                    "backend": backend_choice,
                    "error": str(e),
                },
            )
            if "git" in str(e).lower() or "lake" in str(e).lower():
                msg = (
                    f"Backend initialization failed during toolchain setup:\n"
                    f"  Error: {e}\n"
                    f"  This typically means:\n"
                    f"    - git/lake toolchain issue or mathlib4 unreachable\n"
                    f"    - Lean version mismatch with project\n"
                    f"  Fallback: Use model-only mode (--mode mcts-draft without backend)\n"
                )
                raise RuntimeError(msg) from e
            raise

    # REPLDojo path
    try:
        from lean_repl_dojo import REPLDojo
    except ModuleNotFoundError as exc:
        emit_backend_parity_event(
            backend_flags,
            "backend-open-failure",
            {
                "backend": backend_choice,
                "error": "REPLDojo unavailable",
            },
        )
        raise RuntimeError(
            "DESOL_PROOF_BACKEND resolved to repldojo but scripts/lean_repl_dojo.py is unavailable"
        ) from exc

    emit_backend_parity_event(
        backend_flags,
        "backend-open-success",
        {
            "backend": "repldojo",
            "tmp_snapshot": False,
        },
    )
    local_theorem_name = theorem_name.split(".")[-1].strip() or theorem_name.strip()
    return REPLDojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=local_theorem_name,
        timeout=dojo_timeout,
    ), None


def check_backend_health(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> BackendHealthReport:
    """Run lightweight backend-open health check with structured diagnostics."""
    backend_flags = load_proof_backend_flags()
    try:
        backend = resolve_backend_choice(leandojo_available=_USE_LEAN_DOJO, flags=backend_flags)
    except Exception as exc:
        return build_backend_health_report(backend="resolve", error_text=str(exc))

    if backend == "leandojo":
        patch_status = detect_extractdata_patch_status()
        if patch_status in {"unpatched", "missing", "unknown"}:
            return build_backend_health_report(
                backend=backend,
                error_text=(
                    "LeanDojo ExtractData compatibility check failed "
                    f"(status={patch_status})"
                ),
            )

    dojo_ctx: Any | None = None
    tmp_root: Path | None = None
    try:
        dojo_ctx, tmp_root = _open_dojo(
            project_root=project_root,
            file_path=file_path,
            theorem_name=theorem_name,
            dojo_timeout=max(30, min(dojo_timeout, 180)),
        )
        with dojo_ctx as (_dojo, state):
            if not _is_tactic_state(state):
                return build_backend_health_report(
                    backend=backend,
                    error_text=(
                        "Backend returned unexpected initial state type: "
                        f"{type(state).__name__}"
                    ),
                )
        emit_backend_parity_event(
            backend_flags,
            "backend-health-success",
            {"backend": backend},
        )
        return build_backend_health_report(backend=backend)
    except Exception as exc:
        emit_backend_parity_event(
            backend_flags,
            "backend-health-failure",
            {"backend": backend, "error": str(exc)},
        )
        return build_backend_health_report(backend=backend, error_text=str(exc))
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _estimate_theorem_difficulty(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    dojo_timeout: int,
) -> DifficultyEstimate:
    """Estimate theorem search difficulty from the initial proof state."""
    dojo_ctx, tmp_root = _open_dojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=min(dojo_timeout, 180),
    )
    try:
        with dojo_ctx as (_dojo, state):
            state_text = getattr(state, "pp", "") or ""
            goals = int(getattr(state, "num_goals", 0) or state_text.count("⊢") or 1)
            state_chars = len(state_text)
            hypotheses = sum(
                1
                for line in state_text.splitlines()
                if line.strip() and not line.strip().startswith("⊢")
            )

            goals_factor = min(goals / 6.0, 1.0)
            chars_factor = min(state_chars / 2200.0, 1.0)
            hyps_factor = min(hypotheses / 35.0, 1.0)
            score = (0.55 * goals_factor) + (0.25 * chars_factor) + (0.20 * hyps_factor)

            if score >= 0.67:
                level = "hard"
            elif score >= 0.36:
                level = "medium"
            else:
                level = "easy"

            return DifficultyEstimate(
                level=level,
                score=min(max(score, 0.0), 1.0),
                goals=max(goals, 1),
                state_chars=state_chars,
                hypotheses=hypotheses,
            )
    except Exception:
        # Conservative fallback when dojo pre-estimation fails.
        return DifficultyEstimate(
            level="medium",
            score=0.5,
            goals=1,
            state_chars=0,
            hypotheses=0,
        )
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def _adapt_mcts_params(
    *,
    profile: str,
    base_workers: int,
    iterations: int,
    repair_variants: int,
    max_depth: int,
    difficulty: DifficultyEstimate,
) -> tuple[int, int, int, int, str]:
    """Return adapted (workers, iterations, variants, depth, rationale)."""
    workers = max(1, base_workers)
    iters = max(1, iterations)
    variants = max(1, repair_variants)
    depth = max(1, max_depth)

    if profile == "throughput":
        workers = max(workers, min(mp.cpu_count(), workers + 1))
        depth = max(2, depth - 1)
        rationale = "throughput"
        return workers, iters, variants, depth, rationale

    if profile == "depth":
        workers = max(1, workers // 2)
        iters = max(iters, iters * 2)
        variants = max(variants, variants + 1)
        depth = max(depth, depth + 2)
        rationale = "depth"
        return workers, iters, variants, depth, rationale

    if profile != "hybrid":
        return workers, iters, variants, depth, "fixed"

    # Hybrid: easy => breadth, hard => depth.
    if difficulty.level == "easy":
        workers = max(workers, min(mp.cpu_count(), workers + 1))
        iters = max(iters, workers * 2)
        variants = max(1, variants - 1)
        depth = max(2, depth - 1)
        rationale = "hybrid-easy"
    elif difficulty.level == "hard":
        workers = max(1, workers // 2)
        iters = max(iters * 2, workers * 6)
        variants = max(variants + 1, 3)
        depth = max(depth + 2, 5)
        rationale = "hybrid-hard"
    else:
        iters = max(iters, workers * 3)
        rationale = "hybrid-medium"

    return workers, iters, variants, depth, rationale


def prove_with_ponder(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    max_steps: int = 20,
    max_attempts_per_state: int = 3,
    ponder_max_turns: int = 0,
    ponder_min_act_turns: int = 2,
    ponder_max_act_turns: int = 8,
    confidence_threshold: float = 0.9,
    temperature: float = 0.2,
    dojo_timeout: int = 600,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
) -> tuple[bool, list[StepRecord], str]:
    dojo_ctx, tmp_root = _open_dojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=dojo_timeout,
    )

    records: list[StepRecord] = []

    try:
        with dojo_ctx as (dojo, state):
            if not _is_tactic_state(state):
                return False, records, f"Unexpected initial state type: {type(state).__name__}"

            for step in range(1, max_steps + 1):
                current_state = state
                failed_attempts: list[tuple[str, str]] = []

                for attempt in range(1, max_attempts_per_state + 1):
                    state_context = current_state.pp
                    if failed_attempts:
                        lines = ["Previous failed attempts on this exact state:"]
                        for i, (tac, err) in enumerate(failed_attempts, start=1):
                            err_class = classify_lean_error(err)
                            lines.append(f"{i}. tactic: {tac}")
                            lines.append(f"   lean_error: {err}")
                            lines.append(f"   error_class: {err_class}")
                            lines.append(f"   {repair_hint_for_error_class(err_class)}")
                        lines.append("Propose a different tactic.")
                        state_context = state_context + "\n\n" + "\n".join(lines)

                    try:
                        ponder_result = run_ponder_loop(
                            lean_state=state_context,
                            client=client,
                            model=model,
                            max_turns=(ponder_max_turns if ponder_max_turns > 0 else None),
                            temperature=temperature,
                            min_act_turns=ponder_min_act_turns,
                            max_act_turns=ponder_max_act_turns,
                            confidence_threshold=confidence_threshold,
                            premise_context=premise_context,
                            retrieval_index_path=retrieval_index_path,
                            retrieval_top_k=retrieval_top_k,
                        )
                    except TimeoutError as exc:
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic="",
                                model_turns=0,
                                result="model-timeout",
                                detail=str(exc),
                            )
                        )
                        failed_attempts.append(("<no tactic>", f"ponder timeout: {exc}"))
                        continue
                    tactic = ponder_result.tactic.strip()

                    if re.fullmatch(r"\s*sorry\s*", tactic):
                        err = "Tactic is 'sorry': proof is incomplete. Propose a real tactic."
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="lean-error",
                                detail=err,
                            )
                        )
                        failed_attempts.append((tactic, err))
                        continue

                    invalid_name = None
                    for candidate_name in extract_tactic_theorem_names(tactic):
                        if not validate_lean_name(candidate_name, project_root):
                            invalid_name = candidate_name
                            break

                    if invalid_name is not None:
                        err = (
                            f"The theorem {invalid_name} does not exist in Mathlib. "
                            "Use a verified name."
                        )
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="lean-error",
                                detail=err,
                            )
                        )
                        failed_attempts.append((tactic, err))
                        continue

                    outcome = dojo.run_tac(current_state, tactic)

                    if _is_tactic_state(outcome):
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="state-advanced",
                                detail=f"goals={outcome.num_goals}",
                            )
                        )
                        state = outcome
                        break

                    if _is_proof_finished(outcome):
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="proof-finished",
                                detail=(outcome.message or ""),
                            )
                        )
                        return True, records, "Proof finished"

                    if _is_lean_error(outcome):
                        err = str(getattr(outcome, "error", "")).strip()
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="lean-error",
                                detail=err,
                            )
                        )
                        failed_attempts.append((tactic, err))
                        continue

                    if _is_proof_given_up(outcome):
                        records.append(
                            StepRecord(
                                step=step,
                                attempt=attempt,
                                tactic=tactic,
                                model_turns=ponder_result.turns,
                                result="proof-given-up",
                            )
                        )
                        return False, records, "Model gave up the proof"

                    records.append(
                        StepRecord(
                            step=step,
                            attempt=attempt,
                            tactic=tactic,
                            model_turns=ponder_result.turns,
                            result="unknown-outcome",
                            detail=type(outcome).__name__,
                        )
                    )
                    return False, records, f"Unknown LeanDojo outcome: {type(outcome).__name__}"
                else:
                    return (
                        False,
                        records,
                        f"Failed to advance proof state after {max_attempts_per_state} attempts at step {step}",
                    )

            return False, records, f"Reached max_steps={max_steps} without finishing proof"
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def prove_with_full_draft_repair(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    client: Mistral,
    model: str,
    repair_rounds: int = 5,
    temperature: float = 0.2,
    dojo_timeout: int = 600,
    premise_context: str = "",
    retrieval_index_path: str = "",
    retrieval_top_k: int = 12,
    informal_proof_hint: str = "",
) -> tuple[bool, list[StepRecord], str]:
    _load_persistent_skeleton_cache(project_root)
    dojo_ctx, tmp_root = _open_dojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        dojo_timeout=dojo_timeout,
    )
    records: list[StepRecord] = []

    try:
        with dojo_ctx as (dojo, state):
            if not _is_tactic_state(state):
                return False, records, f"Unexpected initial state type: {type(state).__name__}"

            try:
                structured_candidates = generate_structured_draft_candidates(
                    lean_state=state.pp,
                    client=client,
                    model=model,
                    informal_proof_hint=informal_proof_hint,
                    temperature=temperature,
                    premise_context=premise_context,
                    retrieval_index_path=retrieval_index_path,
                    retrieval_top_k=retrieval_top_k,
                )
            except Exception:
                structured_candidates = []
            structured_candidates = _filter_structured_candidates_by_shape(state.pp, structured_candidates)
            if not structured_candidates:
                structured_candidates = [
                    generate_full_proof_draft(
                        lean_state=state.pp,
                        client=client,
                        model=model,
                        informal_proof_hint=informal_proof_hint,
                        temperature=temperature,
                        premise_context=premise_context,
                        retrieval_index_path=retrieval_index_path,
                        retrieval_top_k=retrieval_top_k,
                    )
                ]
                structured_candidates = _filter_structured_candidates_by_shape(state.pp, structured_candidates)

            # Beam-select the best initial draft by executed progress.
            best_draft = ""
            best_score = -10**9
            best_feedback = ""
            for cand in structured_candidates[:6]:
                cand_seeded = _seed_draft_with_cache_and_prelude(state.pp, cand)
                cand_seeded = _enforce_first_tactic_policy(state.pp, cand_seeded)
                solved_c, _st_c, recs_c, fb_c = _execute_draft(
                    dojo=dojo,
                    initial_state=state,
                    draft=cand_seeded,
                    round_idx=0,
                )
                score = 0
                score += 1000 if solved_c else 0
                score += sum(1 for r in recs_c if getattr(r, "result", "") == "state-advanced")
                score -= sum(1 for r in recs_c if getattr(r, "result", "") in {"lean-error", "proof-given-up"})
                if score > best_score:
                    best_score = score
                    best_draft = cand_seeded
                    best_feedback = fb_c
                if solved_c:
                    current_draft = cand_seeded
                    records.extend(recs_c)
                    key = _state_shape_key(state.pp)
                    executed = [r.tactic for r in recs_c if getattr(r, "tactic", "").strip()]
                    if executed:
                        _DRAFT_SKELETON_CACHE[key] = "\n".join(executed[:4]).strip()
                        _save_persistent_skeleton_cache(project_root)
                    return True, records, "Proof finished in structured-beam precheck"

            current_draft = best_draft.strip() if best_draft.strip() else _seed_draft_with_cache_and_prelude(
                state.pp, structured_candidates[0]
            )
            if best_feedback:
                current_draft = _seed_draft_with_cache_and_prelude(state.pp, current_draft)
            current_draft = _enforce_first_tactic_policy(state.pp, current_draft)

            for round_idx in range(1, repair_rounds + 1):
                solved, _new_state, round_records, error_feedback = _execute_draft(
                    dojo=dojo,
                    initial_state=state,
                    draft=_enforce_first_tactic_policy(state.pp, current_draft),
                    round_idx=round_idx,
                )
                records.extend(round_records)

                if solved:
                    key = _state_shape_key(state.pp)
                    executed = [r.tactic for r in round_records if getattr(r, "tactic", "").strip()]
                    if executed:
                        _DRAFT_SKELETON_CACHE[key] = "\n".join(executed[:4]).strip()
                        _save_persistent_skeleton_cache(project_root)
                    return True, records, f"Proof finished in round {round_idx}"

                err_class = classify_lean_error(error_feedback)
                if _should_trigger_secondary_verifier(error_feedback, err_class):
                    verify_ok, verify_detail = _verify_draft_via_file_check(
                        project_root=project_root,
                        file_path=file_path,
                        theorem_name=theorem_name,
                        draft=current_draft,
                        timeout_s=max(60, min(dojo_timeout, 180)),
                    )
                    records.append(
                        StepRecord(
                            step=round_idx,
                            attempt=max(1, len(round_records) + 1),
                            tactic="__file_level_verify__",
                            model_turns=0,
                            result=("file-verify-pass" if verify_ok else "file-verify-fail"),
                            detail=verify_detail,
                        )
                    )
                    if verify_ok:
                        key = _state_shape_key(state.pp)
                        executed = [r.tactic for r in round_records if getattr(r, "tactic", "").strip()]
                        if executed:
                            _DRAFT_SKELETON_CACHE[key] = "\n".join(executed[:4]).strip()
                            _save_persistent_skeleton_cache(project_root)
                        return True, records, (
                            f"Proof accepted by secondary file-level verifier in round {round_idx}"
                        )

                hint = repair_hint_for_error_class(err_class)
                if err_class == "reflexivity-mismatch":
                    hint = hint + "\nHard policy: do not start with `rfl`/`exact rfl`."
                enriched_feedback = (
                    f"{error_feedback}\nerror_class: {err_class}\n{hint}"
                )

                if round_idx == repair_rounds:
                    return False, records, (
                        f"Failed after repair_rounds={repair_rounds}; "
                        f"last_error={error_feedback} error_class={err_class}"
                    )

                current_draft = repair_full_proof_draft(
                    lean_state=state.pp,
                    current_draft=current_draft,
                    error_feedback=enriched_feedback,
                    client=client,
                    model=model,
                    informal_proof_hint=informal_proof_hint,
                    temperature=temperature,
                    premise_context=premise_context,
                    retrieval_index_path=retrieval_index_path,
                    retrieval_top_k=retrieval_top_k,
                )
                current_draft = _seed_draft_with_cache_and_prelude(state.pp, current_draft)
                current_draft = _enforce_first_tactic_policy(state.pp, current_draft)

            return False, records, "Exhausted repair loop"
    finally:
        if tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)


def run_proof(
    *,
    project_root: Path,
    file_path: Path,
    theorem_name: str,
    mode: str = "full-draft",
    max_repair_rounds: int = 3,
    model: str = "",
    dojo_timeout: int = 180,
) -> dict[str, Any]:
    """Backward-compatible integration entrypoint used by tests.

    Returns a dict with at least:
      - proved: bool
      - status: str
      - rounds_used: int
      - error: str
    """
    project_root = Path(project_root).resolve()
    file_path = Path(file_path)

    # 1) Deterministic quick lane for simple goals.
    # Prefer REPLDojo here to avoid LeanDojo ExtractData fragility in temp repos.
    try:
        from lean_repl_dojo import REPLDojo as _QuickREPLDojo
        with _QuickREPLDojo(
            project_root=project_root,
            file_path=file_path,
            theorem_name=theorem_name,
            timeout=max(30, int(dojo_timeout)),
        ) as (dojo, state):
            if _is_tactic_state(state):
                for tac in ("omega", "simp", "aesop", "ring_nf", "linarith"):
                    out = dojo.run_tac(state, tac)
                    if _is_proof_finished(out):
                        return {
                            "proved": True,
                            "status": "FULLY_PROVEN",
                            "rounds_used": 1,
                            "error": "",
                        }
                    if _is_tactic_state(out):
                        state = out
    except Exception:
        try:
            dojo_ctx, tmp_root = _open_dojo(
                project_root=project_root,
                file_path=file_path,
                theorem_name=theorem_name,
                dojo_timeout=max(30, int(dojo_timeout)),
            )
            try:
                with dojo_ctx as (dojo, state):
                    if _is_tactic_state(state):
                        for tac in ("omega", "simp", "aesop", "ring_nf", "linarith"):
                            out = dojo.run_tac(state, tac)
                            if _is_proof_finished(out):
                                return {
                                    "proved": True,
                                    "status": "FULLY_PROVEN",
                                    "rounds_used": 1,
                                    "error": "",
                                }
                            if _is_tactic_state(out):
                                state = out
            finally:
                if tmp_root is not None:
                    shutil.rmtree(tmp_root, ignore_errors=True)
        except Exception:
            pass

    # 2) Model-assisted fallback for full-draft mode.
    if mode == "full-draft":
        api_key = os.getenv("MISTRAL_API_KEY", "").strip()
        chosen_model = model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
        if api_key and chosen_model:
            try:
                client = Mistral(api_key=api_key)
                ok, records, summary = prove_with_full_draft_repair(
                    project_root=project_root,
                    file_path=file_path,
                    theorem_name=theorem_name,
                    client=client,
                    model=chosen_model,
                    repair_rounds=max(1, int(max_repair_rounds)),
                    dojo_timeout=max(30, int(dojo_timeout)),
                )
                return {
                    "proved": bool(ok),
                    "status": "FULLY_PROVEN" if ok else "UNRESOLVED",
                    "rounds_used": len(records),
                    "error": "" if ok else summary,
                }
            except Exception as exc:
                return {
                    "proved": False,
                    "status": "UNRESOLVED",
                    "rounds_used": 0,
                    "error": str(exc),
                }

    return {
        "proved": False,
        "status": "UNRESOLVED",
        "rounds_used": 0,
        "error": "proof_not_completed",
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drive Lean proof search with ponder loop + LeanDojo")
    parser.add_argument("--project-root", default=".", help="Path to Lean git repo root")
    parser.add_argument("--file", required=True, help="Lean file path relative to project root")
    parser.add_argument("--theorem", required=True, help="Fully-qualified theorem name")
    parser.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL)")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-attempts-per-state", type=int, default=3)
    parser.add_argument(
        "--ponder-max-turns",
        type=int,
        default=0,
        help="Fixed ACT budget per tactic proposal; 0 means adaptive",
    )
    parser.add_argument("--ponder-min-act-turns", type=int, default=2)
    parser.add_argument("--ponder-max-act-turns", type=int, default=8)
    parser.add_argument("--confidence-threshold", type=float, default=0.9)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--dojo-timeout", type=int, default=600)
    parser.add_argument(
        "--mode",
        choices=["tactic", "full-draft", "mcts-draft", "world-model-draft"],
        default="tactic",
        help="Proof mode: tactic-by-tactic, full draft + repair loop, draft-level MCTS, or world-model guided MCTS",
    )
    _default_repair_rounds = _CFG.proof_search.max_repair_rounds if _CFG else 5
    parser.add_argument(
        "--repair-rounds",
        type=int,
        default=_default_repair_rounds,
        help="Number of full-draft repair rounds (full-draft mode only)",
    )
    parser.add_argument(
        "--informal-proof-hint",
        default="",
        help="Optional informal proof hint text injected at draft generation",
    )
    parser.add_argument(
        "--informal-proof-hint-file",
        default="",
        help="Path to a file containing informal proof hint text",
    )
    parser.add_argument(
        "--premise-file",
        default="",
        help="Path to .toon knowledge inventory for premise injection",
    )
    parser.add_argument(
        "--premise-namespace",
        default="ProbabilityTheory",
        help="Namespace filter applied when loading premise-file",
    )
    parser.add_argument(
        "--retrieval-index",
        default="",
        help="Path to retrieval index JSON for dynamic top-k premise injection",
    )
    parser.add_argument(
        "--retrieval-top-k",
        type=int,
        default=12,
        help="Number of retrieved premises injected per proof state",
    )
    parser.add_argument(
        "--mcts-iterations",
        type=int,
        default=12,
        help="Number of draft-MCTS selection/expansion iterations (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-repair-variants",
        type=int,
        default=3,
        help="Repair variants generated per draft-MCTS node (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-max-depth",
        type=int,
        default=5,
        help="Maximum draft-MCTS depth in repair rounds (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-exploration-c",
        type=float,
        default=1.4,
        help="UCB exploration constant for draft-MCTS (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-parallel-workers",
        type=int,
        default=0,
        help="Parallel draft-MCTS workers; 0 selects automatically from --mcts-cpu-target",
    )
    parser.add_argument(
        "--mcts-cpu-target",
        type=float,
        default=0.8,
        help="Target CPU fraction for auto worker selection (mcts-draft mode)",
    )
    parser.add_argument(
        "--mcts-profile",
        choices=["fixed", "throughput", "depth", "hybrid"],
        default="hybrid",
        help="Draft-MCTS tuning profile (mcts-draft mode)",
    )
    parser.add_argument(
        "--world-model-ledger-root",
        default="output/verification_ledgers",
        help="Ledger root used for world-model bridge priors (world-model-draft mode)",
    )
    parser.add_argument(
        "--world-model-budget",
        type=int,
        default=24,
        help="World-model MCTS budget used to precompute bridge priors",
    )
    parser.add_argument(
        "--backend-health-check",
        action="store_true",
        help="Run backend initialization health check and exit",
    )
    return parser


def main() -> int:
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    file_path = Path(args.file)
    backend_flags = load_proof_backend_flags()
    leandojo_available, leandojo_import_error = probe_leandojo_importability()
    if _USE_LEAN_DOJO:
        leandojo_available = True
        leandojo_import_error = ""
    elif _LEAN_DOJO_IMPORT_ERROR and not leandojo_import_error:
        leandojo_import_error = _LEAN_DOJO_IMPORT_ERROR
    startup = build_backend_startup_summary(
        project_root=project_root,
        flags=backend_flags,
        leandojo_available=leandojo_available,
        leandojo_import_error=leandojo_import_error,
    )
    if args.backend_health_check or backend_flags.phase1_enabled:
        for line in format_backend_startup_summary(startup):
            print(line)

    if args.backend_health_check:
        report = check_backend_health(
            project_root=project_root,
            file_path=file_path,
            theorem_name=args.theorem,
            dojo_timeout=args.dojo_timeout,
        )
        if report.ok:
            print(f"[ok] {report.message}")
            return 0
        print(f"[fail] code={report.error_code} backend={report.backend} message={report.message}")
        if report.recommendation:
            print(f"[hint] {report.recommendation}")
        return 1

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY is not set")
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    if not model:
        print("[fail] no model configured")
        return 1

    informal_hint = args.informal_proof_hint.strip()
    if args.informal_proof_hint_file:
        informal_hint = Path(args.informal_proof_hint_file).read_text(encoding="utf-8").strip()

    premise_context = ""
    if args.premise_file:
        premise_context = load_premise_context(
            args.premise_file,
            namespace_filter=args.premise_namespace,
        )

    client = Mistral(api_key=api_key)

    if args.mode == "full-draft":
        ok, records, summary = prove_with_full_draft_repair(
            project_root=project_root,
            file_path=file_path,
            theorem_name=args.theorem,
            client=client,
            model=model,
            repair_rounds=args.repair_rounds,
            temperature=args.temperature,
            dojo_timeout=args.dojo_timeout,
            premise_context=premise_context,
            retrieval_index_path=args.retrieval_index,
            retrieval_top_k=args.retrieval_top_k,
            informal_proof_hint=informal_hint,
        )
    elif args.mode == "mcts-draft":
        # mcts-draft uses state-level MCTS.
        from mcts_search import run_state_mcts

        ok, tactics, summary = run_state_mcts(
            project_root=project_root,
            theorem_statement="",
            client=client,
            model=model,
            iterations=args.mcts_iterations,
            n_tactics=args.mcts_repair_variants,
            max_depth=args.mcts_max_depth,
            temperature=args.temperature,
            premise_context=premise_context,
            retrieval_index_path=args.retrieval_index,
            retrieval_top_k=args.retrieval_top_k,
            file_path=file_path,
            theorem_name=args.theorem,
        )
        raw_records = [{"tactic": t, "result": "state-advanced", "step": i, "attempt": 0,
                        "model_turns": 1, "detail": ""} for i, t in enumerate(tactics)]
        records = [
            StepRecord(
                step=int(r.get("step", 0)),
                attempt=int(r.get("attempt", 0)),
                tactic=str(r.get("tactic", "")),
                model_turns=int(r.get("model_turns", 0)),
                result=str(r.get("result", "")),
                detail=str(r.get("detail", "")),
            )
            for r in raw_records
        ]
    elif args.mode == "world-model-draft":
        # Dedicated world-model proof loop: iterate world priors + MCTS episodes.
        from mcts_search import run_state_mcts

        wm_rounds = max(1, min(4, int(args.world_model_budget // max(1, args.mcts_iterations))))
        wm_records: list[dict[str, Any]] = []
        best = {"ok": False, "tactics": [], "summary": "world-model: no attempt"}

        for wr in range(wm_rounds):
            local_premise_context = premise_context
            try:
                from world_model_bridge import run_world_model_bridge_search

                wm = run_world_model_bridge_search(
                    target_theorem=args.theorem,
                    ledger_root=Path(args.world_model_ledger_root),
                    budget=max(1, int(args.world_model_budget)),
                    max_depth=max(1, int(args.mcts_max_depth)),
                    max_candidates_per_assumption=max(1, int(args.mcts_repair_variants)),
                )
                prior_theorems = [
                    str(a.get("theorem_name", "")).strip()
                    for a in wm.actions_taken
                    if isinstance(a, dict) and str(a.get("kind", "")) == "bridge_candidate"
                ]
                prior_theorems = [p for p in prior_theorems if p and not p.startswith("model_prior:")]
                if prior_theorems:
                    prior_block = "\n".join(f"- {p}" for p in sorted(set(prior_theorems)))
                    local_premise_context = (
                        (local_premise_context + "\n\n") if local_premise_context else ""
                    ) + "World-model bridge priors:\n" + prior_block
                wm_records.append(
                    {
                        "round": wr,
                        "grounded": int(wm.grounded_count),
                        "assumptions_total": int(wm.assumptions_total),
                        "reward": float(wm.reward),
                    }
                )
            except Exception as exc:
                wm_records.append({"round": wr, "error": str(exc)})

            ok_i, tactics_i, summary_i = run_state_mcts(
                project_root=project_root,
                theorem_statement="",
                client=client,
                model=model,
                iterations=max(2, int(args.mcts_iterations)),
                n_tactics=max(1, int(args.mcts_repair_variants)),
                max_depth=max(1, int(args.mcts_max_depth)),
                temperature=args.temperature,
                premise_context=local_premise_context,
                retrieval_index_path=args.retrieval_index,
                retrieval_top_k=args.retrieval_top_k,
                file_path=file_path,
                theorem_name=args.theorem,
            )
            if ok_i:
                best = {"ok": True, "tactics": tactics_i, "summary": summary_i}
                break
            if len(tactics_i) > len(best.get("tactics", [])):
                best = {"ok": False, "tactics": tactics_i, "summary": summary_i}

        ok = bool(best["ok"])
        tactics = list(best["tactics"])
        summary = str(best["summary"]) + f" | wm_rounds={wm_rounds}"
        raw_records = [{"tactic": t, "result": "state-advanced", "step": i, "attempt": 0,
                        "model_turns": 1, "detail": "world-model"} for i, t in enumerate(tactics)]
        for w in wm_records:
            raw_records.append(
                {
                    "tactic": "",
                    "result": "world-model-round",
                    "step": int(w.get("round", 0)),
                    "attempt": 0,
                    "model_turns": 0,
                    "detail": json.dumps(w, ensure_ascii=False),
                }
            )
        records = [
            StepRecord(
                step=int(r.get("step", 0)),
                attempt=int(r.get("attempt", 0)),
                tactic=str(r.get("tactic", "")),
                model_turns=int(r.get("model_turns", 0)),
                result=str(r.get("result", "")),
                detail=str(r.get("detail", "")),
            )
            for r in raw_records
        ]
    else:
        ok, records, summary = prove_with_ponder(
            project_root=project_root,
            file_path=file_path,
            theorem_name=args.theorem,
            client=client,
            model=model,
            max_steps=args.max_steps,
            max_attempts_per_state=args.max_attempts_per_state,
            ponder_max_turns=args.ponder_max_turns,
            ponder_min_act_turns=args.ponder_min_act_turns,
            ponder_max_act_turns=args.ponder_max_act_turns,
            confidence_threshold=args.confidence_threshold,
            temperature=args.temperature,
            dojo_timeout=args.dojo_timeout,
            premise_context=premise_context,
            retrieval_index_path=args.retrieval_index,
            retrieval_top_k=args.retrieval_top_k,
        )

    for r in records:
        detail = f" | {r.detail}" if r.detail else ""
        print(
            f"[step {r.step} attempt {r.attempt}] "
            f"tactic={r.tactic!r} | model_turns={r.model_turns} | result={r.result}{detail}"
        )

    if ok:
        print(f"[ok] {summary}")
        return 0

    print(f"[fail] {summary}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
