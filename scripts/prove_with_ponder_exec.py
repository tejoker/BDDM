"""Execution and tactic-error helpers extracted from prove_with_ponder."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TACTIC_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)+)\b")
_TACTIC_KEYWORDS = {
    "by",
    "exact",
    "apply",
    "refine",
    "intro",
    "intros",
    "rw",
    "simp",
    "simpa",
    "at",
    "with",
    "fun",
    "have",
    "show",
    "constructor",
    "cases",
    "induction",
    "where",
}
_NAME_VALIDATION_CACHE: dict[tuple[str, str], bool] = {}


def classify_lean_error(error_text: str) -> str:
    text = (error_text or "").lower()
    if any(k in text for k in ("unknown constant", "unknown identifier", "does not exist")):
        return "name-resolution"
    if any(k in text for k in ("type mismatch", "application type mismatch")):
        return "type-mismatch"
    if any(k in text for k in ("rewrite failed", "did not match", "simp made no progress")):
        return "rewrite-mismatch"
    if any(k in text for k in ("unsolved goals", "goals remaining", "tactic failed")):
        return "incomplete-progress"
    if any(k in text for k in ("timeout", "maximum recursion depth", "max heartbeats")):
        return "resource-timeout"
    return "generic"


def repair_hint_for_error_class(error_class: str) -> str:
    if error_class == "name-resolution":
        return (
            "Repair strategy[name-resolution]: use only verified theorem names from retrieved premises; "
            "avoid invented dotted names."
        )
    if error_class == "type-mismatch":
        return (
            "Repair strategy[type-mismatch]: introduce hypotheses (`intro`/`rintro`) and avoid over-specialized lemmas."
        )
    if error_class == "rewrite-mismatch":
        return (
            "Repair strategy[rewrite-mismatch]: avoid `rw` unless LHS appears literally; prefer `simp`, `simpa`, `nth_rewrite`."
        )
    if error_class == "incomplete-progress":
        return (
            "Repair strategy[incomplete-progress]: apply decomposition tactics first (`constructor`, `cases`, `have`, `linarith`)."
        )
    if error_class == "resource-timeout":
        return (
            "Repair strategy[resource-timeout]: choose shorter local tactics; avoid expensive global search tactics."
        )
    return "Repair strategy[generic]: choose a different tactic family than prior failures."


def _ensure_lake_on_path() -> None:
    """Best-effort PATH fix for non-interactive shells missing elan bin dir."""
    if shutil.which("lake") is not None:
        return

    candidates = [
        str(Path.home() / ".elan" / "bin"),
        str(Path.home() / ".local" / "bin"),
        "/usr/local/bin",
    ]
    current = os.environ.get("PATH", "")
    parts = current.split(":") if current else []

    changed = False
    for cand in candidates:
        if cand and cand not in parts and Path(cand).exists():
            parts.append(cand)
            changed = True

    if changed:
        os.environ["PATH"] = ":".join(parts)


def _is_tactic_state(value: Any) -> bool:
    return hasattr(value, "pp") and hasattr(value, "num_goals")


def _is_proof_finished(value: Any) -> bool:
    return type(value).__name__ == "ProofFinished"


def _is_lean_error(value: Any) -> bool:
    return hasattr(value, "error") and isinstance(getattr(value, "error"), str)


def _is_proof_given_up(value: Any) -> bool:
    return type(value).__name__ == "ProofGivenUp"


@dataclass
class StepRecord:
    step: int
    attempt: int
    tactic: str
    model_turns: int
    result: str
    detail: str = ""


def _split_draft_into_tactics(draft: str) -> list[str]:
    lines = [ln.rstrip() for ln in draft.splitlines()]
    tactics: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("--"):
            continue
        if stripped in {"by", "begin", "end"}:
            continue
        tactics.append(stripped)
    return tactics


def _execute_draft(
    *,
    dojo: Any,
    initial_state: Any,
    draft: str,
    round_idx: int,
) -> tuple[bool, Any, list[StepRecord], str]:
    """Execute draft tactics in sequence and return structured error feedback."""
    state = initial_state
    records: list[StepRecord] = []
    tactics = _split_draft_into_tactics(draft)

    if not tactics:
        msg = "Draft contains no executable tactics"
        records.append(
            StepRecord(
                step=round_idx,
                attempt=1,
                tactic="",
                model_turns=1,
                result="lean-error",
                detail=msg,
            )
        )
        return False, state, records, "line=1; message=empty draft"

    for idx, tactic in enumerate(tactics, start=1):
        outcome = dojo.run_tac(state, tactic)

        if _is_tactic_state(outcome):
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="state-advanced",
                    detail=f"goals={outcome.num_goals}",
                )
            )
            state = outcome
            continue

        if _is_proof_finished(outcome):
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="proof-finished",
                    detail=(outcome.message or ""),
                )
            )
            return True, state, records, "proof-finished"

        if _is_lean_error(outcome):
            err = str(getattr(outcome, "error", "")).strip()
            detail = f"line={idx}; message={err}"
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="lean-error",
                    detail=detail,
                )
            )
            return False, state, records, detail

        if _is_proof_given_up(outcome):
            detail = f"line={idx}; message=proof-given-up"
            records.append(
                StepRecord(
                    step=round_idx,
                    attempt=idx,
                    tactic=tactic,
                    model_turns=1,
                    result="proof-given-up",
                    detail=detail,
                )
            )
            return False, state, records, detail

        detail = f"line={idx}; message=unknown outcome {type(outcome).__name__}"
        records.append(
            StepRecord(
                step=round_idx,
                attempt=idx,
                tactic=tactic,
                model_turns=1,
                result="unknown-outcome",
                detail=detail,
            )
        )
        return False, state, records, detail

    return False, state, records, "line=end; message=draft exhausted without finishing proof"


def extract_tactic_theorem_names(tactic: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for name in _TACTIC_NAME_RE.findall(tactic):
        head = name.split(".", 1)[0].lower()
        if head in _TACTIC_KEYWORDS:
            continue
        if name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def validate_lean_name(name: str, project_root: Path) -> bool:
    """Return True if `#check <name>` succeeds via lake env lean."""
    key = (str(project_root.resolve()), name)
    if key in _NAME_VALIDATION_CACHE:
        return _NAME_VALIDATION_CACHE[key]

    import uuid
    tmp_path = project_root / "Desol" / f"_tmp_namecheck_{uuid.uuid4().hex[:8]}.lean"
    lean_src = "import Desol.SDE.Basic\n\n#check @" + name + "\n"
    try:
        tmp_path.write_text(lean_src, encoding="utf-8")
        lake_bin = shutil.which("lake") or os.path.expanduser("~/.elan/bin/lake")
        proc = subprocess.run(
            [lake_bin, "env", "lean", str(tmp_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=12,
        )
        ok = proc.returncode == 0
    except Exception:
        ok = False
    finally:
        tmp_path.unlink(missing_ok=True)

    _NAME_VALIDATION_CACHE[key] = ok
    return ok


_ensure_lake_on_path()

