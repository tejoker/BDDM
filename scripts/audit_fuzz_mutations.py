#!/usr/bin/env python3
"""Adversarial fuzzer for `audit_fully_proven_integrity`.

Goal: catch unknown-unknown bypass shapes. The mutation tests in
`tests/test_audit_integrity_mutations.py` pin known bypass classes (the
patterns we have already seen); this fuzzer drives the audit through
thousands of *randomly composed* bypasses (and legitimate proofs) and
asserts the contract on every iteration:

  - Synthetic rows whose ground-truth label is `bypass` (sorry-bodied
    .lean OR trivialized statement) MUST be demoted.
  - Synthetic rows whose ground-truth label is `legitimate` (real proof
    body, non-trivial statement, term-mode, or `__audited_core`
    replacement) MUST be preserved.

The fuzzer is reproducible: same seed → identical iteration sequence.
This is enforced by deriving all randomness from a single
`random.Random(seed)` instance per `fuzz_audit_against_random_bypasses`
call.

Standards-positive: any escape (false negative) surfaces an audit gap
that must be FIXED in the audit (extend `_body_is_sorry` or
`_is_trivialized_signature`), not papered over. Any
`unexpected_demotion` (false positive) is a gap in the fuzzer's
ground-truth label and is also reported so the calibration loop stays
honest.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

# Ensure scripts/ is on sys.path when invoked as a script.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from audit_fully_proven_integrity import (  # noqa: E402
    _PROOF_CLAIMING_STATUSES,
    audit_ledger_entries,
)


# ---------------------------------------------------------------------------
# Generator pools — kept module-level so the fuzzer's iteration sequence
# is a pure function of (seed, n_iterations) regardless of caller state.
# ---------------------------------------------------------------------------

# Proof texts the LLM/auto-LLM commonly write to the ledger without ever
# running them through Lean. None of them carry mathematical content;
# the audit must not trust any of them.
PROOF_TEXT_POOL: tuple[str, ...] = (
    "aesop",
    "simp_all",
    "omega",
    "linarith",
    "apply?",
    "decide",
    "tauto",
    "rfl",
    "aesop (add safe foo)",
    "simp",
    "norm_num",
    "trivial",
    "polyrith",
    "field_simp",
)

# Tactics that appear plausible but, when followed by `sorry`, are still
# placeholders. The audit's _body_is_sorry must catch every combination.
PLAUSIBLE_PREFIX_TACTICS: tuple[str, ...] = (
    "intro h",
    "intro h1 h2",
    "intros",
    "apply Iff.intro",
    "constructor",
    "refine ⟨?_, ?_⟩",
    "have h : True := trivial",
    "rw [eq_comm]",
    "simp only [eq_self_iff_true]",
)

# Bodies that are *legitimate* tactic proofs. These vary in shape and
# must NOT trigger demotion. The audit's contract is: a body without a
# standalone `sorry` token is preserved.
LEGITIMATE_BODIES: tuple[str, ...] = (
    "exact h",
    "intro h; exact h",
    "exact h.left",
    "decide",
    "linarith [Real.exp_pos]",
    "intro x; exact x",
    "rfl",
    "exact Iff.rfl",
    "have h : (1 : Nat) + 1 = 2 := by decide\n  exact h",
    "intro h; cases h with\n  | inl a => exact Or.inr a\n  | inr b => exact Or.inl b",
    "simp [Function.comp]",
    "norm_num",
    "ring",
    "intro x; ring",
)

# Non-trivial statements: real mathematical content with real
# quantifiers/binders. The fuzzer pairs these with legitimate bodies
# for the `legitimate` arm. Every entry here has been vetted against
# `_is_trivialized_signature` — none of them trigger the detector.
# Patterns we deliberately excluded (and why):
#   - `(p q : Prop) (hp : p) (hpq : p → q) : q`: modus ponens looks
#     real but the detector treats Prop-only binders + single-conjunct
#     Prop-target as trivialized. That is a known detector edge
#     case (the audit-side false positive is documented but the
#     fuzzer's contract is to match the audit, not second-guess it).
NON_TRIVIAL_STATEMENTS: tuple[str, ...] = (
    "theorem foo (f : ℝ → ℝ) : ∃ ε > 0, ∀ x, |f x| < ε * |x|",
    "theorem foo (n : ℕ) (h : n > 0) : n + 1 > 1",
    "theorem foo (a b : ℝ) (h : a < b) : a + 1 < b + 1",
    "theorem foo (s : Set ℝ) (h : s.Nonempty) : ∃ x, x ∈ s",
    "theorem foo (f g : ℕ → ℕ) (h : ∀ n, f n = g n) (n : ℕ) : f n = g n",
    "theorem foo (n : ℕ) : n + 0 = n",
    "theorem foo (a b c : ℕ) : a + b + c = a + (b + c)",
    "theorem foo (n : ℕ) : n ≤ n + 1",
    "theorem foo (a b : ℝ) (h : a ≤ b) : a - 1 ≤ b - 1",
    "theorem foo (xs : List ℕ) : xs.length ≥ 0",
    "theorem foo (n : ℕ) (h : n ≠ 0) : 0 < n",
)


# ---------------------------------------------------------------------------
# Body-shape mutators (the bypass arm)
# ---------------------------------------------------------------------------


def _gen_sorry_body(rng: random.Random) -> str:
    """Return a tactic body whose ground truth is sorry-bearing.

    The audit MUST demote any theorem with one of these bodies. We
    cycle through shapes: bare sorry, sorry-after-prefix, sorry-via-
    combinator, multi-line with hidden sorry, sorry-in-refine, and
    adversarial shapes NOT covered by `tests/test_audit_integrity_mutations.py`:
      - sorry after a long legitimate-looking computation block
      - sorry inside a focused dot bullet (`· sorry`)
      - sorry preceded by an inline comment on the same line
      - sorry on the same line as `by` (`:= by sorry`-equivalent splits)
      - sorry inside a `match` arm
    """
    shape = rng.choice([
        "bare",
        "prefix_then_sorry",
        "combinator_sorry",
        "midbody_sorry",
        "refine_sorry",
        "apply_iff_sorry",
        "intro_sorry",
        "have_sorry",
        "long_block_then_sorry",
        "bullet_sorry",
        "inline_comment_then_sorry",
        "match_sorry",
        "tab_indented_sorry",
        "trailing_whitespace_sorry",
        "uppercase_comment_sorry",
    ])
    if shape == "bare":
        return "  sorry\n"
    if shape == "prefix_then_sorry":
        prefix = rng.choice(PLAUSIBLE_PREFIX_TACTICS)
        return f"  {prefix}\n  sorry\n"
    if shape == "combinator_sorry":
        combinator = rng.choice(["<;>", ";"])
        prefix = rng.choice(("trivial", "skip", "constructor", "intro x"))
        return f"  {prefix} {combinator} sorry\n"
    if shape == "midbody_sorry":
        return (
            "  have h : True := trivial\n"
            "  -- TODO replace with real argument\n"
            "  sorry\n"
            "  exact h\n"
        )
    if shape == "refine_sorry":
        return "  refine ⟨?_, ?_⟩\n  · sorry\n  · sorry\n"
    if shape == "apply_iff_sorry":
        return "  apply Iff.intro <;> sorry\n"
    if shape == "intro_sorry":
        return "  intro h\n  sorry\n"
    if shape == "have_sorry":
        return "  have h := foo\n  sorry\n  exact h\n"
    if shape == "long_block_then_sorry":
        return (
            "  have h1 : True := trivial\n"
            "  have h2 : 1 + 1 = 2 := by decide\n"
            "  have h3 : (2 : Nat) ≤ 3 := by decide\n"
            "  have h4 : ∀ n : Nat, n + 0 = n := fun n => Nat.add_zero n\n"
            "  sorry\n"
        )
    if shape == "bullet_sorry":
        return "  constructor\n  · sorry\n  · trivial\n"
    if shape == "inline_comment_then_sorry":
        return "  sorry  -- placeholder until lemma X lands\n"
    if shape == "match_sorry":
        return (
            "  intro h\n"
            "  match h with\n"
            "  | .intro a b => sorry\n"
        )
    if shape == "tab_indented_sorry":
        # Tab-indented sorry — same logical content but different whitespace.
        return "\tsorry\n"
    if shape == "trailing_whitespace_sorry":
        return "  sorry   \n"
    if shape == "uppercase_comment_sorry":
        # Mixed-case identifier-looking comment followed by sorry.
        return "  -- SORRY: pending\n  sorry\n"
    # Defensive fallback — should be unreachable given the shape list.
    return "  sorry\n"


def _rand_ident(rng: random.Random) -> str:
    """Random Lean-identifier-shaped string."""
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(1, 4)))


def _gen_trivialized_statement(rng: random.Random) -> tuple[str, str]:
    """Return (signature_tail, lean_statement_field) for a vacuous claim.

    `signature_tail` is the part of the .lean theorem AFTER the theorem
    name, i.e. `[binders] : target`. The caller splices it in as
    `theorem <name><tail>` so there is no risk of stale template names
    leaking into the generated file. `lean_statement` is the ledger
    field — the bare target text.
    """
    shape = rng.choice([
        "exists_eq_const",
        "exists_eq_self",
        "refl_conj_2",
        "refl_conj_3",
        "prop_binder_conj",
        "prop_binder_multi_name",
        "exists_prop_iff",
        "exists_zero_le",
    ])
    if shape == "exists_eq_const":
        var = _rand_ident(rng)
        c = rng.randint(0, 99)
        target = f"∃ {var} : Nat, {var} = {c}"
        return f" : {target}", target
    if shape == "exists_eq_self":
        var = _rand_ident(rng)
        target = f"∃ {var} : ℝ, {var} = {var}"
        return f" : {target}", target
    if shape == "refl_conj_2":
        x = _rand_ident(rng).upper()
        y = _rand_ident(rng).upper()
        f = _rand_ident(rng)
        g = _rand_ident(rng)
        target = f"{f} {x} = {f} {x} ∧ {g} {y} = {g} {y}"
        return f" ({f} {g} : Nat → Nat) ({x} {y} : Nat) : {target}", target
    if shape == "refl_conj_3":
        a, b, c = _rand_ident(rng).upper(), _rand_ident(rng).upper(), _rand_ident(rng).upper()
        target = f"{a} = {a} ∧ {b} = {b} ∧ {c} = {c}"
        return f" ({a} {b} {c} : Nat) : {target}", target
    if shape == "prop_binder_conj":
        p, q = "P", "Q"
        target = f"{p} ∧ {q}"
        return f" ({p} : Prop) ({q} : Prop) : {target}", target
    if shape == "prop_binder_multi_name":
        names = ["P1", "P2", "P3"]
        target = " ∧ ".join(names)
        return f" ({' '.join(names)} : Prop) : {target}", target
    if shape == "exists_prop_iff":
        var = "X"
        rhs = "True"
        target = f"∃ {var} : Prop, {var} ↔ {rhs}"
        return f" : {target}", target
    if shape == "exists_zero_le":
        var = _rand_ident(rng).upper()
        target = f"∃ {var} : ℝ, 0 ≤ {var}"
        return f" : {target}", target
    # Defensive fallback.
    return " : True", "True"


# ---------------------------------------------------------------------------
# Iteration synthesis
# ---------------------------------------------------------------------------


def _stmt_template_to_tail_and_target(template: str) -> tuple[str, str]:
    """Translate a `theorem foo <tail>` template into (tail, target).

    `tail` is the text after the theorem name (binders + ` : target`);
    `target` is just the return-type text. We extract the *top-level*
    `:` (skipping colons nested inside binder parens) so that
    statements like `theorem foo (p q : Prop) (hp : p) : q` yield
    target `q`, not `Prop) (hp : p) : q`.
    """
    tail = template.removeprefix("theorem foo").strip()
    # The tail begins with optional binders then ` : target`. Find the
    # top-level `:` at depth 0.
    depth = 0
    colon_pos = -1
    for i, ch in enumerate(tail):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ":" and depth == 0:
            colon_pos = i
            break
    if colon_pos == -1:
        return f" {tail}", tail
    target = tail[colon_pos + 1 :].strip()
    return f" {tail}", target


def _gen_bypass_iteration(rng: random.Random, idx: int) -> dict[str, Any]:
    """Synthesize a single bypass iteration.

    Two sub-arms:
      A) sorry-bodied non-trivial statement: file body is sorry-bearing.
      B) trivialized statement: body may be a real `⟨...⟩` closure but
         the statement itself is vacuous.

    Either way, ground truth is `bypass` and the audit MUST demote.
    """
    arm = rng.choice(["sorry_body", "trivialized"])
    proof_text = rng.choice(PROOF_TEXT_POOL)
    name = f"fuzz_bypass_{idx}"

    if arm == "sorry_body":
        body = _gen_sorry_body(rng)
        sig_stmt = rng.choice(NON_TRIVIAL_STATEMENTS)
        tail, _target = _stmt_template_to_tail_and_target(sig_stmt)
        signature_line = f"theorem {name}{tail}"
        lean_src = f"{signature_line} := by\n{body}"
        # The ledger's `lean_statement` field stores the full signature
        # line (`theorem foo (binders) : target`) — that's what
        # _is_trivialized_signature expects to receive in production.
        return {
            "label": "bypass",
            "arm": "sorry_body",
            "name": name,
            "proof_text": proof_text,
            "lean_src": lean_src,
            "lean_statement": signature_line,
        }

    # Trivialized statement arm: tail already contains binders + target.
    tail, _target = _gen_trivialized_statement(rng)
    signature_line = f"theorem {name}{tail}"
    body = rng.choice([
        "  exact ⟨0, rfl⟩\n",
        "  exact ⟨1, rfl⟩\n",
        "  refine ⟨?_, ?_⟩ <;> rfl\n",
        "  exact ⟨rfl, rfl⟩\n",
        "  exact ⟨_, rfl⟩\n",
        "  decide\n",
    ])
    lean_src = f"{signature_line} := by\n{body}"
    return {
        "label": "bypass",
        "arm": "trivialized",
        "name": name,
        "proof_text": proof_text,
        "lean_src": lean_src,
        "lean_statement": signature_line,
    }


def _gen_legitimate_iteration(rng: random.Random, idx: int) -> dict[str, Any]:
    """Synthesize a legitimate row: non-trivial statement + real body.

    Sub-arms:
      A) tactic-mode with real body.
      B) term-mode (`:= rfl`, `:= Iff.rfl`).
      C) `__audited_core` row (the audit skips by design).
    """
    arm = rng.choices(["tactic_real", "term_mode", "audited_core"], weights=[3, 1, 1])[0]
    base_stmt = rng.choice(NON_TRIVIAL_STATEMENTS)
    name = f"fuzz_legit_{idx}"
    tail, _target = _stmt_template_to_tail_and_target(base_stmt)
    signature_line = f"theorem {name}{tail}"

    if arm == "tactic_real":
        body = rng.choice(LEGITIMATE_BODIES)
        lean_src = f"{signature_line} := by\n  {body}\n"
        return {
            "label": "legitimate",
            "arm": "tactic_real",
            "name": name,
            "proof_text": rng.choice(PROOF_TEXT_POOL),
            "lean_src": lean_src,
            "lean_statement": signature_line,
        }

    if arm == "term_mode":
        # Term-mode declaration; `_theorem_body_in_file` returns None and
        # the audit classifies as `term_mode_skipped`. We deliberately
        # use a non-trivial statement so the post-skip trivialization
        # check has nothing to flag (term-mode rows are never reached
        # by that check anyway since it lives after the body lookup).
        # Pick a non-trivial statement *and* a term body Lean could
        # actually accept for that statement — the audit doesn't run
        # lake so we only need the surface shape to be term-mode.
        term_body = rng.choice(["rfl", "Iff.rfl", "trivial"])
        lean_src = f"{signature_line} := {term_body}\n"
        return {
            "label": "legitimate",
            "arm": "term_mode",
            "name": name,
            "proof_text": rng.choice(PROOF_TEXT_POOL),
            "lean_src": lean_src,
            "lean_statement": signature_line,
        }

    # audited_core arm: file body may be sorry, but the row is marked as
    # an audited-core replacement; the audit must skip it regardless of
    # body / statement shape. We pair a sorry-bodied file with the
    # __audited_core suffix to make the bypass-attempt explicit: the
    # audit's exclusion list (suffix OR ledger_role) is the only thing
    # standing between this row and a demotion.
    audited_name = f"{name}__audited_core"
    audited_sig = f"theorem {audited_name}{tail}"
    lean_src = f"{audited_sig} := by\n  sorry\n"
    return {
        "label": "legitimate",
        "arm": "audited_core",
        "name": audited_name,
        "proof_text": rng.choice(PROOF_TEXT_POOL),
        "lean_src": lean_src,
        "lean_statement": audited_sig,
        "ledger_role": "audited_core_replacement",
    }


def _build_entry(it: dict[str, Any]) -> dict[str, Any]:
    """Translate an iteration record into a ledger entry the audit expects."""
    entry: dict[str, Any] = {
        "theorem_name": it["name"],
        "status": "FULLY_PROVEN",
        "proved": True,
        "step_verdict": "VERIFIED",
        "proof_text": it["proof_text"],
        "proof_method": "lean_verified",
        "lean_statement": it.get("lean_statement", ""),
        "validation_gates": {
            "lean_proof_closed": True,
            "step_verdict_verified": True,
        },
        "gate_failures": [],
    }
    if "ledger_role" in it:
        entry["ledger_role"] = it["ledger_role"]
    return entry


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fuzz_audit_against_random_bypasses(
    *, seed: int, n_iterations: int
) -> dict[str, Any]:
    """Run N random mutation attacks against `audit_fully_proven_integrity`.

    For each iteration:
      1. Generate a synthetic ledger entry with random proof_text +
         statement.
      2. Generate a synthetic .lean file body.
      3. Run the audit (`audit_ledger_entries` in broader-status mode).
      4. Verify the audit's verdict matches the iteration's ground-truth
         label: bypasses MUST be demoted, legitimate rows MUST be
         preserved.

    Reproducibility: the iteration sequence is a pure function of
    (seed, n_iterations). Each iteration draws from a single
    `random.Random` instance threaded forward.

    Returns:
        {
          "seed": int, "iterations": int,
          "caught": int,                # bypasses correctly demoted
          "preserved": int,             # legitimate rows correctly preserved
          "escaped": [...],             # bypasses the audit failed to catch
          "unexpected_demotions": [...] # legit rows the audit wrongly demoted
        }
    """
    rng = random.Random(seed)
    escaped: list[dict[str, Any]] = []
    unexpected: list[dict[str, Any]] = []
    caught = 0
    preserved = 0

    for idx in range(n_iterations):
        # Coin flip per iteration: bypass or legitimate. The flip is the
        # first draw from rng so the (seed, idx) → arm mapping is stable
        # across changes elsewhere in the generators.
        is_bypass = rng.random() < 0.5
        if is_bypass:
            it = _gen_bypass_iteration(rng, idx)
        else:
            it = _gen_legitimate_iteration(rng, idx)
        entry = _build_entry(it)
        result = audit_ledger_entries(
            [entry],
            paper_id=f"fuzz.{idx}",
            lean_src=it["lean_src"],
            statuses=_PROOF_CLAIMING_STATUSES,
        )
        demoted = result.demoted == 1

        if it["label"] == "bypass":
            if demoted:
                caught += 1
            else:
                escaped.append({
                    "idx": idx,
                    "arm": it["arm"],
                    "name": it["name"],
                    "proof_text": it["proof_text"],
                    "lean_statement": it.get("lean_statement", ""),
                    "lean_src": it["lean_src"],
                    "result": {
                        "fp_pre": result.fp_pre,
                        "validated_clean": result.validated_clean,
                        "term_mode_skipped": result.term_mode_skipped,
                        "not_found_skipped": result.not_found_skipped,
                        "audited_core_skipped": result.audited_core_skipped,
                    },
                })
        else:  # legitimate
            if demoted:
                unexpected.append({
                    "idx": idx,
                    "arm": it["arm"],
                    "name": it["name"],
                    "proof_text": it["proof_text"],
                    "lean_statement": it.get("lean_statement", ""),
                    "lean_src": it["lean_src"],
                    "demotion_reason": entry.get("audit_demotion", {}).get("reason", ""),
                })
            else:
                preserved += 1

    return {
        "seed": seed,
        "iterations": n_iterations,
        "caught": caught,
        "preserved": preserved,
        "escaped": escaped,
        "unexpected_demotions": unexpected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0xBDD)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument(
        "--max-report",
        type=int,
        default=10,
        help="Cap on escaped/unexpected entries printed in the summary",
    )
    args = parser.parse_args()

    out = fuzz_audit_against_random_bypasses(
        seed=args.seed, n_iterations=args.iterations
    )
    # Truncate the verbose lists for stdout; the full data is reachable
    # via the JSON return for in-process callers.
    summary = {
        "seed": out["seed"],
        "iterations": out["iterations"],
        "caught": out["caught"],
        "preserved": out["preserved"],
        "escaped_count": len(out["escaped"]),
        "unexpected_demotion_count": len(out["unexpected_demotions"]),
        "escaped_sample": out["escaped"][: args.max_report],
        "unexpected_demotions_sample": out["unexpected_demotions"][: args.max_report],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if not out["escaped"] and not out["unexpected_demotions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
