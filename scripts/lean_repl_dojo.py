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

import re
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union


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
        m = re.match(r"error:\s+.+?:\d+:\d+:\s+(.+)$", line)
        if m and "unsolved goals" not in line:
            return m.group(1).strip()[:600]
    return None


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

    def _build(self, tactics: list[str]) -> subprocess.CompletedProcess:
        modified = _replace_theorem_body(self._original, self.theorem_name, tactics)
        self._full_path.write_text(modified)
        try:
            _env = os.environ.copy()
            _elan = str(Path.home() / ".elan" / "bin")
            _env["PATH"] = _elan + ":" + _env.get("PATH", "")
            return subprocess.run(
                ["lake", "build", self._target],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=_env,
            )
        finally:
            self._full_path.write_text(self._original)

    def __enter__(self) -> tuple["REPLDojo", TacticState]:
        self._original = self._full_path.read_text()
        self._tactics = []
        self._decl_line = _find_decl_line(self._original, self.theorem_name)
        pp = _synthetic_initial_state(self._original, self.theorem_name)
        return self, TacticState(pp=pp, id=0)

    def run_tac(
        self, state: TacticState, tactic: str
    ) -> Union[TacticState, ProofFinished, LeanError, ProofGivenUp]:
        tactics = self._tactics[: state.id] + [tactic]
        result = self._build(tactics)
        output = result.stdout + result.stderr

        if result.returncode == 0:
            # Proof finished if no sorry warning at the declaration line
            decl_sorry = re.search(
                rf":{self._decl_line}:\d+:.*declaration uses.*sorry", output
            )
            if decl_sorry is None:
                return ProofFinished(tactic_state_id=state.id)
            # Tactic ran but theorem still uses sorry (e.g. tactic called sorry)
            return LeanError(error="Tactic completed but theorem still uses sorry")

        goals = _extract_unsolved_goals(output)
        if goals is not None:
            self._tactics = tactics
            return TacticState(pp=goals, id=state.id + 1)

        err = _extract_lean_error(output) or output[:500]
        return LeanError(error=err)

    def __exit__(self, *args: object) -> None:
        if self._original:
            self._full_path.write_text(self._original)
