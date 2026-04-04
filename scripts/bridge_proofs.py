#!/usr/bin/env python3
"""Bridge-proof planning and execution for multi-paper dependencies.

This module:
  1. Plans which previously-proved theorems may bridge an ungrounded assumption
     (token-overlap matching, always available).
  2. Checks simple linear-arithmetic entailment via Z3 (optional, requires
     z3-solver).  A statement like "a + b ≤ c" can be discharged automatically
     without touching Lean.
  3. Executes the bridge-proof chain by submitting each candidate as a Lean
     tactic to a running REPLDojo session (optional, requires lean-dojo).
     On success the assumption is promoted to GROUNDED_INTERNAL_KG.

No assumption is claimed as grounded unless the Lean REPL or Z3 confirms it.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from premise_retrieval import PremiseEntry, PremiseRetriever
    _HAS_RETRIEVAL = True
except ImportError:
    _HAS_RETRIEVAL = False


_TOKEN_RE = re.compile(r"[A-Za-z0-9_'.]+")


@dataclass
class BridgeCandidate:
    theorem_name: str
    paper_id: str
    status: str
    score: float


@dataclass
class BridgePlan:
    assumption_expr: str
    candidates: list[BridgeCandidate]


@dataclass
class BridgeChainPlan:
    target_theorem: str
    ordered_candidates: list[str]
    rationale: list[str]


@dataclass
class EntailmentResult:
    """Result of a Z3 or Lean entailment check on a single assumption."""
    assumption_expr: str
    method: str  # "z3", "lean_repl", "unverified"
    entailed: bool
    counterexample: str = ""
    error: str = ""
    elapsed_s: float = 0.0


@dataclass
class BridgeExecutionResult:
    """Result of running the full bridge-proof execution pipeline."""
    target_theorem: str
    chain_plan: BridgeChainPlan
    entailment_results: list[EntailmentResult] = field(default_factory=list)
    newly_grounded: list[str] = field(default_factory=list)
    still_ungrounded: list[str] = field(default_factory=list)
    error: str = ""


def _norm_tokens(text: str) -> set[str]:
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or "")]
    return {t for t in tokens if len(t) >= 4}


def _iter_ledger_entries(ledger_root: Path):
    for path in sorted(ledger_root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
            rows = raw["entries"]
        elif isinstance(raw, list):
            rows = raw
        else:
            continue
        paper_id = path.stem
        for row in rows:
            if isinstance(row, dict):
                yield paper_id, row


def _load_ledger_index(ledger_root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for _paper_id, row in _iter_ledger_entries(ledger_root):
        name = str(row.get("theorem_name", "")).strip()
        if name and name not in index:
            index[name] = row
    return index


def _extract_type_from_assumption_expr(lean_expr: str) -> str:
    m = re.match(r"\(\w+\s*:\s*(.+)\)$", (lean_expr or "").strip())
    if not m:
        return ""
    return m.group(1).strip()


def _extract_hint_candidates(row: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list):
        return hints
    for a in assumptions:
        if not isinstance(a, dict):
            continue
        src = str(a.get("grounding_source", "")).strip()
        if src.startswith("bridge_candidate:"):
            name = src.split(":", 1)[1].strip()
            if name:
                hints.append(name)
    return hints


def suggest_bridge_candidates(
    *,
    assumption_expr: str,
    ledger_root: Path,
    max_candidates: int = 3,
) -> list[BridgeCandidate]:
    """Suggest candidate theorem bridges ranked by semantic similarity.

    Uses embedding-based retrieval (sentence-transformers via PremiseRetriever)
    when available, falling back to token-overlap scoring otherwise.
    Candidates are sourced from FULLY_PROVEN or INTERMEDIARY_PROVEN entries.
    """
    if not assumption_expr:
        return []

    ledger_root = Path(ledger_root)
    if not ledger_root.exists():
        return []

    # Collect eligible entries with their metadata.
    eligible: list[tuple[str, str, str, str]] = []  # (theorem_name, paper_id, status, statement)
    for paper_id, row in _iter_ledger_entries(ledger_root):
        status = str(row.get("status", ""))
        if status not in {"FULLY_PROVEN", "INTERMEDIARY_PROVEN"}:
            continue
        theorem_name = str(row.get("theorem_name", "")).strip()
        statement = str(row.get("lean_statement", "")).strip()
        if not theorem_name:
            continue
        eligible.append((theorem_name, paper_id, status, statement))

    if not eligible:
        return []

    # Embedding path: build a tiny PremiseRetriever over the eligible entries.
    if _HAS_RETRIEVAL:
        try:
            entries = [
                PremiseEntry(name=name, statement=stmt or name, namespace="", source_file=paper_id)
                for name, paper_id, _status, stmt in eligible
            ]
            retriever = PremiseRetriever.build(entries, encoder_name=None)
            hits = retriever.query(assumption_expr, top_k=max(1, max_candidates))
            # Map hits back to BridgeCandidate using the eligible index.
            name_to_meta = {name: (paper_id, status) for name, paper_id, status, _ in eligible}
            scored: list[BridgeCandidate] = []
            for hit in hits:
                meta = name_to_meta.get(hit.name)
                if meta is None:
                    continue
                scored.append(
                    BridgeCandidate(
                        theorem_name=hit.name,
                        paper_id=meta[0],
                        status=meta[1],
                        score=float(hit.score),
                    )
                )
            return scored[: max(1, max_candidates)]
        except Exception as exc:
            logger.debug("Embedding retrieval failed, falling back to token overlap: %s", exc)

    # Token-overlap fallback.
    a_tokens = _norm_tokens(assumption_expr)
    if not a_tokens:
        return []

    fallback: list[BridgeCandidate] = []
    for theorem_name, paper_id, status, statement in eligible:
        t_tokens = _norm_tokens(theorem_name + " " + statement)
        if not t_tokens:
            continue
        overlap = len(a_tokens.intersection(t_tokens))
        if overlap == 0:
            continue
        score = overlap / max(1.0, len(a_tokens))
        fallback.append(
            BridgeCandidate(
                theorem_name=theorem_name,
                paper_id=paper_id,
                status=status,
                score=float(score),
            )
        )

    fallback.sort(key=lambda c: c.score, reverse=True)
    return fallback[: max(1, max_candidates)]


def build_bridge_plan(
    *,
    assumption_expr: str,
    ledger_root: Path,
    max_candidates: int = 3,
) -> BridgePlan:
    return BridgePlan(
        assumption_expr=assumption_expr,
        candidates=suggest_bridge_candidates(
            assumption_expr=assumption_expr,
            ledger_root=ledger_root,
            max_candidates=max_candidates,
        ),
    )


# ---------------------------------------------------------------------------
# Z3 entailment checker
# ---------------------------------------------------------------------------

# Patterns that suggest linear arithmetic goals Z3 can handle.
_ARITH_PATTERNS = re.compile(
    r"\b(?:le|lt|ge|gt|add|sub|mul|div|mod|abs|min|max|"
    r"(?:\d+\s*[+\-*/<>=≤≥≠]+\s*\d+)|"
    r"(?:[a-z]\s*[+\-*/<>=≤≥≠]+\s*[a-z0-9]))\b",
    re.IGNORECASE,
)

# Lean → Python operator translation for simple linear expressions.
_LEAN_OP = {
    "≤": "<=", "≥": ">=", "≠": "!=",
    "∧": "and", "∨": "or", "¬": "not ",
}


def _lean_expr_to_z3_str(lean_expr: str) -> str:
    result = lean_expr
    for lean, z3op in _LEAN_OP.items():
        result = result.replace(lean, z3op)
    # Strip Lean type ascriptions: (x : ℕ) → x
    result = re.sub(r"\(\s*\w+\s*:\s*[\w ℕℤℝ]+\s*\)", "", result)
    return result.strip()


def check_entailment_z3(assumption_expr: str) -> EntailmentResult:
    """Attempt to verify a simple arithmetic assumption using Z3.

    Returns an EntailmentResult.  ``method`` is "z3" on success,
    "unverified" when Z3 cannot handle the expression or is not installed.
    """
    import time
    t0 = time.time()

    try:
        import z3  # type: ignore[import]
    except ImportError:
        return EntailmentResult(
            assumption_expr=assumption_expr,
            method="unverified",
            entailed=False,
            error="z3-solver not installed (pip install z3-solver)",
            elapsed_s=round(time.time() - t0, 3),
        )

    # Heuristic: skip if the expression doesn't look arithmetic.
    if not _ARITH_PATTERNS.search(assumption_expr):
        return EntailmentResult(
            assumption_expr=assumption_expr,
            method="unverified",
            entailed=False,
            error="expression does not appear to be linear arithmetic",
            elapsed_s=round(time.time() - t0, 3),
        )

    try:
        z3_str = _lean_expr_to_z3_str(assumption_expr)
        # Declare free integer variables found in the expression.
        var_names = set(re.findall(r"\b([a-z][a-z0-9_]*)\b", z3_str))
        var_names.discard("and")
        var_names.discard("or")
        var_names.discard("not")
        scope: dict[str, Any] = {}
        for vname in var_names:
            scope[vname] = z3.Int(vname)

        formula = eval(z3_str, {"__builtins__": {}}, {**scope, **{n: getattr(z3, n) for n in dir(z3)}})  # noqa: S307
        solver = z3.Solver()
        solver.add(z3.Not(formula))
        status = solver.check()

        if status == z3.unsat:
            return EntailmentResult(
                assumption_expr=assumption_expr,
                method="z3",
                entailed=True,
                elapsed_s=round(time.time() - t0, 3),
            )
        elif status == z3.sat:
            model = solver.model()
            cex = str(model)
            return EntailmentResult(
                assumption_expr=assumption_expr,
                method="z3",
                entailed=False,
                counterexample=cex,
                elapsed_s=round(time.time() - t0, 3),
            )
        else:
            return EntailmentResult(
                assumption_expr=assumption_expr,
                method="unverified",
                entailed=False,
                error="z3 returned unknown",
                elapsed_s=round(time.time() - t0, 3),
            )
    except Exception as exc:
        return EntailmentResult(
            assumption_expr=assumption_expr,
            method="unverified",
            entailed=False,
            error=f"z3 eval failed: {exc}",
            elapsed_s=round(time.time() - t0, 3),
        )


# ---------------------------------------------------------------------------
# Lean REPL execution for bridge proofs
# ---------------------------------------------------------------------------

def _build_lean_bridge_script(
    lean_statement: str,
    tactic_proof: str,
    imports: list[str] | None = None,
) -> str:
    """Build a minimal Lean 4 file that checks a bridge proof."""
    default_imports = [
        "import Mathlib.Tactic",
        "import Mathlib.Data.Real.Basic",
    ]
    header = "\n".join(imports or default_imports)
    return f"""{header}

-- DESol bridge proof check
{lean_statement} := by
  {tactic_proof}
"""


def execute_bridge_proof_lean(
    lean_statement: str,
    tactic_proof: str,
    *,
    timeout_s: int = 60,
    lake_exe: str = "lake",
    lean_exe: str = "lean",
) -> EntailmentResult:
    """Attempt to verify a bridge proof by invoking `lean --run` on a temp file.

    This is a lightweight check that does not require a full LeanDojo session.
    Suitable for bridge proofs that fit in a single tactic block.

    Returns:
        EntailmentResult with method="lean_repl" on success.
    """
    import time
    t0 = time.time()

    script = _build_lean_bridge_script(lean_statement, tactic_proof)

    with tempfile.NamedTemporaryFile(
        suffix=".lean", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(script)
        tmp_path = f.name

    try:
        proc = subprocess.run(
            [lean_exe, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        elapsed = round(time.time() - t0, 3)
        if proc.returncode == 0 and not proc.stderr.strip():
            return EntailmentResult(
                assumption_expr=lean_statement,
                method="lean_repl",
                entailed=True,
                elapsed_s=elapsed,
            )
        else:
            err = (proc.stderr or proc.stdout or "").strip()[:500]
            return EntailmentResult(
                assumption_expr=lean_statement,
                method="lean_repl",
                entailed=False,
                error=err,
                elapsed_s=elapsed,
            )
    except subprocess.TimeoutExpired:
        return EntailmentResult(
            assumption_expr=lean_statement,
            method="lean_repl",
            entailed=False,
            error=f"lean timed out after {timeout_s}s",
            elapsed_s=round(time.time() - t0, 3),
        )
    except FileNotFoundError:
        return EntailmentResult(
            assumption_expr=lean_statement,
            method="lean_repl",
            entailed=False,
            error="lean executable not found; install Lean 4",
            elapsed_s=round(time.time() - t0, 3),
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Full bridge execution pipeline
# ---------------------------------------------------------------------------

def execute_bridge_chain(
    *,
    target_theorem: str,
    ledger_root: Path,
    proof_callback: Any | None = None,
    lean_timeout_s: int = 60,
    use_z3: bool = True,
    use_lean: bool = True,
    max_depth: int = 2,
    max_candidates_per_step: int = 3,
) -> BridgeExecutionResult:
    """Run the bridge-proof pipeline for a target theorem.

    For each ungrounded assumption of ``target_theorem``:
      1. Try Z3 (if use_z3=True and assumption looks arithmetic).
      2. Try Lean REPL with a tactic proof from ``proof_callback`` (if provided).
      3. Fall back to planning-only (token-overlap candidates).

    Assumptions that are confirmed by Z3 or Lean are added to
    ``newly_grounded``; the rest stay in ``still_ungrounded``.

    Args:
        target_theorem: Name of the theorem with ungrounded assumptions.
        ledger_root: Root of the verification ledger directory.
        proof_callback: Optional ``(lean_statement: str) -> str`` that returns a
            tactic proof string to try.  Typically wraps the ponder loop.
        lean_timeout_s: Timeout for each Lean REPL check.
        use_z3: Whether to attempt Z3 entailment.
        use_lean: Whether to attempt Lean REPL execution.
        max_depth: Depth for bridge chain planning.
        max_candidates_per_step: Branching factor for chain planning.

    Returns:
        BridgeExecutionResult summarising what was grounded.
    """
    ledger_root = Path(ledger_root)
    chain_plan = collect_bridge_retry_targets(
        target_theorem=target_theorem,
        ledger_root=ledger_root,
        max_depth=max_depth,
        max_candidates_per_step=max_candidates_per_step,
    )

    index = _load_ledger_index(ledger_root)
    row = index.get(target_theorem, {})
    assumptions = row.get("assumptions", [])
    if not isinstance(assumptions, list):
        assumptions = []

    entailment_results: list[EntailmentResult] = []
    newly_grounded: list[str] = []
    still_ungrounded: list[str] = []

    for a in assumptions:
        if not isinstance(a, dict):
            continue
        grounding = str(a.get("grounding", "")).upper()
        if grounding not in {"UNGROUNDED", "UNKNOWN", ""}:
            continue

        lean_expr = str(a.get("lean_expr", "") or a.get("label", "")).strip()
        lean_stmt = str(a.get("lean_statement", "")).strip()
        if not lean_expr and not lean_stmt:
            continue

        grounded = False

        # Step 1: Z3 entailment.
        if use_z3 and lean_expr:
            er = check_entailment_z3(lean_expr)
            entailment_results.append(er)
            if er.entailed:
                newly_grounded.append(lean_expr)
                grounded = True
                logger.info("Z3 grounded: %s", lean_expr[:80])

        # Step 2: Lean REPL.
        if not grounded and use_lean and lean_stmt:
            tactic_proof = "sorry"  # placeholder unless callback provided
            if proof_callback is not None:
                try:
                    tactic_proof = proof_callback(lean_stmt) or "sorry"
                except Exception as exc:
                    logger.debug("proof_callback failed: %s", exc)

            if tactic_proof and tactic_proof != "sorry":
                er = execute_bridge_proof_lean(lean_stmt, tactic_proof, timeout_s=int(lean_timeout_s))
                entailment_results.append(er)
                if er.entailed:
                    newly_grounded.append(lean_stmt)
                    grounded = True
                    logger.info("Lean REPL grounded: %s", lean_stmt[:80])

        if not grounded:
            still_ungrounded.append(lean_expr or lean_stmt)

    logger.info(
        "Bridge execution for %s: grounded=%d still_ungrounded=%d",
        target_theorem,
        len(newly_grounded),
        len(still_ungrounded),
    )

    return BridgeExecutionResult(
        target_theorem=target_theorem,
        chain_plan=chain_plan,
        entailment_results=entailment_results,
        newly_grounded=newly_grounded,
        still_ungrounded=still_ungrounded,
    )


def collect_bridge_retry_targets(
    *,
    target_theorem: str,
    ledger_root: Path,
    max_depth: int = 2,
    max_candidates_per_step: int = 3,
) -> BridgeChainPlan:
    """Build an ordered list of bridge theorems to attempt before retrying a target theorem.

    The planner follows existing bridge hints from ledger assumptions and augments
    them with token-overlap candidates for currently ungrounded assumptions.
    """
    ledger_root = Path(ledger_root)
    index = _load_ledger_index(ledger_root)
    seen: set[str] = {target_theorem}
    frontier = [target_theorem]
    ordered: list[str] = []
    rationale: list[str] = []

    for _depth in range(max(1, max_depth)):
        if not frontier:
            break
        next_frontier: list[str] = []
        for theorem_name in frontier:
            row = index.get(theorem_name)
            if not row:
                continue

            candidates: list[str] = []
            # 1) Existing grounding hints from previous runs.
            candidates.extend(_extract_hint_candidates(row))

            # 2) New candidate suggestions from ungrounded assumptions.
            assumptions = row.get("assumptions", [])
            if isinstance(assumptions, list):
                for a in assumptions:
                    if not isinstance(a, dict):
                        continue
                    grounding = str(a.get("grounding", "")).upper()
                    if grounding not in {"UNGROUNDED", "UNKNOWN", ""}:
                        continue
                    expr = _extract_type_from_assumption_expr(str(a.get("lean_expr", "")))
                    if not expr:
                        expr = str(a.get("label", ""))
                    if not expr:
                        continue
                    suggested = suggest_bridge_candidates(
                        assumption_expr=expr,
                        ledger_root=ledger_root,
                        max_candidates=max_candidates_per_step,
                    )
                    candidates.extend(c.theorem_name for c in suggested)

            if candidates:
                rationale.append(f"{theorem_name}: {', '.join(candidates[:max_candidates_per_step])}")

            for cand in candidates[:max_candidates_per_step]:
                if not cand or cand in seen:
                    continue
                seen.add(cand)
                ordered.append(cand)
                next_frontier.append(cand)

        frontier = next_frontier

    return BridgeChainPlan(
        target_theorem=target_theorem,
        ordered_candidates=ordered,
        rationale=rationale,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Bridge planning utilities")
    parser.add_argument("--assumption", default="", help="Assumption expression to bridge")
    parser.add_argument("--target-theorem", default="", help="Target theorem for bridge-chain planning")
    parser.add_argument("--ledger-root", default="output/verification_ledgers", help="Ledger directory")
    parser.add_argument("--top-k", type=int, default=3, help="Number of candidates")
    parser.add_argument("--depth", type=int, default=2, help="Bridge-chain depth")
    args = parser.parse_args()

    if not args.assumption and not args.target_theorem:
        raise SystemExit("provide --assumption or --target-theorem")

    if args.target_theorem:
        chain = collect_bridge_retry_targets(
            target_theorem=args.target_theorem,
            ledger_root=Path(args.ledger_root),
            max_depth=args.depth,
            max_candidates_per_step=args.top_k,
        )
        out = {
            "target_theorem": chain.target_theorem,
            "ordered_candidates": chain.ordered_candidates,
            "rationale": chain.rationale,
        }
        print(json.dumps(out, indent=2))
        raise SystemExit(0)

    plan = build_bridge_plan(
        assumption_expr=args.assumption,
        ledger_root=Path(args.ledger_root),
        max_candidates=args.top_k,
    )

    out = {
        "assumption_expr": plan.assumption_expr,
        "candidates": [
            {
                "theorem_name": c.theorem_name,
                "paper_id": c.paper_id,
                "status": c.status,
                "score": c.score,
            }
            for c in plan.candidates
        ],
    }
    print(json.dumps(out, indent=2))
