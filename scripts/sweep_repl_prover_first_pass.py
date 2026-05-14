#!/usr/bin/env python3
"""Standalone sweep wrapper: REPL-prover first-pass + whole-proof fallback.

This is the wiring complement to `leanstral_repl_proof_generator.py` for
end-to-end use without modifying the in-flight `sweep_lemma_factor_v2.py`
(which is being edited concurrently by another agent). It reproduces the
sweep's row selection and patch/validate plumbing, but the FIRST pass on
each parent candidate goes through `prove_via_repl` BEFORE the whole-proof
generator.

Usage::

  python3 scripts/sweep_repl_prover_first_pass.py \\
      --paper 2304.09598 --max-candidates 4

When invoked WITHOUT `--use-repl-prover` (the default), the wrapper falls
through to the standard whole-proof first-pass — matching the existing
sweep's behaviour. With `--use-repl-prover`, the REPL prover is tried
first; if it returns None, the whole-proof generator runs as the fallback.

Aux lemmas (when factoring fires) always use the whole-proof generator —
REPL overhead is not worth it for small auxiliary signatures.

Standards-positive: same forbidden-token / lake-validation / integrity-
audit gates as the canonical sweep.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
    load_dotenv()
except Exception:
    pass

import leanstral_repl_proof_generator as repl_gen  # noqa: E402
import leanstral_whole_proof_generator as wp_gen  # noqa: E402
import sweep_leanstral_whole_proof as wp_sweep  # noqa: E402


def _build_client() -> Any | None:
    try:
        from mistralai import Mistral  # type: ignore[import-not-found]
    except Exception:
        try:
            from mistralai.client import Mistral  # type: ignore[import-not-found,no-redef]
        except Exception:
            return None
    key = os.environ.get("MISTRAL_API_KEY") or os.environ.get("LEANSTRAL_API_KEY")
    if not key:
        return None
    return Mistral(api_key=key)


def _candidate_rows(entries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for e in entries:
        status = str(e.get("status", "") or "")
        if status not in ("UNRESOLVED", "INTERMEDIARY_PROVEN"):
            continue
        gates = e.get("validation_gates") or {}
        if isinstance(gates, dict) and gates.get("lean_proof_closed") is True:
            continue
        proof_text = str(e.get("proof_text", "") or "").strip()
        if proof_text and proof_text != "sorry":
            continue
        if not (e.get("lean_statement") or "").strip():
            continue
        out.append(e)
    return out


def _attempt_row(
    *,
    paper_id: str,
    entry: dict,
    lean_file: Path,
    paper_theory_hint: str,
    file_text: str,
    client: Any,
    model: str,
    use_repl: bool,
    repl_timeout_s: int,
) -> dict[str, Any]:
    name = str(entry.get("theorem_name", "") or "")
    short = name.rsplit(".", 1)[-1] if name else ""
    lean_stmt = str(entry.get("lean_statement", "") or "")
    target = short if wp_sweep._flex_theorem_sorry_re(short).search(file_text) else name

    out: dict[str, Any] = {"theorem": name, "stages": [], "closed": False, "via": None}

    # REPL first pass.
    if use_repl and client is not None:
        try:
            repl_result = repl_gen.prove_via_repl(
                paper_id=paper_id, theorem_name=short or name,
                lean_statement=lean_stmt, paper_theory_hint=paper_theory_hint,
                paper_local_file=str(lean_file), client=client, model=model,
                repl_timeout_s=repl_timeout_s,
            )
        except Exception as exc:
            out["stages"].append({"stage": "repl_transport_error", "err": str(exc)[:200]})
            repl_result = None
        if repl_result is not None:
            body = repl_result["proof_body"]
            if wp_sweep._patch_proof_flex(lean_file, target, body):
                # Validate.
                from sweep_lemma_factor_v2 import _capture_baseline_errors, _lake_validate_aware
                baseline = _capture_baseline_errors(lean_file, timeout_s=repl_timeout_s * 2)
                ok, err = _lake_validate_aware(lean_file, target, baseline_errors=baseline, timeout_s=repl_timeout_s)
                if ok:
                    out["closed"] = True
                    out["via"] = "repl_prover"
                    out["rounds"] = repl_result.get("rounds", 0)
                    out["stages"].append({"stage": "repl_validated"})
                    wp_sweep._apply_accept_to_entry(
                        entry, proof_body=body,
                        reasoning=f"repl_prover:{repl_result.get('rounds',0)}rounds",
                        confidence=0.9, round_idx=1,
                    )
                    return out
                wp_sweep._revert_proof_flex(lean_file, target)
                out["stages"].append({"stage": "repl_lake_error", "err": (err or "")[-200:]})
            else:
                out["stages"].append({"stage": "repl_patch_failed"})
        else:
            out["stages"].append({"stage": "repl_returned_none"})

    # Whole-proof fallback.
    try:
        cand = wp_gen.generate_proof_candidate(
            paper_id=paper_id, theorem_name=short or name,
            lean_statement=lean_stmt, paper_theory_hint=paper_theory_hint,
            paper_local_file=file_text, error_tail="",
            client=client, model=model,
        )
    except Exception as exc:
        out["stages"].append({"stage": "wp_transport_error", "err": str(exc)[:200]})
        return out
    if cand is None:
        out["stages"].append({"stage": "wp_returned_none"})
        return out
    body = cand["proof_body"]
    if wp_sweep._patch_proof_flex(lean_file, target, body):
        from sweep_lemma_factor_v2 import _capture_baseline_errors, _lake_validate_aware
        baseline = _capture_baseline_errors(lean_file, timeout_s=repl_timeout_s * 2)
        ok, err = _lake_validate_aware(lean_file, target, baseline_errors=baseline, timeout_s=repl_timeout_s)
        if ok:
            out["closed"] = True
            out["via"] = "whole_proof"
            out["stages"].append({"stage": "wp_validated"})
            wp_sweep._apply_accept_to_entry(
                entry, proof_body=body,
                reasoning=cand.get("reasoning", ""),
                confidence=float(cand.get("confidence", 0.0)), round_idx=1,
            )
            return out
        wp_sweep._revert_proof_flex(lean_file, target)
        out["stages"].append({"stage": "wp_lake_error", "err": (err or "")[-200:]})
    else:
        out["stages"].append({"stage": "wp_patch_failed"})
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paper", action="append", default=[], required=True)
    p.add_argument("--max-candidates", type=int, default=4)
    p.add_argument("--use-repl-prover", action="store_true", default=False)
    p.add_argument("--repl-timeout-s", type=int, default=120)
    p.add_argument("--model", default=os.getenv("MISTRAL_MODEL", "labs-leanstral-2603"))
    p.add_argument("--summary", default="output/sweep_repl_first_pass_summary.json")
    args = p.parse_args()

    client = _build_client()
    if client is None:
        print("[error] no Mistral client", file=sys.stderr)
        return 2

    t0 = time.time()
    papers_out = []
    for pid in args.paper:
        led = PROJECT_ROOT / "output" / "verification_ledgers" / f"{pid}.json"
        lean_file = PROJECT_ROOT / "output" / f"{pid}.lean"
        if not (led.exists() and lean_file.exists()):
            papers_out.append({"paper_id": pid, "error": "missing_file"})
            continue
        data = json.loads(led.read_text())
        entries = data if isinstance(data, list) else data.get("entries", [])
        cands = _candidate_rows(entries)[: args.max_candidates]

        paper_theory = PROJECT_ROOT / "Desol" / "PaperTheory" / f"Paper_{pid.replace('.','_')}.lean"
        hint = wp_gen.extract_paper_theory_hint(paper_theory) if paper_theory.exists() else ""
        file_text = lean_file.read_text(encoding="utf-8")

        row_results = []
        for entry in cands:
            r = _attempt_row(
                paper_id=pid, entry=entry, lean_file=lean_file,
                paper_theory_hint=hint, file_text=file_text,
                client=client, model=args.model,
                use_repl=args.use_repl_prover, repl_timeout_s=args.repl_timeout_s,
            )
            row_results.append(r)
            file_text = lean_file.read_text(encoding="utf-8")
        papers_out.append({"paper_id": pid, "rows": row_results,
                           "closed": sum(1 for r in row_results if r["closed"])})

    elapsed = time.time() - t0
    summary = {
        "elapsed_seconds": round(elapsed, 1),
        "use_repl_prover": args.use_repl_prover,
        "papers": papers_out,
    }
    out_path = PROJECT_ROOT / args.summary
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"papers": [{"id": p.get("paper_id"), "closed": p.get("closed", 0)} for p in papers_out]}, indent=2))
    print(f"[summary] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
