#!/usr/bin/env python3
"""Non-LLM deterministic micro-prover for lemma-factor aux lemmas.

Round-XII data showed the real bottleneck wasn't composition templates
(rendering already covers and/or/iff/exists/trans/symm well) — it was
aux closure rate: 192 aux proposed, 75 elaborated, only 4 closed (2%).

Every aux closure attempt in `sweep_lemma_factor_v2.py` is currently
a Leanstral call. Many aux are SHALLOW — `0 ≤ x^2`, `a = a`, simple
arithmetic, etc. The deterministic micro-prover catalog can close
these without burning Leanstral budget AND without consuming the
retry rounds budget.

This module exposes `try_deterministic_close_aux` which tries a small
catalog of canonical tactics, validating each via the isolated
patch-check. First-success-wins; on no-success returns
`(False, "", last_err)` so the caller falls through to Leanstral.

Standards-positive: the deterministic body still passes through the
SAME `_run_isolated_patch_check` validator as Leanstral candidates.
The audit gate fires the same way. The pre-pass is an optimization
(avoid spending Leanstral budget on trivial aux), not a trust layer.

Empirical expectation: with the lake-validation-cache warm, each
attempt is ~6 ms. 10 tactics × 0.006 s = 60 ms per aux. With 192
aux Round-XII would cost ~12 s extra for the full sweep — a
rounding error vs. the multi-minute Leanstral cost it replaces.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

# Ordered by yield expectation × speed. `trivial`/`rfl` are tried first
# because they're cheapest and the most-frequent closures for shallow aux.
# `aesop` and `exact?` go last because they're slowest.
#
# Catalog expanded after Round-XVI (commit f174a10): added paper-grade
# tactics shown to close real research lemmas — polyrith for polynomial
# arithmetic, linear_combination for equality goals with explicit
# coefficients, gcongr for monotonicity chains, tauto for propositional
# tautologies, constructor for inductive types, norm_cast for coercion
# bridges.
_DEFAULT_CATALOG: tuple[str, ...] = (
    "trivial",
    "rfl",
    "decide",
    "tauto",
    "norm_num",
    "norm_cast",
    "positivity",
    "linarith",
    "omega",
    "nlinarith",
    "gcongr",
    "simp_all",
    "field_simp",
    "ring",
    "ring_nf",
    "polyrith",
    "linear_combination 0",
    "constructor",
    "aesop",
    "exact?",
)


def try_deterministic_close_aux(
    *,
    lean_file: Path,
    aux_name: str,
    aux_signature: str,
    validator: Callable[[Path, str, str], tuple[bool, str]],
    catalog: Optional[tuple[str, ...]] = None,
) -> tuple[bool, str, str]:
    """Try each deterministic tactic in catalog order against ``aux_name``.

    Parameters
    ----------
    lean_file:
        Target file (passed through to the validator).
    aux_name:
        Short name of the aux theorem (passed through to the validator).
    aux_signature:
        Full declaration text up to (and including) `:= by`. The
        validator pulls this in as the isolated-baseline theorem
        declaration; here it is passed through unchanged.
    validator:
        ``(lean_file, theorem_name, proof_body) -> (ok, err_tail)``.
        Caller is responsible for wiring this to the same isolated
        patch-check the Leanstral path uses, so accept criteria match.
    catalog:
        Optional override for the tactic catalog (mainly for tests).

    Returns
    -------
    (closed, body, last_err)
        ``closed=True`` when some catalog body validates. ``body`` is
        the tactic text that won. ``last_err`` carries the final error
        on no-success so the Leanstral retry sees a non-empty signal.
    """
    if not aux_name.strip() or not aux_signature.strip():
        return False, "", "empty_aux_input"
    cat = catalog if catalog is not None else _DEFAULT_CATALOG
    last_err = ""
    for tactic in cat:
        body = tactic.strip()
        if not body:
            continue
        try:
            ok, err = validator(lean_file, aux_name, body)
        except Exception as exc:  # pragma: no cover - defensive
            last_err = f"validator_exception:{exc.__class__.__name__}"
            continue
        if ok:
            return True, body, ""
        last_err = (err or "").strip() or last_err
    return False, "", last_err


def catalog_summary() -> dict[str, Any]:
    """Lightweight introspection helper for telemetry."""
    return {
        "n_tactics": len(_DEFAULT_CATALOG),
        "tactics": list(_DEFAULT_CATALOG),
    }


def main() -> int:  # pragma: no cover
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show-catalog", action="store_true")
    args = parser.parse_args()
    if args.show_catalog:
        print(json.dumps(catalog_summary(), indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
