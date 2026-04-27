"""
Batch-mode drop-in replacement for LeanDojo's Dojo/TacticState interface.
Uses incremental `lake build` (~1.5s/call on a cached project).

Protocol:
  __enter__  : synthetic initial proof state from theorem signature (no Lean call)
  run_tac    : write modified file → lake build → parse output → restore file
               exit 0 + no sorry warning at decl line → ProofFinished
               exit 1 + "unsolved goals"              → TacticState
               exit 1 + other error                   → LeanError
"""

from __future__ import annotations

import hashlib
import json
import re
import os
import time
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union


# ── Mirror of lean_dojo types ─────────────────────────────────────────────────

@dataclass(frozen=True)
class TacticState:
    pp: str
    id: int = field(compare=False)
    message: Optional[str] = field(default=None, compare=False)

    @property
    def num_goals(self) -> int:
        return self.pp.count("⊢")


@dataclass(frozen=True)
class ProofFinished:
    tactic_state_id: int
    message: Optional[str] = field(default=None, compare=False)


@dataclass(frozen=True)
class ProofGivenUp:
    pass


@dataclass(frozen=True)
class LeanError:
    error: str


@dataclass(frozen=True)
class StepTrace:
    step_idx: int
    tactic: str
    result_kind: str
    elapsed_ms: int
    before_pp: str = field(default="", compare=False)
    after_pp: str = field(default="", compare=False)
    error: str = field(default="", compare=False)


@dataclass(frozen=True)
class BatchExpansionRequest:
    project_root: str
    file_path: str
    theorem_name: str
    tactics: list[str]
    timeout: int = 300


@dataclass(frozen=True)
class SearchTreeCheckpoint:
    tree_id: str
    theorem_name: str
    created_at_unix: int
    frontier: list[dict[str, Any]]
    explored: list[dict[str, Any]]


# ── File helpers ──────────────────────────────────────────────────────────────

def _lean_target(file_path: Path) -> str:
    """Desol/SDE/Basic.lean -> Desol.SDE.Basic"""
    return str(file_path.with_suffix("")).replace("/", ".").replace("\\", ".")


def _find_decl_line(src: str, theorem_name: str) -> int:
    """Return 1-indexed line number of the theorem declaration."""
    for i, line in enumerate(src.splitlines(), 1):
        if re.match(rf"\s*(?:lemma|theorem)\s+{re.escape(theorem_name)}\b", line):
            return i
    raise ValueError(f"Theorem '{theorem_name}' not found in source")


def _replace_theorem_body(src: str, theorem_name: str, tactics: list[str]) -> str:
    """Replace the body of theorem_name with the given tactics."""
    lines = src.splitlines(keepends=True)

    theorem_start: int | None = None
    by_line_idx: int | None = None

    for i, line in enumerate(lines):
        if theorem_start is None:
            if re.match(rf"\s*(?:lemma|theorem)\s+{re.escape(theorem_name)}\b", line):
                theorem_start = i
        if theorem_start is not None:
            if re.search(r":=\s*by\s*$", line.rstrip()):
                by_line_idx = i
                break

    if theorem_start is None or by_line_idx is None:
        raise ValueError(f"Could not find ':= by' for theorem '{theorem_name}'")

    # Body = indented or blank lines after ':= by'
    body_end = by_line_idx + 1
    while body_end < len(lines):
        line = lines[body_end]
        if not line.rstrip() or line[0] in (" ", "\t"):
            body_end += 1
        else:
            break

    new_body: list[str] = []
    for tactic in tactics:
        for t_line in tactic.strip().splitlines():
            new_body.append(f"  {t_line}\n")
    if not new_body:
        new_body = ["  sorry\n"]

    return "".join(lines[: by_line_idx + 1] + new_body + lines[body_end:])


def _parse_param_groups(params_str: str) -> list[tuple[str, str]]:
    """
    Parse top-level parameter groups like '(x y : ℕ) {h : P} [inst : C]'.
    Returns [(name, type), ...], skipping anonymous/instance params.
    Handles nested parentheses in types.
    """
    results: list[tuple[str, str]] = []
    i, n = 0, len(params_str)
    while i < n:
        if params_str[i] in "([{":
            # Find matching close bracket, tracking nesting
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if params_str[j] in "([{":
                    depth += 1
                elif params_str[j] in ")]}":
                    depth -= 1
                j += 1
            content = params_str[i + 1 : j - 1].strip()
            # Find first top-level ':' inside this group
            d2, colon = 0, -1
            for k, c in enumerate(content):
                if c in "([{":
                    d2 += 1
                elif c in ")]}":
                    d2 -= 1
                elif c == ":" and d2 == 0 and (k == 0 or content[k - 1] != ":"):
                    # skip ':=' and '::'
                    if k + 1 < len(content) and content[k + 1] in ("=", ":"):
                        continue
                    colon = k
                    break
            if colon >= 0:
                names_raw = content[:colon].strip()
                typ = content[colon + 1 :].strip()
                # Skip anonymous/typeclass params (no plain identifier before colon)
                for token in names_raw.split():
                    if re.fullmatch(r"[\w'ℱℬ𝒢]+", token):
                        results.append((token, typ))
            i = j
        else:
            i += 1
    return results


def _synthetic_initial_state(src: str, theorem_name: str) -> str:
    """
    Build a Lean-style proof state from the theorem signature.
    Handles nested parentheses in types and ∀/∃ conclusions.
    """
    lines = src.splitlines()
    start: int | None = None
    sig_lines: list[str] = []

    for i, line in enumerate(lines):
        if start is None:
            if re.match(rf"\s*(?:lemma|theorem)\s+{re.escape(theorem_name)}\b", line):
                start = i
                sig_lines.append(line)
        elif start is not None:
            if re.search(r":=\s*by", line):
                pre = re.sub(r":=\s*by.*$", "", line).rstrip()
                if pre.strip():
                    sig_lines.append(pre)
                break
            sig_lines.append(line)

    if not sig_lines:
        return "⊢ ???"

    sig = " ".join(l.strip() for l in sig_lines).strip()
    sig = re.sub(rf"^(?:lemma|theorem)\s+{re.escape(theorem_name)}\s*", "", sig).strip()

    # Find the separator ':' = FIRST ':' at depth 0 that isn't ':=' or '::'.
    # Parameter colons like '(x : T)' are always at depth >= 1, so the first
    # depth-0 colon is the one separating params from conclusion.
    sep_colon = -1
    depth = 0
    for i, c in enumerate(sig):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ":" and depth == 0:
            nxt = sig[i + 1] if i + 1 < len(sig) else ""
            if nxt not in ("=", ":"):
                sep_colon = i
                break

    if sep_colon == -1:
        return f"⊢ {sig}"

    params_str = sig[:sep_colon].strip()
    conclusion = sig[sep_colon + 1 :].strip()

    pairs = _parse_param_groups(params_str)
    hyps = [f"{name} : {typ}" for name, typ in pairs]

    pp = "\n".join(hyps)
    if pp:
        pp += "\n"
    pp += f"⊢ {conclusion}"
    return pp


# ── Output parsing ────────────────────────────────────────────────────────────

def _extract_unsolved_goals(output: str) -> str | None:
    """
    Extract proof state from a 'unsolved goals' error.
    Lake format: 'error: <file>:<line>:<col>: unsolved goals\n<context>\n⊢ <goal>'
    """
    lines = output.splitlines()
    collecting = False
    goal_lines: list[str] = []

    for line in lines:
        if not collecting:
            if "unsolved goals" in line:
                collecting = True
                goal_lines = []
        else:
            # Stop at next Lake diagnostic line
            if re.match(r"(?:error|warning|info):", line):
                break
            goal_lines.append(line)

    if collecting and goal_lines:
        return "\n".join(goal_lines).strip()
    return None


def _extract_lean_error(output: str) -> str | None:
    """Extract the first meaningful Lean error message (not 'unsolved goals')."""
    for line in output.splitlines():
        m1 = re.match(r"error:\s+.+?:\d+:\d+:\s+(.+)$", line)
        m2 = re.match(r".+?:\d+:\d+:\s+error:\s+(.+)$", line)
        msg = None
        if m1:
            msg = m1.group(1).strip()
        elif m2:
            msg = m2.group(1).strip()
        if msg and "unsolved goals" not in msg.lower():
            return msg[:600]
    return None


def _ensure_repl_compat_dependency(project_root: Path) -> None:
    """Ensure path-based repl dependency exists for copied temp projects.

    Some integration tests copy `lakefile.toml` into a temporary project root
    without copying `third_party/repl_compat`. If the lakefile references this
    local path dependency, `lake build` fails immediately. We materialize a
    symlink (or fallback copy) from the canonical repository location.
    """
    try:
        lakefile = project_root / "lakefile.toml"
        if not lakefile.exists():
            return
        txt = lakefile.read_text(encoding="utf-8", errors="replace")
        if "third_party/repl_compat" not in txt:
            return
        target = project_root / "third_party" / "repl_compat"
        if target.exists():
            return
        canonical = Path(__file__).resolve().parent.parent / "third_party" / "repl_compat"
        if not canonical.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(canonical, target_is_directory=True)
        except Exception:
            import shutil as _shutil
            _shutil.copytree(canonical, target)
    except Exception:
        # Never block proof execution on dependency materialization best-effort.
        return


# ── Main class ────────────────────────────────────────────────────────────────

class REPLDojo:
    """
    Context manager for Lean proof search via incremental lake build.

    Usage::
        with REPLDojo(project_root, file_path, theorem_name, timeout=300) as (dojo, state):
            result = dojo.run_tac(state, "omega")
    """

    def __init__(
        self,
        project_root: Path,
        file_path: Path,
        theorem_name: str,
        timeout: int = 300,
        cache_path: Path | None = None,
    ) -> None:
        self.project_root = project_root
        self.file_path = file_path
        self.theorem_name = theorem_name
        self.timeout = timeout
        self._target = _lean_target(file_path)
        self._full_path = project_root / file_path
        self._original: str = ""
        self._decl_line: int = 0
        self._tactics: list[str] = []
        self._traces: list[StepTrace] = []
        self._cache_path = cache_path or (project_root / "output" / "dojo_tactic_cache.json")
        self._cache: dict[str, dict[str, Any]] = {}

    def _cache_key(self, tactics: list[str]) -> str:
        base = {
            "target": self._target,
            "theorem": self.theorem_name,
            "source_hash": hashlib.sha256(self._original.encode("utf-8")).hexdigest(),
            "tactics": tactics,
        }
        raw = json.dumps(base, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_cache(self) -> None:
        if self._cache:
            return
        try:
            if self._cache_path.exists():
                self._cache = json.loads(self._cache_path.read_text(encoding="utf-8"))
                if not isinstance(self._cache, dict):
                    self._cache = {}
        except Exception:
            self._cache = {}

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _result_from_cache(self, key: str) -> subprocess.CompletedProcess[str] | None:
        self._load_cache()
        row = self._cache.get(key)
        if not isinstance(row, dict):
            return None
        return subprocess.CompletedProcess(
            args=["lake", "build", self._target],
            returncode=int(row.get("returncode", 1)),
            stdout=str(row.get("stdout", "")),
            stderr=str(row.get("stderr", "")),
        )

    def _write_cache(self, key: str, result: subprocess.CompletedProcess[str]) -> None:
        self._load_cache()
        self._cache[key] = {
            "returncode": int(result.returncode),
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        self._save_cache()

    def _build(self, tactics: list[str]) -> subprocess.CompletedProcess:
        key = self._cache_key(tactics)
        cached = self._result_from_cache(key)
        if cached is not None:
            return cached
        modified = _replace_theorem_body(self._original, self.theorem_name, tactics)
        self._full_path.write_text(modified)
        try:
            _env = os.environ.copy()
            _elan = str(Path.home() / ".elan" / "bin")
            _env["PATH"] = _elan + ":" + _env.get("PATH", "")
            # Bootstrap project dependencies first, then compile the concrete file.
            # We use `lake update` (not `lake build`) so temp projects that don't
            # include the package's executable targets still work.
            bootstrap = subprocess.run(
                ["lake", "update"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=_env,
            )
            if bootstrap.returncode != 0:
                result = bootstrap
            else:
                result = subprocess.run(
                    ["lake", "env", "lean", str(self.file_path)],
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    env=_env,
                )
            self._write_cache(key, result)
            return result
        finally:
            self._full_path.write_text(self._original)

    def __enter__(self) -> tuple["REPLDojo", TacticState]:
        _ensure_repl_compat_dependency(self.project_root)
        self._original = self._full_path.read_text()
        self._tactics = []
        self._traces = []
        self._decl_line = _find_decl_line(self._original, self.theorem_name)
        pp = _synthetic_initial_state(self._original, self.theorem_name)
        return self, TacticState(pp=pp, id=0)

    def run_tac(
        self, state: TacticState, tactic: str
    ) -> Union[TacticState, ProofFinished, LeanError, ProofGivenUp]:
        t0 = time.monotonic()
        tactics = self._tactics[: state.id] + [tactic]
        result = self._build(tactics)
        output = result.stdout + result.stderr
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode == 0:
            # Proof finished if no sorry warning at the declaration line
            decl_sorry = re.search(
                rf":{self._decl_line}:\d+:.*declaration uses.*sorry", output
            )
            if decl_sorry is None:
                self._traces.append(
                    StepTrace(
                        step_idx=state.id,
                        tactic=tactic,
                        result_kind="proof_finished",
                        elapsed_ms=elapsed_ms,
                        before_pp=state.pp,
                    )
                )
                return ProofFinished(tactic_state_id=state.id)
            # Tactic ran but theorem still uses sorry (e.g. tactic called sorry)
            self._traces.append(
                StepTrace(
                    step_idx=state.id,
                    tactic=tactic,
                    result_kind="lean_error",
                    elapsed_ms=elapsed_ms,
                    before_pp=state.pp,
                    error="Tactic completed but theorem still uses sorry",
                )
            )
            return LeanError(error="Tactic completed but theorem still uses sorry")

        # Prefer concrete Lean diagnostics over generic unsolved-goal parsing.
        # Some malformed tactics can emit both; in that case this must be an error.
        err = _extract_lean_error(output)
        if err:
            self._traces.append(
                StepTrace(
                    step_idx=state.id,
                    tactic=tactic,
                    result_kind="lean_error",
                    elapsed_ms=elapsed_ms,
                    before_pp=state.pp,
                    error=err,
                )
            )
            return LeanError(error=err)

        goals = _extract_unsolved_goals(output)
        if goals is not None:
            if goals.strip() == state.pp.strip():
                msg = "Tactic produced no progress (goal state unchanged)"
                self._traces.append(
                    StepTrace(
                        step_idx=state.id,
                        tactic=tactic,
                        result_kind="lean_error",
                        elapsed_ms=elapsed_ms,
                        before_pp=state.pp,
                        error=msg,
                    )
                )
                return LeanError(error=msg)
            self._tactics = tactics
            self._traces.append(
                StepTrace(
                    step_idx=state.id,
                    tactic=tactic,
                    result_kind="state_advanced",
                    elapsed_ms=elapsed_ms,
                    before_pp=state.pp,
                    after_pp=goals,
                )
            )
            return TacticState(pp=goals, id=state.id + 1)

        err = output[:500]
        self._traces.append(
            StepTrace(
                step_idx=state.id,
                tactic=tactic,
                result_kind="lean_error",
                elapsed_ms=elapsed_ms,
                before_pp=state.pp,
                error=err,
            )
        )
        return LeanError(error=err)

    def get_step_traces(self) -> list[StepTrace]:
        """Structured trace of executed tactics for replay/debug pipelines."""
        return list(self._traces)

    def __exit__(self, *args: object) -> None:
        if self._original:
            self._full_path.write_text(self._original)


def replay_step_traces(initial_state: str, traces: list[StepTrace]) -> dict[str, Any]:
    """Deterministically replay stored step traces for debugging."""
    state = initial_state
    transcript: list[dict[str, Any]] = []
    for tr in traces:
        before = tr.before_pp or state
        after = tr.after_pp or before
        transcript.append(
            {
                "step_idx": tr.step_idx,
                "tactic": tr.tactic,
                "result_kind": tr.result_kind,
                "before_pp": before,
                "after_pp": after,
                "error": tr.error,
            }
        )
        state = after
    return {"final_state_pp": state, "steps": transcript}


def save_tree_checkpoint(path: Path, checkpoint: SearchTreeCheckpoint) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tree_id": checkpoint.tree_id,
        "theorem_name": checkpoint.theorem_name,
        "created_at_unix": checkpoint.created_at_unix,
        "frontier": checkpoint.frontier,
        "explored": checkpoint.explored,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_tree_checkpoint(path: Path) -> SearchTreeCheckpoint | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    return SearchTreeCheckpoint(
        tree_id=str(raw.get("tree_id", "")),
        theorem_name=str(raw.get("theorem_name", "")),
        created_at_unix=int(raw.get("created_at_unix", 0)),
        frontier=list(raw.get("frontier", [])),
        explored=list(raw.get("explored", [])),
    )


def _run_batch_request(req: BatchExpansionRequest) -> dict[str, Any]:
    project_root = Path(req.project_root)
    file_path = Path(req.file_path)
    with REPLDojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=req.theorem_name,
        timeout=int(req.timeout),
    ) as (dojo, state):
        cur: Union[TacticState, ProofFinished, LeanError, ProofGivenUp] = state
        for tac in req.tactics:
            if not isinstance(cur, TacticState):
                break
            cur = dojo.run_tac(cur, tac)
        kind = type(cur).__name__
        payload: dict[str, Any] = {"result_kind": kind, "traces": [t.__dict__ for t in dojo.get_step_traces()]}
        if isinstance(cur, TacticState):
            payload["state_pp"] = cur.pp
            payload["state_id"] = cur.id
        elif isinstance(cur, LeanError):
            payload["error"] = cur.error
        return payload


class REPLDojoWorkerPool:
    """Process-isolated worker pool for batched tactic expansions."""

    def __init__(self, max_workers: int = 2) -> None:
        self.max_workers = max(1, int(max_workers))

    def run_batch(self, requests: list[BatchExpansionRequest]) -> list[dict[str, Any]]:
        if not requests:
            return []
        out: list[dict[str, Any]] = [dict() for _ in requests]
        with ProcessPoolExecutor(max_workers=self.max_workers) as ex:
            fut_map = {ex.submit(_run_batch_request, req): i for i, req in enumerate(requests)}
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                try:
                    out[idx] = fut.result(timeout=max(1, requests[idx].timeout + 5))
                except Exception as exc:
                    out[idx] = {"result_kind": "LeanError", "error": str(exc), "traces": []}
        return out


def expand_tactics_batch(
    requests: list[BatchExpansionRequest],
    *,
    max_workers: int = 2,
) -> list[dict[str, Any]]:
    pool = REPLDojoWorkerPool(max_workers=max_workers)
    return pool.run_batch(requests)
