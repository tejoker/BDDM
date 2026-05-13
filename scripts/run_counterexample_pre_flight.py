#!/usr/bin/env python3
"""Counterexample-search statement-fidelity pre-flight.

Motivation
----------
The 2604.21884 single-paper closure POC surfaced this as the highest-leverage
corpus-wide intervention. After exhausting MCTS + deterministic tactics on a
paper's UNRESOLVED rows, 4 of the 4 remaining rows turned out to be
**mathematically false as stated** because the LaTeX→Lean translator dropped a
binding hypothesis. Examples:

  * `thm_operator_main`: `∃ C, ∀ w : ℝ → ℝ, P(w) → ‖w‖ ≤ C` — uniform bound
    impossible since `w` ranges over all `ℝ → ℝ`; lost a constraint binding
    `w` to a specific subspace.
  * `ass_strichartz`: same shape — unbounded `‖X‖, ‖Y‖` over arbitrary `ℝ → ℝ`.
  * `prop_det_contraction`: RHS contains `(i : ℝ) ^ exp` which is 0 at `i = 0`
    — false unless `i ≥ 1` is added.
  * `cor_safe_range`: requires `∃ ε > 0` simultaneously satisfying two
    unrelated constraints; missing a `s2 < 4α − 3 − (3/2)θ` hypothesis.

These rows consumed full MCTS budget despite being un-provable — proof search
will *never* close a counterexample-able statement. Pre-flight gating these
rows to statement-repair (instead of proof search) is a corpus-wide win.

What this script does
---------------------
1. Reads `output/verification_ledgers/<id>.json`.
2. For each UNRESOLVED row with a non-empty `lean_statement`, scans the
   statement for free variables that appear in the *conclusion* (or its
   inner quantifiers) but are NOT bound by an explicit hypothesis. If
   every free variable is constrained, we skip the LLM call entirely
   and record `no_counterexample` (cheap exit — most rows hit this path).
3. For the remaining rows, calls Leanstral (`labs-leanstral-2603` by
   default) with a structured counterexample-probe prompt. Parses the
   response into `{verdict, witness, reasoning}`.
4. Records the verdict in the row under a NEW field `counterexample_preflight`.
   Does NOT mutate `status` or `gate_failures` by default. Optional flag
   `--write-gate-failure` appends `counterexample_found` to `gate_failures`
   when the verdict is `counterexample_found` — disabled by default while we
   audit Leanstral's accuracy.

Design notes
------------
- **Standards-positive only**: this script NEVER promotes a row. It only
  flags potential issues. Verdict is metadata.
- **Leanstral is the only LLM the pipeline calls** — we reuse the same
  Mistral client construction pattern as `scripts/run_auto_alignment_review.py`.
- The probe prompt explicitly instructs: "if every free variable is
  constrained by a hypothesis, no counterexample exists; reply
  `no_counterexample`." This is critical — over-rejection blocks
  legitimate theorems and is worse than no probe.
- Inconclusive / malformed Leanstral responses are recorded as
  `inconclusive`, NOT as `counterexample_found` (fail-safe direction).

Usage
-----
    python scripts/run_counterexample_pre_flight.py --paper-id 2604.21884
    python scripts/run_counterexample_pre_flight.py --paper-id 2604.21884 --limit 4 --dry-run
    python scripts/run_counterexample_pre_flight.py --paper-id 2604.21884 --write-gate-failure
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from mistralai import Mistral  # type: ignore[import-not-found]
except ImportError:
    try:
        from mistralai.client import Mistral  # type: ignore[no-redef,import-not-found]
    except ImportError:  # pragma: no cover - exercised when SDK missing
        Mistral = None  # type: ignore[assignment,misc]

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional in tests
    pass


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
PREFLIGHT_FIELD = "counterexample_preflight"
# v1.1: two-stage prompt with quantifier-semantics framing + Stage-B
# `blocking_hypothesis` enforcement (auto-promotes unjustified `safe`
# verdicts to `counterexample_found`). Smoke test on 2604.21884:
# recall 3/4 on false-as-stated UR rows (vs 1/4 with v1), FP rate 0/4
# on FULLY_PROVEN control rows.
SCHEMA_VERSION = "counterexample_preflight.v1.1"

_VALID_VERDICTS = {"counterexample_found", "no_counterexample", "inconclusive"}


# ---------------------------------------------------------------------------
# Free-variable analysis
# ---------------------------------------------------------------------------

# A "binder" is anything of the shape `(name1 name2 ... : Type)` or
# `{name1 ... : Type}` between `theorem <name>` and the `:` that opens the
# claim body (or a `:= by`).
_BINDER_RE = re.compile(
    r"[\(\{\[]\s*([A-Za-z_][A-Za-z_0-9']*(?:\s+[A-Za-z_][A-Za-z_0-9']*)*)\s*:",
)
# Identifier tokens that look like variables (lowercase / Greek prefixes).
_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z_0-9']*)\b")

_LEAN_KEYWORDS: frozenset[str] = frozenset({
    "theorem", "lemma", "def", "example", "by", "fun", "let", "if", "then",
    "else", "match", "with", "do", "have", "show", "from", "this", "in",
    "where", "rfl", "sorry", "trivial", "fun", "True", "False", "Type",
    "Prop", "Sort", "open", "namespace", "end", "section", "variable",
    "variables", "instance", "structure", "class", "inductive", "import",
})

# Identifiers that are types / Mathlib names (start with a capital, OR are
# well-known type aliases). Free-variable scanning must NOT flag these.
_BUILTIN_TYPES: frozenset[str] = frozenset({
    "ℝ", "ℕ", "ℤ", "ℚ", "ℂ",
    "Real", "Nat", "Int", "Rat", "Complex", "Bool", "String", "Char",
    "Filter", "Set", "Finset", "List", "Option", "Sum", "Prod", "Fin",
    "Function", "Group", "Ring", "Field", "Module",
    "NNReal", "ENNReal", "EReal",
})

# Tactic / library identifiers commonly appearing in statements.
_LIBRARY_IDENTS: frozenset[str] = frozenset({
    "Tendsto", "atTop", "nhds", "MeasureTheory", "Filter",
    "And", "Or", "Not", "Iff", "Eq", "HEq",
    "rfl", "id", "fun", "Exists", "Forall",
    "max", "min", "abs", "sup", "inf",
    "Real", "Nat", "Int", "Rat", "Complex",
    "le", "lt", "ge", "gt", "ne", "eq",
})


def _strip_unicode_quantifiers(text: str) -> str:
    """Replace ∀ x : T, ... and ∃ x : T, ... binders with a marker that
    preserves the bound names. Used so quantifier-bound variables don't get
    flagged as free."""
    # We don't need to fully parse — just collect the names by regex below.
    return text


def _extract_theorem_head_and_body(lean_stmt: str) -> tuple[str, str]:
    """Split a Lean theorem signature into (head, body).

    `head` covers `theorem name (...binders...) :`  (up to the colon that
    opens the claim body). `body` is the conclusion: everything after that
    colon and before `:= by` or end-of-string.

    Returns ("", lean_stmt) when no clear header is detected (safer for
    unusual shapes — every identifier is treated as potentially free).
    """
    if not lean_stmt or not lean_stmt.strip():
        return ("", "")
    # Trim trailing `:= by sorry` / `:= by` clauses — they aren't part of
    # the claim body and contain no free variables of interest.
    cleaned = re.sub(r":=\s*by\b.*$", "", lean_stmt, flags=re.DOTALL).strip()
    # Find the body-opening `:` (the FIRST `:` at top level that is followed
    # by a newline OR by the first occurrence of `∃`/`∀` — heuristic but
    # robust on the ledger's actual shapes).
    # Strategy: walk character-by-character, tracking paren depth; the
    # body-opener is the first `:` at depth 0 AFTER we have seen at least
    # one binder closer ()/]) — OR the only `:` at depth 0 if no binders.
    depth = 0
    seen_close = False
    body_open = -1
    in_string = False
    for i, ch in enumerate(cleaned):
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                seen_close = True
        elif ch == ":" and depth == 0:
            # Skip `theorem name :` when name is right before the colon and
            # we haven't seen a binder — that's a header without binders.
            if seen_close or _no_binders_only_header(cleaned[:i]):
                body_open = i
                break
    if body_open < 0:
        return ("", cleaned)
    head = cleaned[:body_open]
    body = cleaned[body_open + 1:].strip()
    return (head, body)


def _no_binders_only_header(prefix: str) -> bool:
    """Return True when `prefix` looks like `theorem name` (no binder group),
    so the first `:` is the body-opener."""
    return bool(re.match(r"\s*theorem\s+\S+\s*$", prefix.strip()))


def _binder_names(head: str) -> set[str]:
    """Extract names declared in `(name1 name2 ... : Type)` binders inside
    the theorem head. Multi-name binders like `(a b c : ℝ)` declare all of
    `a`, `b`, `c`."""
    out: set[str] = set()
    for m in _BINDER_RE.finditer(head):
        for name in m.group(1).split():
            if name and name not in _LEAN_KEYWORDS:
                out.add(name)
    return out


def _walk_top_level_binders(head: str) -> list[tuple[str, str, str]]:
    """Walk the theorem header and return one (open, body, close) tuple per
    top-level `(...)` / `{...}` / `[...]` binder.

    The body may contain NESTED parentheses (e.g. `(halpha : (3:ℝ)/4 < alpha)`).
    A regex with `[^()]*?` cannot match these — we need a paren-depth walker.
    """
    out: list[tuple[str, str, str]] = []
    n = len(head)
    i = 0
    while i < n:
        ch = head[i]
        if ch in "([{":
            opener = ch
            depth = 1
            start = i + 1
            j = i + 1
            in_string = False
            while j < n:
                c = head[j]
                if c == '"':
                    in_string = not in_string
                elif not in_string:
                    if c in "([{":
                        depth += 1
                    elif c in ")]}":
                        depth -= 1
                        if depth == 0:
                            break
                j += 1
            if depth == 0:
                body = head[start:j]
                closer = head[j]
                out.append((opener, body, closer))
                i = j + 1
                continue
        i += 1
    return out


def _hypothesis_binders(head: str) -> list[tuple[set[str], str]]:
    """Return (names, type) tuples for HYPOTHESIS binders in the header.

    A hypothesis binder is one whose Type is a Prop-ish statement. Heuristic:
    hypothesis-binder names typically start with `h` or `H` (paper-Lean
    convention) OR sit in a binder whose Type contains a relational
    operator (`<`, `≤`, `=`, `∈`, `∧`, `∨`, `→`, `↔`, `¬`).

    Uses a paren-depth walker so binder Types with nested parens (e.g.
    `(halpha : (3:ℝ)/4 < alpha)`) are matched correctly.
    """
    out: list[tuple[set[str], str]] = []
    for _opener, body, _closer in _walk_top_level_binders(head):
        # Split into `names : type` at the FIRST top-level `:` in the body.
        # Body itself can still contain nested colons (e.g. `(3:ℝ)` inside
        # a Type), so use a paren-depth walker again to find the splitter.
        depth = 0
        in_string = False
        split_at = -1
        for k, c in enumerate(body):
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == ":" and depth == 0:
                split_at = k
                break
        if split_at < 0:
            continue
        names_blob = body[:split_at]
        ty = body[split_at + 1:]
        ty_relational = bool(re.search(r"[<≤≥>]|=|∈|∧|∨|→|↔|¬", ty))
        first_name = (names_blob.strip().split() or [""])[0]
        looks_hyp = first_name.startswith(("h", "H"))
        if ty_relational or looks_hyp:
            names_set = {nm for nm in names_blob.split() if nm}
            out.append((names_set, ty))
    return out


def _hypothesis_binder_names(head: str) -> set[str]:
    """Just the names of the hypothesis binders themselves (e.g. `{hn, hT}`)."""
    out: set[str] = set()
    for names, _ty in _hypothesis_binders(head):
        out |= names
    return out


def _variables_constrained_by_hypothesis(head: str) -> set[str]:
    """Names of value-level variables (NOT the hypothesis names themselves)
    that appear inside the TYPE of some hypothesis binder. These variables
    are constrained by that hypothesis, so they don't need to be flagged.

    Example: `(n : ℕ) (hn : 0 < n)` — `n` appears in the type `0 < n` of
    the hypothesis `hn`, so `n` is constrained.
    """
    out: set[str] = set()
    for _hyp_names, ty in _hypothesis_binders(head):
        for m in _IDENT_RE.finditer(ty):
            nm = m.group(1)
            if nm and nm not in _LEAN_KEYWORDS and nm not in _BUILTIN_TYPES:
                out.add(nm)
    return out


def _quantifier_bound_names(body: str) -> set[str]:
    """Variables introduced inside the body via `∀` / `∃` binders. These
    are bound by the quantifier itself and are NOT free in the conclusion
    in the dangerous sense.

    Note: A `∃ C, ∀ w, P(w) → Q(C, w)` shape DOES quantify both `C` and `w`,
    but the danger pattern is `∃ C, ∀ w : T, Q(w)` where `w` ranges over
    an *unconstrained* `T`. We collect the bound names so they aren't
    misreported as "free in conclusion".
    """
    bound: set[str] = set()
    # Match `∀ x : T,` / `∃ x : T,` / `∀ x y z : T,` / `∃ x,` / `∀ (x : T),`.
    pat = re.compile(
        r"[∀∃]\s*\(?\s*([A-Za-z_][A-Za-z_0-9']*(?:\s+[A-Za-z_][A-Za-z_0-9']*)*)"
        r"(?:\s*:\s*[^,)\n]+)?\s*[,)]"
    )
    for m in pat.finditer(body):
        for nm in m.group(1).split():
            bound.add(nm)
    return bound


@dataclass
class FreeVariableReport:
    """Result of free-variable analysis on a Lean theorem statement."""

    head: str
    body: str
    binder_names: set[str] = field(default_factory=set)
    hypothesis_binder_names: set[str] = field(default_factory=set)
    quantifier_bound_in_body: set[str] = field(default_factory=set)
    # Variables that appear in the conclusion (or its quantifiers) but
    # are NOT constrained by any explicit hypothesis binder.
    unconstrained_in_body: set[str] = field(default_factory=set)

    @property
    def has_unconstrained_free_variable(self) -> bool:
        return bool(self.unconstrained_in_body)


def analyze_free_variables(lean_stmt: str) -> FreeVariableReport:
    """Identify free variables that appear in the conclusion but aren't
    bound by an explicit hypothesis binder.

    A binder like `(α : ℝ)` declares `α` as a free variable. If NO
    hypothesis like `(hα : 0 < α)` constrains it, then any conclusion
    involving `α` is potentially counterexample-able by picking `α := 0`,
    `α := -1`, etc.

    Returns a FreeVariableReport. `has_unconstrained_free_variable` is True
    when the LLM probe is warranted.
    """
    head, body = _extract_theorem_head_and_body(lean_stmt)
    if not head and not body:
        return FreeVariableReport(head="", body="")

    declared = _binder_names(head)
    hyps = _hypothesis_binder_names(head)
    constrained = _variables_constrained_by_hypothesis(head)
    qbound = _quantifier_bound_names(body)

    # Free vars that appear in the body but aren't (a) hypothesis-bound and
    # (b) aren't quantifier-bound inside the body itself.
    body_idents = {m.group(1) for m in _IDENT_RE.finditer(body)}
    body_idents -= _LEAN_KEYWORDS
    body_idents -= _BUILTIN_TYPES
    body_idents -= _LIBRARY_IDENTS

    # Declared value-level vars (exclude the hypothesis names themselves)
    # minus those constrained by appearing in some hypothesis type, minus
    # those quantifier-bound inside the body itself.
    declared_value_level = declared - hyps
    unconstrained = (declared_value_level & body_idents) - constrained - qbound

    # Also flag the `∀ w : (function type), P(w)` shape — quantifier-bound
    # variables whose Type is itself a function-space (`ℝ → ℝ`, `ℝ → ℂ`,
    # `ℕ → ℝ`, etc.) over unbounded domain. These are the canonical
    # counterexample shapes from the 2604.21884 POC.
    func_qbound = _function_space_quantifier_targets(body)
    unconstrained |= func_qbound

    # Also flag existentials with internal conjunctive constraints — e.g.
    # `∃ ε > 0, P(ε) ∧ Q(ε)` where `P` and `Q` may be jointly unsatisfiable
    # under some choice of the (otherwise constrained) free variables. The
    # static analyzer can't decide this; routing to the LLM is the right
    # call. We surface a synthetic marker `<existential_witness>` so the
    # caller knows the slow-path is warranted.
    existential_with_conjunction = _existential_with_conjunctive_body(body)
    if existential_with_conjunction:
        unconstrained.add(existential_with_conjunction)

    return FreeVariableReport(
        head=head,
        body=body,
        binder_names=declared,
        hypothesis_binder_names=hyps,
        quantifier_bound_in_body=qbound,
        unconstrained_in_body=unconstrained,
    )


def _existential_with_conjunctive_body(body: str) -> str:
    """If the conclusion is `∃ <var> ..., A ∧ B (∧ ...)`, return the bound
    variable name so the LLM can decide whether the conjuncts are jointly
    satisfiable. Returns "" when no such shape is present.

    This catches the `cor_safe_range`-style counterexample: a witness must
    satisfy multiple constraints simultaneously, and the static analyzer
    can't decide joint satisfiability.
    """
    m = re.search(
        r"∃\s*\(?\s*([A-Za-z_][A-Za-z_0-9']*)\b[^,]*,\s*(.+)",
        body,
        flags=re.DOTALL,
    )
    if not m:
        return ""
    bound_var = m.group(1)
    tail = m.group(2)
    # Heuristic: at least two `∧` joiners in the tail → multiple constraints
    # that must hold jointly. The LLM can decide joint satisfiability.
    if tail.count("∧") >= 2:
        return bound_var
    return ""


def _function_space_quantifier_targets(body: str) -> set[str]:
    """Detect `∀ x : ℝ → ℝ,` / `∀ X Y : ℝ → ℝ,` style quantifiers — the
    function-space ones where `x` ranges over arbitrary functions. These
    are the 2604.21884 POC shape (uniform bound on `‖w‖` impossible)."""
    out: set[str] = set()
    pat = re.compile(
        r"∀\s*\(?\s*([A-Za-z_][A-Za-z_0-9']*(?:\s+[A-Za-z_][A-Za-z_0-9']*)*)"
        r"\s*:\s*([^,)\n]+?)\s*[,)]"
    )
    for m in pat.finditer(body):
        names, ty = m.group(1), m.group(2)
        # Function-space (ℝ → ℝ, ℕ → ℝ, ℝ → ℂ, etc.).
        if "→" in ty:
            for nm in names.split():
                out.add(nm)
    return out


# ---------------------------------------------------------------------------
# Leanstral counterexample probe
# ---------------------------------------------------------------------------


_PROBE_SYSTEM = (
    "You are a counterexample-search judge for Lean 4 theorem statements.\n"
    "Your job: detect statements that are mathematically FALSE as written\n"
    "(typically because a hypothesis was dropped during LaTeX→Lean\n"
    "translation). You flag SUSPECTED issues as metadata — missing a\n"
    "false statement is worse than over-flagging, but spurious flags on\n"
    "legitimate theorems are also costly.\n\n"
    "CRITICAL — quantifier semantics. Read the theorem carefully BEFORE\n"
    "any verdict work. The outermost quantifier of the conclusion\n"
    "determines what a counterexample even MEANS:\n"
    "  * `∀ x, P(x)` is false iff THERE EXISTS x with ¬P(x). To\n"
    "    falsify, exhibit ONE bad x.\n"
    "  * `∃ x, P(x)` is false iff FOR ALL x, ¬P(x). To falsify, you\n"
    "    must argue that NO choice of x makes P(x) true. A single\n"
    "    bad x is NOT a counterexample to an existential.\n"
    "      Concretely: if the theorem is\n"
    "        `∃ alpha epsilon : ℝ, big_conjunction(alpha, epsilon, ...)`\n"
    "      then showing `alpha = 0` violates one conjunct is IRRELEVANT.\n"
    "      The theorem holds as long as SOME (alpha, epsilon) works.\n"
    "      Try a generous parameter range (alpha = 1, 10, 100; epsilon\n"
    "      tiny). If even one candidate satisfies the conjunction,\n"
    "      the theorem is TRUE → vote `no_counterexample` and put\n"
    "      the witnessing tuple in `blocking_hypothesis`.\n"
    "      Vote `counterexample_found` for an `∃ x, P(x)` shape ONLY\n"
    "      when you have a structural argument (e.g. a derived\n"
    "      inequality `0 < epsilon < c` where `c ≤ 0` for ALL\n"
    "      admissible parameters) that no witness can possibly exist.\n"
    "  * Nested: `∃ C, ∀ w, P(w) → bound(C, w)`. To falsify, show\n"
    "    that for every candidate C, some w (satisfying P) violates\n"
    "    the bound. This IS the canonical false shape when w ranges\n"
    "    over an unbounded function space.\n"
    "  * Definitional identities (`P ↔ Q` where both sides unfold to\n"
    "    the same algebraic form, or `f x = g x` where g is the\n"
    "    explicit form of f): almost always TRUE; vote\n"
    "    `no_counterexample` with `blocking_hypothesis: \"definitional\"`.\n\n"
    "Two-stage reasoning (you MUST do BOTH):\n\n"
    "STAGE A — Falsifiability check (do FIRST, before any witness work).\n"
    "  Assuming every hypothesis holds, identify the OUTERMOST quantifier\n"
    "  structure of the conclusion. Then ask: does the structure admit a\n"
    "  falsifier consistent with the quantifier semantics above? You do\n"
    "  NOT need a fully rigorous witness — a parametric family, limiting\n"
    "  sequence, or corner-case sketch all count.\n\n"
    "  Examples that ADMIT a counterexample:\n"
    "    * `∃ C, ∀ w : ℝ → ℝ, ‖w‖ ≤ C` (or with a Tendsto side\n"
    "      condition): for any candidate C, pick w := fun _ => C+1.\n"
    "      Side conditions like Tendsto on `(N:ℝ)^β * ‖w‖ → 0` are\n"
    "      often vacuously true for constants when β < 0. Check\n"
    "      whether a trivial w still satisfies them.\n"
    "    * `∀ i j : ℕ, i ≠ j → Bound i j ≤ C * (i:ℝ)^exp` with exp > 0\n"
    "      and Bound known positive: pick i = 0, j = 1 — RHS = 0,\n"
    "      LHS > 0, contradiction.\n"
    "    * `∃ ε > 0, P(ε) ∧ Q(ε)` where P forces ε ∈ (0, a) and Q\n"
    "      forces ε > b with b ≥ a under some allowed setting of\n"
    "      the OUTER free variables. Joint satisfiability fails for\n"
    "      that setting.\n\n"
    "  Examples that DO NOT admit a counterexample (you must NOT flag\n"
    "  these as `counterexample_found`):\n"
    "    * `∃ N C, ...` with α free — finding one α for which most\n"
    "      (N, C) pairs fail is NOT a counterexample. The theorem only\n"
    "      needs ONE (N, C) to work per α. Unless you can show NO\n"
    "      choice works, vote `no_counterexample`.\n"
    "    * `∃ alpha epsilon ..., conjunction` with NO outer free\n"
    "      variables and the conjunction known satisfiable for some\n"
    "      concrete tuple — vote `no_counterexample` and name the\n"
    "      satisfying tuple as `blocking_hypothesis: \"witness exists\"`.\n"
    "    * Statements of the form `P ↔ Q` where both sides are\n"
    "      algebraic identities that just need expansion — these are\n"
    "      almost always true; vote `no_counterexample`.\n\n"
    "STAGE B — Verdict assignment.\n"
    "  * counterexample_found: Stage A revealed a falsifier consistent\n"
    "    with quantifier semantics. State the falsifier (concrete or\n"
    "    parametric) in `reasoning`. `witness` may be null for\n"
    "    parametric families.\n"
    "  * no_counterexample: name a SPECIFIC hypothesis (e.g.\n"
    "    `halpha`), structural feature (e.g. `outermost ∃ admits\n"
    "    witness alpha=1, eps=0.1, ...`), or quantifier-semantics\n"
    "    argument (`∃ N C — only one (N, C) per α needed`). Put this\n"
    "    name/sketch in the `blocking_hypothesis` field. If you cannot\n"
    "    name one, prefer `counterexample_found`.\n"
    "  * inconclusive: paper-local opaque symbols AND no falsifier\n"
    "    independent of them. Do NOT use this just because building\n"
    "    a witness is hard.\n\n"
    "Anti-patterns to AVOID (each gave a wrong answer in past audits):\n"
    "  - `all free variables are constrained` → WRONG. Per-variable\n"
    "    hypotheses do not rule out joint-satisfiability failures.\n"
    "  - `w is universally quantified, bound is consistent with\n"
    "    hypotheses` → WRONG. A uniform bound over an unconstrained\n"
    "    function space is the canonical false shape.\n"
    "  - `the existential is satisfiable` → only valid when you\n"
    "    actually exhibit a satisfying tuple.\n"
    "  - Flagging `∃ N C, ...` because most (N, C) fail → WRONG.\n"
    "    The theorem only needs ONE witness.\n"
    "  - Flagging algebraic-identity ↔ statements → WRONG. Expand\n"
    "    both sides; they're usually equal by construction.\n\n"
    "Respond with JSON ONLY (no prose outside the JSON):\n"
    '{"verdict": "counterexample_found|no_counterexample|inconclusive",\n'
    ' "blocking_hypothesis": "<binder name, witness sketch, or null>",\n'
    ' "witness": {"<var_name>": "<concrete value or sketch>"} | null,\n'
    ' "reasoning": "<= 320 chars summarizing Stage A + Stage B"}\n'
)

_PROBE_USER_TEMPLATE = (
    "Theorem name: {name}\n\n"
    "Lean 4 statement:\n```lean\n{lean}\n```\n\n"
    "Free variables flagged as potentially unconstrained:\n  {flagged}\n\n"
    "Procedure (FOLLOW EXACTLY):\n"
    "  1. Identify the OUTERMOST quantifier of the conclusion. Write it\n"
    "     down explicitly before doing any analysis.\n"
    "  2. If the outermost quantifier is `∃` (one or more variables):\n"
    "       Try at least THREE candidate witnesses (small/large/\n"
    "       boundary). If ANY satisfies the body under SOME setting\n"
    "       of the outer free variables, vote `no_counterexample`\n"
    "       and put the witnessing tuple in `blocking_hypothesis`.\n"
    "       Vote `counterexample_found` only when you can derive a\n"
    "       chain of inequalities forcing the body to fail for EVERY\n"
    "       possible witness. Be especially careful with theorems\n"
    "       ending in `:= proof_term` (not `:= by sorry`) — those\n"
    "       HAVE a proof and are extremely unlikely to be false.\n"
    "  3. If the outermost is `∀` or `∃ C, ∀ w, ...`:\n"
    "       Try a constant function, a polynomial, and a corner case\n"
    "       (zero, large, small). If any violates the bound while\n"
    "       satisfying the side hypotheses, vote `counterexample_found`.\n"
    "  4. If the conclusion is an `iff` / equality between expressions\n"
    "     that look like a definition unfolding or simple algebraic\n"
    "     rearrangement, vote `no_counterexample` with\n"
    "     `blocking_hypothesis: \"definitional\"`.\n"
    "  5. STAGE B: every `no_counterexample` MUST name the specific\n"
    "     blocking hypothesis / witness tuple / structural reason.\n"
    "     If you can't, vote `counterexample_found`.\n\n"
    "Output JSON only."
)


@dataclass
class CounterexampleVerdict:
    """Result of a counterexample probe call."""

    verdict: str  # one of _VALID_VERDICTS
    witness: dict[str, Any] | None
    reasoning: str
    raw: str = ""
    flagged_vars: list[str] = field(default_factory=list)
    queried_llm: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "verdict": self.verdict,
            "witness": self.witness,
            "reasoning": self.reasoning,
            "flagged_free_vars": list(self.flagged_vars),
            "queried_llm": self.queried_llm,
            "queried_at": datetime.now(UTC).isoformat(),
        }


def _parse_probe_response(raw: str) -> dict[str, Any]:
    """Extract the JSON object from a Leanstral probe response. Robust to
    leading/trailing prose and markdown fences."""
    if not raw or not raw.strip():
        return {}
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # Greedy outermost { ... } capture.
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        return {}
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _call_leanstral(
    *,
    client: Any,
    model: str,
    user: str,
    max_tokens: int = 768,
) -> str:
    """Call Mistral chat. Returns the assistant text content."""
    response = client.chat.complete(
        model=model,
        messages=[
            {"role": "system", "content": _PROBE_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
    )
    text = ""
    if hasattr(response, "choices") and response.choices:
        msg = response.choices[0].message
        text = getattr(msg, "content", "") or ""
    return text.strip()


def probe_counterexample(
    *,
    theorem_name: str,
    lean_statement: str,
    client: Any,
    model: str,
) -> CounterexampleVerdict:
    """Run the full preflight on a single theorem.

    Fast-path: when no free variable in the statement is unconstrained,
    return `no_counterexample` WITHOUT calling the LLM. This keeps cost
    low on the typical row.

    Slow-path: when at least one unconstrained free variable is detected,
    call Leanstral with the probe prompt and parse the verdict.
    """
    if not lean_statement or not lean_statement.strip():
        return CounterexampleVerdict(
            verdict="inconclusive",
            witness=None,
            reasoning="empty lean_statement",
            flagged_vars=[],
            queried_llm=False,
        )

    report = analyze_free_variables(lean_statement)
    flagged = sorted(report.unconstrained_in_body)

    if not flagged:
        return CounterexampleVerdict(
            verdict="no_counterexample",
            witness=None,
            reasoning="all free variables hypothesis-constrained; LLM not consulted",
            flagged_vars=[],
            queried_llm=False,
        )

    if client is None:
        return CounterexampleVerdict(
            verdict="inconclusive",
            witness=None,
            reasoning="no LLM client available (dry-run)",
            flagged_vars=flagged,
            queried_llm=False,
        )

    user = _PROBE_USER_TEMPLATE.format(
        name=theorem_name or "<anonymous>",
        lean=lean_statement.strip()[:1500],
        flagged=", ".join(flagged) or "<none>",
    )
    try:
        raw = _call_leanstral(client=client, model=model, user=user)
    except Exception as exc:  # pragma: no cover - exercised in live use
        return CounterexampleVerdict(
            verdict="inconclusive",
            witness=None,
            reasoning=f"leanstral_error: {str(exc)[:120]}",
            raw="",
            flagged_vars=flagged,
            queried_llm=True,
        )

    parsed = _parse_probe_response(raw)
    if not parsed:
        return CounterexampleVerdict(
            verdict="inconclusive",
            witness=None,
            reasoning="malformed leanstral response (no JSON parsed)",
            raw=raw,
            flagged_vars=flagged,
            queried_llm=True,
        )

    verdict = str(parsed.get("verdict", "") or "").strip().lower()
    if verdict not in _VALID_VERDICTS:
        verdict = "inconclusive"
    witness = parsed.get("witness")
    if witness is not None and not isinstance(witness, dict):
        witness = None
    reasoning = str(parsed.get("reasoning", "") or "")[:320]
    blocking_hyp_raw = parsed.get("blocking_hypothesis")
    blocking_hyp = (
        str(blocking_hyp_raw).strip()
        if blocking_hyp_raw is not None
        else ""
    )
    # Stage-B enforcement: a `no_counterexample` verdict must name a
    # specific blocking hypothesis. When the model fails to do so, flip
    # to `counterexample_found` (matches the user-instructed protocol:
    # "If it can't name one, route to counterexample_found"). This is
    # the recall-raising lever — the previous prompt let the model
    # vote safe without justification.
    if verdict == "no_counterexample":
        normalized = blocking_hyp.lower()
        is_null_blocker = normalized in {"", "null", "none", "n/a", "na"}
        if is_null_blocker:
            verdict = "counterexample_found"
            reasoning = (
                "[promoted by Stage-B enforcement: model voted "
                "no_counterexample but named no blocking hypothesis] "
                + reasoning
            )[:320]
    return CounterexampleVerdict(
        verdict=verdict,
        witness=witness,
        reasoning=reasoning,
        raw=raw,
        flagged_vars=flagged,
        queried_llm=True,
    )


# ---------------------------------------------------------------------------
# Ledger orchestration
# ---------------------------------------------------------------------------


def _iter_unresolved(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in entries:
        if str(e.get("status", "") or "").upper() == "UNRESOLVED":
            out.append(e)
    return out


def _ensure_client(*, dry_run: bool) -> Any:
    if dry_run:
        return None
    if Mistral is None:
        raise RuntimeError(
            "mistralai package is not installed; cannot run counterexample preflight"
        )
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY is not set in the environment")
    return Mistral(api_key=api_key)


def run_preflight_on_paper(
    *,
    paper_id: str,
    project_root: Path = _PROJECT_ROOT,
    client: Any = None,
    model: str = DEFAULT_MODEL,
    limit: int | None = None,
    write_gate_failure: bool = False,
    dry_run: bool = False,
    write_ledger: bool = True,
) -> dict[str, Any]:
    """Run the counterexample preflight on every UNRESOLVED row in a
    paper's verification ledger.

    Args:
      paper_id: e.g. "2604.21884".
      project_root: path to the BDDM repo root.
      client: Mistral client (None → constructed from MISTRAL_API_KEY,
        or skipped when dry_run=True).
      model: Leanstral model ID.
      limit: max rows to probe (None = all UNRESOLVED).
      write_gate_failure: when True, append `counterexample_found` to
        the row's `gate_failures` list for verdicts of that name. Default
        False (metadata-only first round).
      dry_run: when True, do NOT call the LLM and do NOT write the ledger.
      write_ledger: when False, return the summary without writing.

    Returns a summary dict with counts + per-row decisions.
    """
    ledger_path = project_root / "output" / "verification_ledgers" / f"{paper_id}.json"
    if not ledger_path.exists():
        raise FileNotFoundError(f"No ledger at {ledger_path}")

    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]]
    is_list = isinstance(data, list)
    if is_list:
        entries = data
    else:
        entries = data.get("entries", [])

    ur_rows = _iter_unresolved(entries)
    if limit is not None:
        ur_rows = ur_rows[:limit]

    if client is None and not dry_run:
        client = _ensure_client(dry_run=False)

    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION + ".summary",
        "paper_id": paper_id,
        "ledger_path": str(ledger_path),
        "rows_probed": 0,
        "verdict_counts": {v: 0 for v in _VALID_VERDICTS},
        "llm_calls": 0,
        "fast_path_skips": 0,
        "decisions": [],
        "write_gate_failure_enabled": write_gate_failure,
        "dry_run": dry_run,
    }

    mutated = False
    for row in ur_rows:
        theorem_name = str(row.get("theorem_name", "") or "")
        lean_stmt = str(row.get("lean_statement", "") or "")
        verdict_obj = probe_counterexample(
            theorem_name=theorem_name,
            lean_statement=lean_stmt,
            client=client,
            model=model,
        )
        payload = verdict_obj.as_payload()
        summary["rows_probed"] += 1
        summary["verdict_counts"][verdict_obj.verdict] += 1
        if verdict_obj.queried_llm:
            summary["llm_calls"] += 1
        else:
            summary["fast_path_skips"] += 1
        summary["decisions"].append(
            {
                "theorem_name": theorem_name,
                "verdict": verdict_obj.verdict,
                "witness": verdict_obj.witness,
                "reasoning": verdict_obj.reasoning,
                "flagged_free_vars": list(verdict_obj.flagged_vars),
                "queried_llm": verdict_obj.queried_llm,
            }
        )

        if not dry_run:
            row[PREFLIGHT_FIELD] = payload
            mutated = True
            if (
                write_gate_failure
                and verdict_obj.verdict == "counterexample_found"
            ):
                existing = row.get("gate_failures")
                if not isinstance(existing, list):
                    existing = []
                if "counterexample_found" not in existing:
                    existing.append("counterexample_found")
                row["gate_failures"] = existing

    if write_ledger and mutated:
        payload = data if is_list else {**data, "entries": entries}
        ledger_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paper-id",
        action="append",
        required=True,
        help="arxiv id, e.g. 2604.21884. Repeat for batch.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Leanstral model ID")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap on number of UNRESOLVED rows probed per paper (safe rollout)",
    )
    parser.add_argument(
        "--write-gate-failure",
        action="store_true",
        help=(
            "When set, append 'counterexample_found' to the row's gate_failures "
            "for verdicts of that name. Default OFF — metadata-only first round."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip LLM calls and skip ledger writes; print the would-do plan.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_PROJECT_ROOT,
        help="BDDM repo root (default: inferred from script path)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional JSON summary output path",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        client = None
    else:
        client = _ensure_client(dry_run=False)

    summaries: list[dict[str, Any]] = []
    for pid in args.paper_id:
        try:
            summary = run_preflight_on_paper(
                paper_id=pid,
                project_root=args.project_root,
                client=client,
                model=args.model,
                limit=args.limit,
                write_gate_failure=args.write_gate_failure,
                dry_run=args.dry_run,
            )
        except FileNotFoundError as exc:
            print(f"[skip] {pid}: {exc}", file=sys.stderr)
            continue
        summaries.append(summary)
        ce = summary["verdict_counts"]["counterexample_found"]
        nc = summary["verdict_counts"]["no_counterexample"]
        ic = summary["verdict_counts"]["inconclusive"]
        print(
            f"{pid}: probed={summary['rows_probed']} "
            f"cex={ce} no_cex={nc} inconclusive={ic} "
            f"llm_calls={summary['llm_calls']} fast_path={summary['fast_path_skips']}"
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"papers": summaries}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
