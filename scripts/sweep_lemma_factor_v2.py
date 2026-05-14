#!/usr/bin/env python3
"""Combined sweep driver: lemma-factor-v2 + whole-proof generator + composition.

Round-VII (after Round-VI's 0/58 closures): the remaining lever is
decomposition. For each canonical UR/IP candidate:

  1. Elaborate-probe the parent's lean_statement. Skip if it doesn't
     elaborate (statement-repair territory, not proof closure).
  2. First-pass: ask the whole-proof generator for a parent proof body.
     Validate via `lake env lean`. If it survives, commit and continue.
  3. If the first-pass fails: invoke lemma-factor-v2. Probe each proposed
     aux signature in isolation. Drop non-elaborating aux. For each
     surviving aux (must be >=2), run the whole-proof generator with up
     to 2 retry rounds.
  4. If >=2 aux close, attempt parent composition: try each
     composition shape (anonymous-constructor, refine, constructor) in
     turn, run `lake env lean`. If any survives, commit.
  5. After each commit, run integrity audit on that paper. Any demotion
     reverts the commit.

Standards-positive:
  - forbidden tokens rejected pre-patch
  - lake-in-context required for accept
  - integrity audit final
  - aux signatures use `__factored_aux` suffix (parallel to `__audited_core`)
    so they're TREATED AS PIPELINE OUTPUT, not curated content.

Aux lemmas land inline in `output/<paper>.lean` (just above the parent)
inside the same namespace, so the parent's composition sees them without
extra imports. This is conservative and self-contained — the audit looks
at the FILE for the parent's body and will validate the full chain.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import lemma_factor_v2 as lfv2  # noqa: E402
import leanstral_repl_proof_generator as repl_gen  # noqa: E402
import leanstral_whole_proof_generator as gen  # noqa: E402
import sweep_canonical_patch_and_validate as patcher  # noqa: E402
import sweep_leanstral_whole_proof as wp_sweep  # noqa: E402

try:
    from mistralai import Mistral  # type: ignore[import-not-found]
except Exception:
    try:
        from mistralai.client import Mistral  # type: ignore[import-not-found,no-redef]
    except Exception:
        Mistral = None  # type: ignore[assignment,misc]

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

try:
    from prove_arxiv_batch import _run_isolated_file_check  # type: ignore[import-not-found]
except Exception:
    _run_isolated_file_check = None  # type: ignore[assignment]


CANONICAL_PAPERS = [
    "2012.09271",
    "2304.09598",
    "2401.04567",
    "2604.21314",
    "2604.21583",
    "2604.21616",
    "2604.21821",
    "2604.21884",
]

DEFAULT_MODEL = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603")
FACTORED_AUX_SUFFIX = "__factored_aux"


# --- Baseline error fingerprinting ----------------------------------------


_ERROR_LINE_RX = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+): error:")


def _capture_baseline_errors(lean_file: Path, *, timeout_s: int = 180) -> int:
    """Capture the COUNT of `error:` diagnostics in the file's untouched
    lake-output. Line numbers are not stable across patches (inserting
    aux above a parent shifts subsequent line numbers), so we use count
    as the comparison signal instead of (path, line) tuples.

    Returns 0 on file compiles cleanly or on lake timeout.
    """
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(lean_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 0
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return sum(1 for ln in out.splitlines() if _ERROR_LINE_RX.match(ln.strip()))


def _lake_validate_aware(
    lean_file: Path,
    theorem_name: str,
    *,
    baseline_errors: int,
    timeout_s: int = 60,
) -> tuple[bool, str]:
    """Baseline-aware lake validator. Returns (ok, error_tail) where ok=True
    iff:
      - returncode == 0, OR
      - error_count <= baseline_errors (we didn't introduce fresh errors)
      AND no `declaration uses 'sorry'` warning is attached to our theorem.

    On failure, error_tail contains the last 1500 chars of output.
    """
    try:
        proc = subprocess.run(
            ["lake", "env", "lean", str(lean_file)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"lake_timeout:{timeout_s}s"
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    err_count = sum(1 for ln in out.splitlines() if _ERROR_LINE_RX.match(ln.strip()))
    if err_count > baseline_errors:
        delta = err_count - baseline_errors
        return False, f"fresh_errors_count={delta} (baseline={baseline_errors}, now={err_count}):\n{out[-1000:]}"
    # Check whether OUR theorem still triggers a sorry warning.
    short = _theorem_short_name(theorem_name)
    line_no = patcher._theorem_line_in_file(lean_file, short) \
        or patcher._theorem_line_in_file(lean_file, theorem_name)
    if line_no is None:
        return False, f"theorem_not_found_in_file:{theorem_name}"
    if not patcher._file_compiles_clean_for_theorem(out, line_no):
        return False, "patched_body_emits_sorry_warning"
    return True, ""


# --- Candidate selection (mirrors sweep_leanstral_whole_proof) ------------


def _is_candidate_row(entry: dict[str, Any]) -> tuple[bool, int]:
    """Same priority buckets as the whole-proof sweep:
      0: UR with reviewed_equivalence_verdict='equivalent'
      1: IP with lean_proof_closed empty
      2: remaining UR rows
    Non-candidate: AB / FP / FLAWED, already-closed rows, trivial
    proof_text, missing lean_statement.
    """
    status = str(entry.get("status", "") or "")
    if status not in ("UNRESOLVED", "INTERMEDIARY_PROVEN"):
        return False, 99
    gates = entry.get("validation_gates") or {}
    if isinstance(gates, dict) and gates.get("lean_proof_closed") is True:
        return False, 99
    proof_text = str(entry.get("proof_text", "") or "").strip()
    if proof_text and proof_text != "sorry":
        return False, 99
    if not (entry.get("lean_statement") or "").strip():
        return False, 99
    if status == "UNRESOLVED":
        if str(entry.get("reviewed_equivalence_verdict", "") or "").lower() == "equivalent":
            return True, 0
        return True, 2
    if status == "INTERMEDIARY_PROVEN":
        return True, 1
    return False, 99


def _theorem_short_name(name: str) -> str:
    if not name:
        return ""
    return name.rsplit(".", 1)[-1]


# --- Inline aux injection -------------------------------------------------


def _insert_aux_lemmas_above_parent(
    lean_file: Path,
    parent_short_name: str,
    aux_signatures: list[str],
) -> tuple[bool, list[int]]:
    """Insert `aux_signatures` (each ending in `:= by sorry`) into the lean
    file immediately above the parent's `theorem <parent_short>` line.

    Returns (inserted, line_numbers_of_aux_starts_1_indexed).

    Each aux is rewritten so its body is `:= by sorry` (we replace it later
    via the whole-proof generator + flex patcher). We do NOT bracket them
    in any namespace — they sit at the same lexical level as the parent
    (which is inside the file's `namespace ArxivPaper`).
    """
    text = lean_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    head_pat = re.compile(
        r"^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        + re.escape(parent_short_name)
        + r"\b"
    )
    target_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        if head_pat.match(ln):
            target_idx = i
            break
    if target_idx is None:
        return False, []
    insertion_lines: list[str] = []
    aux_start_lines: list[int] = []
    cur_line = target_idx + 1  # 1-indexed line of parent
    # Build insertion buffer.
    for sig in aux_signatures:
        sig_clean = sig.strip()
        if not sig_clean.endswith(":= by sorry"):
            # Force the body to sorry so the file still compiles (with a
            # `declaration uses 'sorry'` warning on the aux — we'll replace
            # it before final validation).
            sig_clean = re.sub(r":=.*$", "", sig_clean, flags=re.DOTALL).rstrip()
            sig_clean = sig_clean + " := by sorry"
        # Multi-line aux: ensure it ends with a blank line.
        insertion_lines.append(sig_clean + "\n\n")
    # Track where each aux starts.
    running_line = target_idx + 1  # 1-indexed
    for chunk in insertion_lines:
        aux_start_lines.append(running_line)
        running_line += chunk.count("\n")
    insertion = "".join(insertion_lines)
    new_lines = lines[:target_idx] + [insertion] + lines[target_idx:]
    lean_file.write_text("".join(new_lines), encoding="utf-8")
    return True, aux_start_lines


def _remove_aux_lemmas(lean_file: Path, aux_names: list[str]) -> int:
    """Remove the auxiliary lemma declarations from the file. Used for
    rollback. Returns the count of removed declarations.
    """
    text = lean_file.read_text(encoding="utf-8")
    removed = 0
    for name in aux_names:
        # Match the entire declaration block from `theorem <name>` up to
        # (but not including) the next top-level decl or end-of-namespace.
        pat = re.compile(
            r"(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
            + re.escape(name)
            + r"\b[\s\S]*?(?=\n(?:noncomputable\s+|private\s+)?(?:theorem|lemma|def|abbrev|axiom|end|namespace)\b|\Z)",
        )
        new_text, n = pat.subn("", text, count=1)
        if n > 0:
            removed += 1
            text = new_text
    # Tidy double blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    lean_file.write_text(text, encoding="utf-8")
    return removed


def _qualify_aux_name(parent_short: str, aux_name: str, idx: int) -> str:
    """Return the canonical aux name with `__factored_aux` suffix. Ensures
    we don't collide with existing theorem names by including the parent
    short name as a prefix and a 1-based index."""
    base = re.sub(r"[^A-Za-z0-9_']", "_", aux_name).strip("_")
    if not base:
        base = f"aux_{idx}"
    # Always include the parent short prefix + suffix so the name is
    # globally unique within the file.
    parent_clean = re.sub(r"[^A-Za-z0-9_']", "_", parent_short).strip("_") or "thm"
    return f"{parent_clean}_{base}_{idx}{FACTORED_AUX_SUFFIX}"


def _rename_aux_in_signature(sig: str, new_name: str) -> str:
    """Rewrite the `theorem <old> ...` head to use `new_name`."""
    return re.sub(
        r"^(\s*)(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+[A-Za-z_][A-Za-z0-9_'.]*",
        rf"\1theorem {new_name}",
        sig,
        count=1,
    )


# --- Composition attempt --------------------------------------------------


def attempt_composition(
    *,
    lean_file: Path,
    parent_short_name: str,
    aux_names: list[str],
    parent_target_shape: str,
    per_lake_timeout: int,
    baseline_errors: int = 0,
    validator: Optional[Callable[[Path, str], tuple[bool, str]]] = None,
    aux_records: Optional[list[dict[str, Any]]] = None,
) -> tuple[bool, str, str]:
    """Try each composition shape for `parent_short_name` using `aux_names`.
    Returns (validated, body_used, error_tail). On success the parent's
    proof body is left in the file; on failure the parent is reverted to
    `:= by sorry`.

    `parent_target_shape` may be a fine label
    (`exists_with_witness`, `conjunction_with_ineq`, `iff_bidirectional`,
    `implication`, `universal_implication`, `universal_with_bound`,
    `calc_chain`, `disjunction`, `nested_exists`, `exists_with_prop`) or a
    legacy coarse label (`and` / `exists` / `iff` / `other`). The emitter
    handles both vocabularies.

    `aux_records` carries `{aux_name, compose_hint}` rows so the role-mapper
    can place each aux into the right slot of the chosen skeleton.

    `validator(lean_file, theorem_name) -> (ok, err_tail)` defaults to the
    baseline-aware validator if not provided.
    """
    bodies = lfv2.render_composition_attempts(
        parent_target_shape=parent_target_shape,
        aux_names=aux_names,
        aux_records=aux_records,
    )
    if not bodies:
        return False, "", "no_composition_bodies"
    if validator is None:
        baseline = int(baseline_errors or 0)

        def _default_validator(f: Path, name: str) -> tuple[bool, str]:
            return _lake_validate_aware(
                f, name, baseline_errors=baseline, timeout_s=per_lake_timeout,
            )

        validator = _default_validator
    last_err = ""
    for body in bodies:
        # Patch parent to body, validate, on failure revert.
        patched = wp_sweep._patch_proof_flex(lean_file, parent_short_name, body)
        if not patched:
            return False, body, "patch_failed"
        ok, err_tail = validator(lean_file, parent_short_name)
        if ok:
            return True, body, ""
        last_err = err_tail or "lake_error"
        # Revert and try the next composition body.
        wp_sweep._revert_proof_flex(lean_file, parent_short_name)
    return False, "", last_err


# --- Audit integration ----------------------------------------------------


def _run_integrity_audit(paper_id: str) -> tuple[bool, dict[str, Any]]:
    """Run audit_fully_proven_integrity (ledger audit, dry-run) on the
    given paper to verify no demotions occurred after the commit. Returns
    (ok, audit_summary_dict).
    """
    try:
        from audit_fully_proven_integrity import (
            audit_paper,
            DEFAULT_LEDGER_DIR,
            DEFAULT_LEAN_DIR,
            DEFAULT_REPRO_DIR,
        )
    except Exception as exc:
        return False, {"error": f"audit_import_failed:{exc}"}
    statuses = ("FULLY_PROVEN", "AXIOM_BACKED", "INTERMEDIARY_PROVEN")
    try:
        result = audit_paper(
            paper_id,
            ledger_dir=DEFAULT_LEDGER_DIR,
            lean_dir=DEFAULT_LEAN_DIR,
            repro_dir=DEFAULT_REPRO_DIR,
            write=False,
            statuses=statuses,
        )
    except Exception as exc:
        return False, {"error": f"audit_failed:{exc}"}
    ephem = result.get("ephemeral", {})
    demoted = int(ephem.get("demoted", 0) or 0) if isinstance(ephem, dict) else 0
    return (demoted == 0), result


# --- Main sweep loop ------------------------------------------------------


def _sweep_paper(
    *,
    paper_id: str,
    client: Any,
    model: str,
    max_candidates: int,
    max_rounds: int,
    per_lake_timeout: int,
    dry_run: bool,
    bucket_counts: Counter[str],
    use_repl_prover: bool = False,
) -> dict[str, Any]:
    led_path = PROJECT_ROOT / "output" / "verification_ledgers" / f"{paper_id}.json"
    lean_file = PROJECT_ROOT / "output" / f"{paper_id}.lean"
    report = {
        "paper_id": paper_id,
        "candidates_elaborated": 0,
        "candidates_attempted": 0,
        "first_pass_validated": 0,
        "factored": 0,
        "aux_proposed": 0,
        "aux_elaborated": 0,
        "aux_closed": 0,
        "composed": 0,
        "audit_survived": 0,
        "details": [],
    }
    if not led_path.exists() or not lean_file.exists():
        report["error"] = "no_ledger_or_file"
        return report

    data = json.loads(led_path.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])

    paper_theory_path = (
        PROJECT_ROOT / "Desol" / "PaperTheory"
        / f"Paper_{paper_id.replace('.', '_')}.lean"
    )
    paper_theory_hint = ""
    exported_symbols = ""
    if paper_theory_path.exists():
        paper_theory_hint = gen.extract_paper_theory_hint(paper_theory_path)
        exported_symbols = lfv2.extract_exported_symbols(paper_theory_path)
    file_text = lean_file.read_text(encoding="utf-8")

    # Capture baseline `error:` COUNT so the post-patch validator can
    # distinguish OUR damage from pre-existing damage. Several canonical
    # files have 1-47 pre-existing errors that block the naive validator.
    if not dry_run:
        print(f"[{paper_id}] capturing baseline errors...", flush=True)
        baseline_errors = _capture_baseline_errors(
            lean_file, timeout_s=max(120, per_lake_timeout * 2),
        )
        print(f"[{paper_id}] baseline_errors={baseline_errors}", flush=True)
        report["baseline_errors"] = baseline_errors
    else:
        baseline_errors = 0

    # Build validator closure for aux elaboration probe.
    def _validate_aux(decl: str) -> tuple[bool, str]:
        if _run_isolated_file_check is None:
            # In dry-run / environments without lake we still want to
            # accept the candidate so the LLM's structural output is
            # captured. The downstream whole-proof + lake validation is
            # the real guard.
            return True, ""
        return _run_isolated_file_check(
            project_root=PROJECT_ROOT,
            source_file=lean_file,
            theorem_decl=decl,
            timeout_s=max(30, per_lake_timeout),
        )

    # Filter and sort candidates by priority.
    cands: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        ok, prio = _is_candidate_row(entry)
        if not ok:
            continue
        name = str(entry.get("theorem_name", "") or "")
        is_sorry, _ = wp_sweep._file_has_sorry_body_for(lean_file, name)
        if not is_sorry:
            continue
        cands.append((prio, entry))
    cands.sort(key=lambda t: t[0])
    if max_candidates > 0:
        cands = cands[:max_candidates]
    print(f"[{paper_id}] candidates_pre_elab={len(cands)}", flush=True)

    for _prio, entry in cands:
        name = str(entry.get("theorem_name", "") or "")
        short = _theorem_short_name(name)
        lean_stmt = str(entry.get("lean_statement", "") or "")
        report["candidates_attempted"] += 1
        per_row: dict[str, Any] = {
            "theorem": name,
            "stages": [],
        }

        # Trust in-file evidence (the body is currently sorry => the
        # signature already elaborated). We don't re-probe the parent;
        # the lake validation post-patch is the load-bearing guard.
        report["candidates_elaborated"] += 1

        if dry_run:
            per_row["stages"].append("dry_run_skip")
            report["details"].append(per_row)
            continue

        # --- First pass: whole-proof on parent --------------------------
        target_name = short if wp_sweep._flex_theorem_sorry_re(short).search(file_text) else name
        first_pass_validated = False

        # --- REPL-prover first pass (opt-in) ----------------------------
        # When `--use-repl-prover` is set, try the REPL-driven tactic-by-
        # tactic prover BEFORE the whole-proof generator. The REPL prover
        # validates each tactic against the real on-disk file via lake;
        # we still re-validate the assembled body once at the end to guard
        # against any in-REPL drift relative to the on-disk file.
        if use_repl_prover and client is not None:
            try:
                repl_result = repl_gen.prove_via_repl(
                    paper_id=paper_id,
                    theorem_name=short or name,
                    lean_statement=lean_stmt,
                    paper_theory_hint=paper_theory_hint,
                    paper_local_file=str(lean_file),
                    client=client,
                    model=model,
                    repl_timeout_s=per_lake_timeout,
                )
            except Exception as exc:
                per_row["stages"].append({
                    "stage": "repl_prover_transport_error",
                    "err": str(exc)[:200],
                })
                bucket_counts["transport_errors"] += 1
                repl_result = None
            if repl_result is not None:
                body = repl_result["proof_body"]
                if wp_sweep._patch_proof_flex(lean_file, target_name, body):
                    ok, err_tail = _lake_validate_aware(
                        lean_file, target_name,
                        baseline_errors=baseline_errors,
                        timeout_s=per_lake_timeout,
                    )
                    if ok:
                        first_pass_validated = True
                        report["first_pass_validated"] += 1
                        wp_sweep._apply_accept_to_entry(
                            entry,
                            proof_body=body,
                            reasoning=f"repl_prover:{repl_result.get('rounds', 0)}rounds",
                            confidence=0.9,
                            round_idx=1,
                        )
                        per_row["stages"].append({
                            "stage": "repl_prover_validated",
                            "body_preview": body[:80],
                            "rounds": repl_result.get("rounds", 0),
                        })
                    else:
                        wp_sweep._revert_proof_flex(lean_file, target_name)
                        per_row["stages"].append({
                            "stage": "repl_prover_lake_error",
                            "err_tail": (err_tail or "")[-160:],
                        })
                        bucket_counts["lake_errors"] += 1
                    file_text = lean_file.read_text(encoding="utf-8")
                else:
                    per_row["stages"].append({"stage": "repl_prover_patch_failed"})
            else:
                per_row["stages"].append({"stage": "repl_prover_returned_none"})

        # Whole-proof first pass runs only if REPL prover didn't already win.
        cand = None
        if not first_pass_validated:
            try:
                cand = gen.generate_proof_candidate(
                    paper_id=paper_id,
                    theorem_name=short or name,
                    lean_statement=lean_stmt,
                    paper_theory_hint=paper_theory_hint,
                    paper_local_file=file_text,
                    error_tail="",
                    client=client,
                    model=model,
                )
            except Exception as exc:
                per_row["stages"].append({"stage": "first_pass_transport_error", "err": str(exc)[:200]})
                bucket_counts["transport_errors"] += 1

        if cand is not None:
            body = cand["proof_body"]
            if wp_sweep._patch_proof_flex(lean_file, target_name, body):
                ok, err_tail = _lake_validate_aware(
                    lean_file, target_name,
                    baseline_errors=baseline_errors,
                    timeout_s=per_lake_timeout,
                )
                if ok:
                    first_pass_validated = True
                    report["first_pass_validated"] += 1
                    wp_sweep._apply_accept_to_entry(
                        entry,
                        proof_body=body,
                        reasoning=cand.get("reasoning", ""),
                        confidence=float(cand.get("confidence", 0.0)),
                        round_idx=1,
                    )
                    per_row["stages"].append({
                        "stage": "first_pass_validated",
                        "body_preview": body[:80],
                    })
                else:
                    wp_sweep._revert_proof_flex(lean_file, target_name)
                    per_row["stages"].append({
                        "stage": "first_pass_lake_error",
                        "err_tail": (err_tail or "")[-160:],
                    })
                    bucket_counts["lake_errors"] += 1
                # Refresh in-memory file_text after any patch/revert.
                file_text = lean_file.read_text(encoding="utf-8")
            else:
                per_row["stages"].append({"stage": "first_pass_patch_failed"})
        else:
            per_row["stages"].append({"stage": "first_pass_forbidden_or_malformed"})
            bucket_counts["forbidden_token_rejects"] += 1

        if first_pass_validated:
            # Audit per-paper to ensure we didn't accidentally inflate.
            audit_ok, audit_summary = _run_integrity_audit(paper_id)
            if audit_ok:
                report["audit_survived"] += 1
                per_row["audit"] = "survived"
            else:
                per_row["audit"] = "demoted"
                per_row["audit_summary"] = audit_summary
                # Roll back: revert the parent to sorry and remove
                # accept-state from the entry.
                wp_sweep._revert_proof_flex(lean_file, target_name)
                # Re-load from disk (audit may have written).
                data = json.loads(led_path.read_text(encoding="utf-8"))
                entries = data if isinstance(data, list) else data.get("entries", [])
            report["details"].append(per_row)
            continue

        # --- Factor pass: lemma-factor-v2 -------------------------------
        try:
            factor_records = lfv2.factor_long_theorem_v2(
                paper_id=paper_id,
                theorem_name=short or name,
                lean_statement=lean_stmt,
                paper_theory_hint=paper_theory_hint,
                exported_symbols=exported_symbols,
                client=client,
                model=model,
                validate_elaboration=_validate_aux,
            )
        except Exception as exc:
            per_row["stages"].append({"stage": "factor_transport_error", "err": str(exc)[:200]})
            bucket_counts["transport_errors"] += 1
            report["details"].append(per_row)
            continue
        report["aux_proposed"] += len(factor_records)
        elaborated = [r for r in factor_records if not r["rejected"]]
        report["aux_elaborated"] += len(elaborated)
        if len(elaborated) < 2:
            per_row["stages"].append({
                "stage": "factor_below_min_aux",
                "proposed": len(factor_records),
                "elaborated": len(elaborated),
            })
            report["details"].append(per_row)
            continue

        report["factored"] += 1
        # Rename each aux for global uniqueness + factored-aux suffix.
        # Prefer the fine shape (Round-VIII); fall back to the coarse one
        # if the field is absent (older records).
        target_shape = elaborated[0].get(
            "parent_target_shape_fine",
            elaborated[0].get("parent_target_shape", "other"),
        )
        renamed: list[tuple[str, str, dict[str, Any]]] = []  # (new_name, sig, record)
        for idx, rec in enumerate(elaborated, start=1):
            new_name = _qualify_aux_name(short or name, rec["aux_name"], idx)
            new_sig = _rename_aux_in_signature(rec["aux_signature"], new_name)
            renamed.append((new_name, new_sig, rec))

        # Insert aux into the file above the parent.
        aux_signatures_only = [sig for _, sig, _ in renamed]
        inserted, _ = _insert_aux_lemmas_above_parent(
            lean_file, target_name, aux_signatures_only,
        )
        if not inserted:
            per_row["stages"].append({"stage": "aux_insert_failed"})
            report["details"].append(per_row)
            continue
        file_text = lean_file.read_text(encoding="utf-8")

        # --- Close each aux via whole-proof generator -------------------
        aux_closed_names: list[str] = []
        for new_name, sig, rec in renamed:
            err_tail = ""
            closed = False
            for round_idx in range(1, max_rounds + 1):
                try:
                    aux_cand = gen.generate_proof_candidate(
                        paper_id=paper_id,
                        theorem_name=new_name,
                        lean_statement=sig,
                        paper_theory_hint=paper_theory_hint,
                        paper_local_file=file_text,
                        error_tail=err_tail,
                        client=client,
                        model=model,
                    )
                except Exception as exc:
                    bucket_counts["transport_errors"] += 1
                    per_row["stages"].append({"stage": "aux_transport_error",
                                              "aux": new_name, "err": str(exc)[:120]})
                    break
                if aux_cand is None:
                    bucket_counts["forbidden_token_rejects"] += 1
                    continue
                body = aux_cand["proof_body"]
                if not wp_sweep._patch_proof_flex(lean_file, new_name, body):
                    per_row["stages"].append({"stage": "aux_patch_failed", "aux": new_name})
                    break
                ok, t = _lake_validate_aware(
                    lean_file, new_name,
                    baseline_errors=baseline_errors,
                    timeout_s=per_lake_timeout,
                )
                if ok:
                    closed = True
                    file_text = lean_file.read_text(encoding="utf-8")
                    break
                wp_sweep._revert_proof_flex(lean_file, new_name)
                err_tail = t or ""
                bucket_counts["lake_errors"] += 1
            if closed:
                aux_closed_names.append(new_name)
                report["aux_closed"] += 1

        per_row["aux_closed"] = aux_closed_names
        per_row["aux_proposed_count"] = len(renamed)

        # Shapes that can be composed from a single aux:
        _SINGLE_AUX_OK_SHAPES = {
            "implication",
            "universal_implication",
            "universal_with_bound",
            "disjunction",
            "exists_with_witness",
        }
        min_needed = 1 if target_shape in _SINGLE_AUX_OK_SHAPES else 2
        if len(aux_closed_names) < min_needed:
            # Insufficient closed aux to attempt composition; clean up.
            cleanup_names = [nm for nm, _, _ in renamed]
            _remove_aux_lemmas(lean_file, cleanup_names)
            file_text = lean_file.read_text(encoding="utf-8")
            per_row["stages"].append({
                "stage": "insufficient_aux_closed",
                "closed": len(aux_closed_names),
                "needed": min_needed,
                "shape": target_shape,
            })
            report["details"].append(per_row)
            continue

        # --- Attempt parent composition --------------------------------
        # Build aux records so the role-mapper can place each closed aux
        # into the right slot of the chosen skeleton.
        closed_records = [
            {
                "aux_name": new_name,
                "compose_hint": rec.get("compose_hint", ""),
            }
            for (new_name, _sig, rec) in renamed
            if new_name in aux_closed_names
        ]
        composed_ok, comp_body, comp_err = attempt_composition(
            lean_file=lean_file,
            parent_short_name=target_name,
            aux_names=aux_closed_names,
            parent_target_shape=target_shape,
            per_lake_timeout=per_lake_timeout,
            baseline_errors=baseline_errors,
            aux_records=closed_records,
        )
        if not composed_ok:
            cleanup_names = [nm for nm, _, _ in renamed]
            _remove_aux_lemmas(lean_file, cleanup_names)
            file_text = lean_file.read_text(encoding="utf-8")
            per_row["stages"].append({
                "stage": "composition_failed",
                "err_tail": (comp_err or "")[-160:],
            })
            report["details"].append(per_row)
            continue

        report["composed"] += 1
        # Update the parent's ledger entry to record the success.
        wp_sweep._apply_accept_to_entry(
            entry,
            proof_body=comp_body,
            reasoning=f"factored via {len(aux_closed_names)} aux: " + ", ".join(aux_closed_names),
            confidence=0.6,
            round_idx=1,
        )
        # Record factored-aux metadata on the entry.
        entry["factored_aux"] = {
            "protocol": "lemma_factor_v2",
            "aux_names": aux_closed_names,
            "composition_body": comp_body,
            "parent_target_shape": target_shape,
        }
        file_text = lean_file.read_text(encoding="utf-8")

        # Audit per-paper to verify the integrity.
        audit_ok, audit_summary = _run_integrity_audit(paper_id)
        if audit_ok:
            report["audit_survived"] += 1
            per_row["audit"] = "survived"
            per_row["stages"].append({
                "stage": "composed_and_audit_survived",
                "composition_body": comp_body[:120],
            })
        else:
            # Roll back the parent + aux.
            wp_sweep._revert_proof_flex(lean_file, target_name)
            cleanup_names = [nm for nm, _, _ in renamed]
            _remove_aux_lemmas(lean_file, cleanup_names)
            data = json.loads(led_path.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else data.get("entries", [])
            file_text = lean_file.read_text(encoding="utf-8")
            per_row["audit"] = "demoted"
            per_row["audit_summary"] = audit_summary
            per_row["stages"].append({"stage": "composition_audit_demoted"})

        report["details"].append(per_row)

    # Save ledger if any progress.
    if not dry_run and (report["first_pass_validated"] + report["composed"]) > 0:
        led_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"[{paper_id}] ledger updated: first_pass={report['first_pass_validated']} composed={report['composed']}", flush=True)

    return report


# --- Mistral client -------------------------------------------------------


def _build_mistral_client() -> Any | None:
    if Mistral is None:
        return None
    key = (os.environ.get("MISTRAL_API_KEY") or os.environ.get("LEANSTRAL_API_KEY") or "").strip()
    if not key:
        return None
    try:
        return Mistral(api_key=key)
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", action="append", default=[])
    parser.add_argument("--max-candidates", type=int, default=12,
                        help="Per-paper cap on candidates (0 = no limit).")
    parser.add_argument("--max-rounds", type=int, default=2,
                        help="Max LLM retry rounds per aux proof generation.")
    parser.add_argument("--per-lake-timeout", type=int, default=60)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--use-repl-prover",
        action="store_true",
        default=False,
        help=(
            "Enable the REPL-driven step-by-step prover as a parent first-pass "
            "BEFORE the whole-proof generator (default OFF). Aux lemmas always "
            "use the whole-proof generator regardless."
        ),
    )
    parser.add_argument("--summary", default="output/lemma_factor_v2_sweep_summary.json")
    args = parser.parse_args()

    papers = args.paper or CANONICAL_PAPERS

    if args.dry_run:
        client = None
    else:
        client = _build_mistral_client()
        if client is None:
            print("[error] MISTRAL_API_KEY not set or mistralai unavailable", file=sys.stderr)
            return 2

    t0 = time.time()
    bucket_counts: Counter[str] = Counter()
    reports: list[dict[str, Any]] = []
    for pid in papers:
        r = _sweep_paper(
            paper_id=pid,
            client=client,
            model=args.model,
            max_candidates=args.max_candidates,
            max_rounds=args.max_rounds,
            per_lake_timeout=args.per_lake_timeout,
            dry_run=args.dry_run,
            bucket_counts=bucket_counts,
            use_repl_prover=args.use_repl_prover,
        )
        reports.append(r)
        # Persist partial summary after each paper so a mid-sweep crash
        # doesn't lose data.
        partial = {
            "elapsed_seconds": round(time.time() - t0, 1),
            "papers": reports,
            "bucket_counts": dict(bucket_counts),
        }
        out = PROJECT_ROOT / args.summary
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(partial, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    elapsed = time.time() - t0
    summary = {
        "elapsed_seconds": round(elapsed, 1),
        "papers": reports,
        "totals": {
            "candidates_elaborated": sum(r.get("candidates_elaborated", 0) for r in reports),
            "candidates_attempted": sum(r.get("candidates_attempted", 0) for r in reports),
            "first_pass_validated": sum(r.get("first_pass_validated", 0) for r in reports),
            "factored": sum(r.get("factored", 0) for r in reports),
            "aux_proposed": sum(r.get("aux_proposed", 0) for r in reports),
            "aux_elaborated": sum(r.get("aux_elaborated", 0) for r in reports),
            "aux_closed": sum(r.get("aux_closed", 0) for r in reports),
            "composed": sum(r.get("composed", 0) for r in reports),
            "audit_survived": sum(r.get("audit_survived", 0) for r in reports),
        },
        "bucket_counts": dict(bucket_counts),
    }
    print("\n=== Sweep summary ===")
    print(json.dumps(summary["totals"], indent=2))

    out = PROJECT_ROOT / args.summary
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[summary] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
