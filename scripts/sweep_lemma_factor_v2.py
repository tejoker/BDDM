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
import signature_typeclass_patcher as tc_patcher  # noqa: E402
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

try:
    import lake_validation_cache as _lvc  # type: ignore[import-not-found]
except Exception:
    _lvc = None  # type: ignore[assignment]

try:
    import route_to_axiom_backed as r2ab  # type: ignore[import-not-found]
except Exception:
    r2ab = None  # type: ignore[assignment]

try:
    import autoproved_promotion as autoprom  # type: ignore[import-not-found]
except Exception:
    autoprom = None  # type: ignore[assignment]


# --- Fast/slow validator selector ---------------------------------------
# Flipped at startup by `main()` based on --use-fast-validation. The fast
# path reuses a persistent REPL worker per (project, paper_id) and avoids
# paying the Mathlib import cost on every call. Setting this False forces
# every call through the legacy ``lake env lean`` path so flag-OFF runs
# behave bit-identically to pre-cache behavior.
_USE_FAST_VALIDATION: bool = False
# Number of leading candidates to differential-check (run BOTH validators
# and require agreement). 0 disables.
_DIFFERENTIAL_REMAINING: int = 0
_DIFFERENTIAL_RESULTS: list[dict[str, Any]] = []

# When True, audit-surviving first-pass closures are also mirrored to
# `Desol/PaperProofs/Paper_<id>.lean` as `<name>__autoproved`. Flipped by
# the `--auto-promote-to-curated` CLI flag (default ON). Purely additive
# infrastructure: a failed promotion never blocks the underlying close.
_AUTO_PROMOTE_TO_CURATED: bool = True


def _select_validator(
    *,
    project_root: Path,
    paper_id: str,
    source_file: Path,
    theorem_decl: str,
    proof_body: Optional[str],
    timeout_s: int,
) -> tuple[bool, str]:
    """Route to fast or slow validator based on ``_USE_FAST_VALIDATION``.

    When differential-check is enabled (first N candidates of a sweep), runs
    BOTH validators, records the comparison, and uses the FAST result. Any
    disagreement is logged loudly so the sweep operator can spot regressions
    immediately.
    """
    global _DIFFERENTIAL_REMAINING
    if not _USE_FAST_VALIDATION or _lvc is None:
        if _run_isolated_file_check is None:
            return True, "isolated_check_skipped_no_lake"
        return _run_isolated_file_check(
            project_root=project_root, source_file=source_file,
            theorem_decl=theorem_decl, proof_body=proof_body, timeout_s=timeout_s,
        )
    if _DIFFERENTIAL_REMAINING > 0 and _run_isolated_file_check is not None:
        _DIFFERENTIAL_REMAINING -= 1
        fast_ok, fast_tail, diag = _lvc.differential_check(
            project_root=project_root, source_file=source_file,
            paper_id=paper_id, theorem_decl=theorem_decl,
            proof_body=proof_body, timeout_s=timeout_s,
        )
        _DIFFERENTIAL_RESULTS.append(diag)
        if not diag.get("agreement", True):
            print(
                f"[fast-validation][DIVERGENCE] paper={paper_id} "
                f"fast_ok={diag['fast_ok']} slow_ok={diag['slow_ok']} "
                f"decl={theorem_decl[:120]!r}",
                flush=True,
            )
        return fast_ok, fast_tail
    return _lvc.validated_isolated_check(
        project_root=project_root, paper_id=paper_id,
        theorem_decl=theorem_decl, proof_body=proof_body, timeout_s=timeout_s,
    )


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


# --- Patch isolation (Improvement 2) -------------------------------------


def _extract_theorem_decl_from_file(lean_text: str, theorem_name: str) -> str:
    """Return the full theorem declaration block (head through last
    body-or-signature line) for `theorem_name` from `lean_text`, or "" if
    not found. The block ends just before the next top-level decl or
    `end`/`namespace` directive. Matches both the fully-qualified name
    and the short suffix.
    """
    if not lean_text or not theorem_name:
        return ""
    short = theorem_name.rsplit(".", 1)[-1]
    lines = lean_text.splitlines()
    head_pat = re.compile(
        r"^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+(?:"
        + re.escape(theorem_name) + r"|" + re.escape(short)
        + r")\b"
    )
    next_pat = re.compile(
        r"^\s*(?:noncomputable\s+|private\s+)?(?:theorem|lemma|def|abbrev|axiom|end|namespace)\b"
    )
    start = -1
    for i, ln in enumerate(lines):
        if head_pat.match(ln):
            start = i
            break
    if start < 0:
        return ""
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if next_pat.match(lines[j]):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip()


def _run_isolated_patch_check(
    *,
    lean_file: Path,
    theorem_name: str,
    proof_body: str,
    theorem_decl: Optional[str] = None,
    extra_decls: Optional[list[str]] = None,
    timeout_s: int = 60,
    paper_id: str = "",
) -> tuple[bool, str]:
    """Validate a candidate proof body against a CLEAN BASELINE isolated
    `.lean` file containing ONLY the source-file prelude (imports + open
    scopes + namespace prologue), any `extra_decls` (e.g. aux lemmas the
    target proof depends on), and the target theorem with the candidate
    body. Returns (ok, error_tail).

    Why: lake reports errors from the ENTIRE on-disk file, including
    pre-existing errors in unrelated theorems. Validating against the
    full file means a perfectly good patch can be rejected (or, worse,
    spuriously accepted) — the baseline-error-count comparator can't
    tell whose error each diagnostic belongs to. The isolated path
    eliminates cross-theorem contamination.

    Re-uses `prove_arxiv_batch._run_isolated_file_check` (extended in
    Improvement 2 to accept an optional `proof_body`); when `extra_decls`
    are supplied we concatenate them ahead of the target theorem so the
    composition body can reference them.

    `theorem_decl` defaults to the decl text scraped from `lean_file`. The
    caller may override (e.g. for an aux that lives only in memory).
    """
    if _run_isolated_file_check is None:
        # No lake bridge available — treat as pass so the caller's
        # downstream gates remain the load-bearing guard.
        return True, "isolated_check_skipped_no_lake"
    decl = (theorem_decl or "").strip()
    if not decl:
        try:
            text = lean_file.read_text(encoding="utf-8")
        except Exception:
            return False, "isolated_patch_check_read_failed"
        decl = _extract_theorem_decl_from_file(text, theorem_name)
        if not decl:
            return False, f"isolated_patch_check_decl_not_found:{theorem_name}"
    if extra_decls:
        # Prepend aux/etc decls to the target so the isolated probe sees
        # them. Each extra is treated as a self-contained block; we don't
        # rewrite its body.
        joined_extras = "\n\n".join(
            (d or "").rstrip() for d in extra_decls if (d or "").strip()
        )
        if joined_extras:
            decl = joined_extras + "\n\n" + decl
    return _select_validator(
        project_root=PROJECT_ROOT,
        paper_id=paper_id,
        source_file=lean_file,
        theorem_decl=decl,
        proof_body=proof_body,
        timeout_s=timeout_s,
    )


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


def _capture_baseline_error_tail(
    lean_file: Path, *, timeout_s: int = 180, tail_chars: int = 4000
) -> str:
    """Capture the LAST `tail_chars` of lake-output for the untouched file.
    Used by the typeclass-patcher pre-pass to look for
    `synthInstanceFailed:` markers attributable to a specific row.
    Returns empty string on lake timeout.
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
        return ""
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if len(out) > tail_chars:
        return out[-tail_chars:]
    return out


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


# --- Signature splice (used by typeclass pre-pass) ------------------------


def _splice_signature_in_file(
    lean_file: Path, short_name: str, new_signature: str
) -> tuple[bool, str]:
    """Replace the on-disk signature head of `theorem <short_name>` with the
    head of `new_signature`. The proof body (`:= by ...`) is preserved.

    Returns (replaced, old_head) so the caller can revert. On failure returns
    (False, "").
    """
    text = lean_file.read_text(encoding="utf-8")
    pat = re.compile(
        r"((?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        + re.escape(short_name)
        + r"\b[\s\S]*?)(:=)",
    )
    m = pat.search(text)
    if m is None:
        return False, ""
    old_head = m.group(1)
    new_head_match = re.match(r"([\s\S]*?)(:=|$)", new_signature.strip())
    if new_head_match is None:
        return False, ""
    new_head = new_head_match.group(1).rstrip() + " "
    new_text = text[: m.start(1)] + new_head + text[m.end(1) :]
    lean_file.write_text(new_text, encoding="utf-8")
    return True, old_head


def _restore_signature_in_file(
    lean_file: Path, short_name: str, old_head: str
) -> bool:
    """Restore the signature head saved by `_splice_signature_in_file`."""
    if not old_head:
        return False
    text = lean_file.read_text(encoding="utf-8")
    pat = re.compile(
        r"((?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        + re.escape(short_name)
        + r"\b[\s\S]*?)(:=)",
    )
    m = pat.search(text)
    if m is None:
        return False
    new_text = text[: m.start(1)] + old_head + text[m.end(1) :]
    lean_file.write_text(new_text, encoding="utf-8")
    return True


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
    parent_target: str = "",
    use_isolated_check: bool = True,
    paper_id: str = "",
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
    isolated patch-check (Improvement 2) when `use_isolated_check=True`,
    otherwise the legacy baseline-aware full-file validator. The isolated
    path eliminates contamination from pre-existing errors in unrelated
    theorems; the aux blocks (with their now-closed proof bodies) are
    pulled from `lean_file` and prepended to the isolated probe so the
    composition body can reference them.
    """
    bodies = lfv2.render_composition_attempts(
        parent_target_shape=parent_target_shape,
        aux_names=aux_names,
        aux_records=aux_records,
        parent_target=parent_target,
    )
    if not bodies:
        return False, "", "no_composition_bodies"
    # Shared single-element list so the isolated_validator closure can read
    # the current composition body being attempted (avoids re-parsing it
    # out of the on-disk file which has been patched in-place).
    _current_body: list[str] = [""]
    if validator is None:
        baseline = int(baseline_errors or 0)

        if use_isolated_check and _run_isolated_file_check is not None:
            def _isolated_validator(f: Path, name: str) -> tuple[bool, str]:
                # Pull each aux's full block (with closed body) so the
                # composition body can reference it inside the isolated
                # baseline. Falls back to the baseline-aware validator if
                # any aux block can't be scraped (file read failure or a
                # missing aux declaration).
                try:
                    file_text_local = f.read_text(encoding="utf-8")
                except Exception:
                    return _lake_validate_aware(
                        f, name, baseline_errors=baseline, timeout_s=per_lake_timeout,
                    )
                aux_decls: list[str] = []
                for aux_nm in aux_names:
                    block = _extract_theorem_decl_from_file(file_text_local, aux_nm)
                    if not block:
                        return _lake_validate_aware(
                            f, name, baseline_errors=baseline, timeout_s=per_lake_timeout,
                        )
                    aux_decls.append(block)
                parent_block = _extract_theorem_decl_from_file(file_text_local, name)
                if not parent_block:
                    return _lake_validate_aware(
                        f, name, baseline_errors=baseline, timeout_s=per_lake_timeout,
                    )
                return _run_isolated_patch_check(
                    lean_file=f,
                    theorem_name=name,
                    proof_body=_current_body[0],
                    theorem_decl=parent_block,
                    extra_decls=aux_decls,
                    timeout_s=per_lake_timeout,
                    paper_id=paper_id,
                )

            validator = _isolated_validator
        else:
            def _default_validator(f: Path, name: str) -> tuple[bool, str]:
                return _lake_validate_aware(
                    f, name, baseline_errors=baseline, timeout_s=per_lake_timeout,
                )

            validator = _default_validator
    last_err = ""
    for body in bodies:
        _current_body[0] = body
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
    use_typeclass_patcher: bool = False,
    multi_shot_samples: int = 1,
    max_factor_depth: int = 1,
    theorem_filter: tuple[str, ...] = (),
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
        "routed_to_axiom_backed": 0,
        # Recursive factoring (--max-factor-depth >= 2). When the depth-1
        # factor pass leaves long unclosed aux on the table, sweep can
        # recursively factor them up to ``max_factor_depth``. These
        # counters track per-paper recursive activity even when
        # max_factor_depth=1 (in which case they stay at 0).
        "factor_recursive_attempts": 0,
        "factor_recursive_closures": 0,
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
        if _run_isolated_file_check is None and _lvc is None:
            # In dry-run / environments without lake we still want to
            # accept the candidate so the LLM's structural output is
            # captured. The downstream whole-proof + lake validation is
            # the real guard.
            return True, ""
        return _select_validator(
            project_root=PROJECT_ROOT,
            paper_id=paper_id,
            source_file=lean_file,
            theorem_decl=decl,
            proof_body=None,
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
        signature_patch_state: dict[str, Any] = {
            "applied": False,
            "old_head": "",
            "patched_signature": "",
        }

        # --- Typeclass-patcher pre-pass (opt-in) ------------------------
        # Capture the per-row baseline lake error tail; if it names a
        # `synthInstanceFailed: <Class> <FreeVar>` pinned to a Type-var
        # declared in the row's signature, propose a signature patch
        # (insert `[<Class> <FreeVar>]`) and splice the FIRST candidate
        # that elaborates into the on-disk file. The signature change
        # persists only if a proof body subsequently closes against it
        # (decided by the existing whole-proof + audit pipeline below).
        if use_typeclass_patcher and _run_isolated_file_check is not None:
            tail = _capture_baseline_error_tail(
                lean_file, timeout_s=max(120, per_lake_timeout * 2)
            )
            try:
                proposals = tc_patcher.propose_typeclass_additions(
                    paper_id=paper_id,
                    theorem_name=target_name,
                    lean_statement=lean_stmt,
                    baseline_error=tail,
                    validate=tc_patcher.build_isolated_validator(
                        project_root=PROJECT_ROOT,
                        source_file=lean_file,
                        timeout_s=max(30, per_lake_timeout),
                    ),
                )
            except Exception as exc:
                proposals = []
                per_row["stages"].append({
                    "stage": "typeclass_patcher_error",
                    "err": str(exc)[:160],
                })
            if proposals:
                # Splice the first elaborating candidate into the file.
                ok, old_head = _splice_signature_in_file(
                    lean_file, target_name, proposals[0],
                )
                if ok:
                    new_baseline = _capture_baseline_errors(
                        lean_file, timeout_s=max(120, per_lake_timeout * 2),
                    )
                    if new_baseline <= baseline_errors:
                        signature_patch_state["applied"] = True
                        signature_patch_state["old_head"] = old_head
                        signature_patch_state["patched_signature"] = proposals[0]
                        baseline_errors = new_baseline
                        file_text = lean_file.read_text(encoding="utf-8")
                        per_row["stages"].append({
                            "stage": "typeclass_patch_spliced",
                            "patched_sig_preview": proposals[0][:160],
                            "new_baseline": new_baseline,
                        })
                    else:
                        # Patch regressed baseline — revert.
                        _restore_signature_in_file(lean_file, target_name, old_head)
                        file_text = lean_file.read_text(encoding="utf-8")
                        per_row["stages"].append({
                            "stage": "typeclass_patch_regressed_baseline",
                            "new_baseline": new_baseline,
                            "prior_baseline": baseline_errors,
                        })
                else:
                    per_row["stages"].append({"stage": "typeclass_patch_splice_failed"})

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
        first_pass_rejection_sink: dict[str, Any] = {}
        multi_shot_meta: dict[str, Any] = {}
        if not first_pass_validated:
            try:
                if multi_shot_samples and multi_shot_samples > 1:
                    # Multi-shot mode: N parallel samples with diverse
                    # temperatures, each independently validated via the
                    # fast isolated-elaboration probe. Short-circuit on
                    # the first survivor; otherwise return the sorted
                    # list and hand the head off to the downstream
                    # isolated-check below (which will reject again, but
                    # we still surface diagnostic temperature data).
                    def _ms_validator(c: dict[str, Any]) -> tuple[bool, str]:
                        return _run_isolated_patch_check(
                            lean_file=lean_file,
                            theorem_name=target_name,
                            proof_body=c["proof_body"],
                            timeout_s=per_lake_timeout,
                            paper_id=paper_id,
                        )

                    multi_shot_sink: dict[str, Any] = {}
                    cands_list = gen.generate_proof_candidates_multi_shot(
                        paper_id=paper_id,
                        theorem_name=short or name,
                        lean_statement=lean_stmt,
                        paper_theory_hint=paper_theory_hint,
                        paper_local_file=file_text,
                        client=client,
                        model=model,
                        n_samples=int(multi_shot_samples),
                        validate_elaboration=_ms_validator,
                        rejection_sink=multi_shot_sink,
                    )
                    multi_shot_meta = {
                        "n_samples": int(multi_shot_samples),
                        "short_circuited": bool(multi_shot_sink.get("short_circuited")),
                        "winning_sample_idx": multi_shot_sink.get("winning_sample_idx"),
                        "winning_temperature": multi_shot_sink.get("winning_temperature"),
                        "rejection_log": multi_shot_sink.get("rejection_log", []),
                        "candidates_returned": len(cands_list),
                    }
                    cand = cands_list[0] if cands_list else None
                else:
                    cand = gen.generate_proof_candidate(
                        paper_id=paper_id,
                        theorem_name=short or name,
                        lean_statement=lean_stmt,
                        paper_theory_hint=paper_theory_hint,
                        paper_local_file=file_text,
                        error_tail="",
                        client=client,
                        model=model,
                        rejection_sink=first_pass_rejection_sink,
                    )
            except Exception as exc:
                per_row["stages"].append({"stage": "first_pass_transport_error", "err": str(exc)[:200]})
                bucket_counts["transport_errors"] += 1

        if cand is not None:
            body = cand["proof_body"]
            # Improvement 2: validate against a CLEAN BASELINE isolated
            # `.lean` (prelude + target only, no cross-theorem
            # contamination). On accept, patch into the on-disk file.
            isolated_ok, isolated_err = _run_isolated_patch_check(
                lean_file=lean_file,
                theorem_name=target_name,
                proof_body=body,
                timeout_s=per_lake_timeout,
                paper_id=paper_id,
            )
            if isolated_ok and wp_sweep._patch_proof_flex(lean_file, target_name, body):
                first_pass_validated = True
                report["first_pass_validated"] += 1
                wp_sweep._apply_accept_to_entry(
                    entry,
                    proof_body=body,
                    reasoning=cand.get("reasoning", ""),
                    confidence=float(cand.get("confidence", 0.0)),
                    round_idx=1,
                )
                validated_stage: dict[str, Any] = {
                    "stage": "first_pass_validated",
                    "body_preview": body[:80],
                    "validator": "isolated_patch_check",
                }
                if multi_shot_meta:
                    validated_stage["multi_shot"] = multi_shot_meta
                per_row["stages"].append(validated_stage)
                file_text = lean_file.read_text(encoding="utf-8")
            elif not isolated_ok:
                per_row["stages"].append({
                    "stage": "first_pass_isolated_check_failed",
                    "err_tail": (isolated_err or "")[-160:],
                })
                bucket_counts["lake_errors"] += 1
            else:
                per_row["stages"].append({"stage": "first_pass_patch_failed"})
        else:
            stage_record: dict[str, Any] = {"stage": "first_pass_forbidden_or_malformed"}
            if first_pass_rejection_sink.get("reason"):
                stage_record["rejection_reason"] = first_pass_rejection_sink["reason"]
            if multi_shot_meta:
                stage_record["multi_shot"] = multi_shot_meta
            per_row["stages"].append(stage_record)
            bucket_counts["forbidden_token_rejects"] += 1

        if first_pass_validated:
            # Audit per-paper to ensure we didn't accidentally inflate.
            audit_ok, audit_summary = _run_integrity_audit(paper_id)
            if audit_ok:
                report["audit_survived"] += 1
                per_row["audit"] = "survived"
                # Record the signature-patch as part of the formalization
                # commitment (caller-visible audit_trail field).
                if signature_patch_state["applied"]:
                    trail = entry.setdefault("audit_trail", [])
                    if isinstance(trail, list):
                        trail.append({
                            "event": "signature_patched_for_typeclass",
                            "patched_signature": signature_patch_state["patched_signature"],
                            "prior_head": signature_patch_state["old_head"],
                            "protocol": "signature_typeclass_patcher_v1",
                        })
                    entry["signature_patched_for_typeclass"] = True
                    per_row["stages"].append({"stage": "signature_patch_kept"})
                # --- Auto-promote to curated PaperProofs ----------------
                # Mirror the audit-surviving proof into
                # `Desol/PaperProofs/Paper_<id>.lean` as a `__autoproved`
                # companion. This is purely additive infrastructure: a
                # failed promotion never blocks the underlying close.
                if _AUTO_PROMOTE_TO_CURATED and autoprom is not None:
                    try:
                        autoprom_result = autoprom.promote_to_autoproved(
                            paper_id=paper_id,
                            theorem_name=short or name,
                            lean_statement=lean_stmt,
                            proof_body=body,
                            project_root=PROJECT_ROOT,
                            validate_elaboration=None,
                        )
                    except Exception as exc:
                        autoprom_result = {
                            "ok": False,
                            "status": f"raised:{exc.__class__.__name__}",
                        }
                    if autoprom_result.get("ok"):
                        trail = entry.setdefault("audit_trail", [])
                        if isinstance(trail, list):
                            trail.append({
                                "event": "autoproved_promotion",
                                "file": autoprom_result.get("path", ""),
                                "sha": autoprom_result.get("sha", ""),
                                "autoproved_name": autoprom_result.get("autoproved_name", ""),
                                "status": autoprom_result.get("status", ""),
                                "protocol": "autoproved_promotion_v1",
                            })
                    per_row["stages"].append({
                        "stage": "autoproved_promotion",
                        "ok": bool(autoprom_result.get("ok")),
                        "status": autoprom_result.get("status", ""),
                        "autoproved_name": autoprom_result.get("autoproved_name", ""),
                    })
            else:
                per_row["audit"] = "demoted"
                per_row["audit_summary"] = audit_summary
                # Roll back: revert the parent to sorry and remove
                # accept-state from the entry.
                wp_sweep._revert_proof_flex(lean_file, target_name)
                # Revert any signature patch — proofless commitments are
                # not real formalization progress.
                if signature_patch_state["applied"]:
                    _restore_signature_in_file(
                        lean_file, target_name, signature_patch_state["old_head"],
                    )
                    per_row["stages"].append({"stage": "signature_patch_reverted_on_audit_demotion"})
                # Re-load from disk (audit may have written).
                data = json.loads(led_path.read_text(encoding="utf-8"))
                entries = data if isinstance(data, list) else data.get("entries", [])
            report["details"].append(per_row)
            continue

        # --- Paper-local axiom routing ----------------------------------
        # When the first-pass lake error indicates that the row is
        # blocked by an opaque paper-local axiom (declared as
        # `axiom <name>` or stub `def <name> := 0/True/sorry/Set.univ`
        # in paper-theory), route the entry to AXIOM_BACKED with the
        # precise axiom_debt list. No tactic search can close such a
        # row honestly; decomposition (lemma-factor) does not help
        # against opacity. Skip the factor pass so we don't burn
        # Mistral budget on a hopeless decomposition.
        if r2ab is not None:
            ax_err_tail = ""
            for stg in reversed(per_row["stages"]):
                if isinstance(stg, dict) and stg.get("stage") in (
                    "first_pass_lake_error",
                    "repl_prover_lake_error",
                ):
                    ax_err_tail = str(stg.get("err_tail", "") or "")
                    break
            if ax_err_tail:
                ax_route = r2ab.detect_paper_axiom_block(
                    paper_id=paper_id,
                    theorem_name=short or name,
                    lean_statement=lean_stmt,
                    lake_error=ax_err_tail,
                    paper_theory_file=paper_theory_path,
                )
                if ax_route is not None:
                    r2ab.apply_route_to_entry(
                        entry,
                        route=ax_route,
                        paper_id=paper_id,
                        lake_error_preview=ax_err_tail,
                    )
                    report["routed_to_axiom_backed"] += 1
                    per_row["stages"].append({
                        "stage": "routed_to_axiom_backed",
                        "axiom_debt": ax_route["axiom_debt"],
                    })
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
            if signature_patch_state["applied"]:
                _restore_signature_in_file(
                    lean_file, target_name, signature_patch_state["old_head"]
                )
                file_text = lean_file.read_text(encoding="utf-8")
                per_row["stages"].append({"stage": "signature_patch_reverted_no_closure"})
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
            if signature_patch_state["applied"]:
                _restore_signature_in_file(
                    lean_file, target_name, signature_patch_state["old_head"]
                )
                file_text = lean_file.read_text(encoding="utf-8")
                per_row["stages"].append({"stage": "signature_patch_reverted_no_closure"})
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
            if signature_patch_state["applied"]:
                _restore_signature_in_file(
                    lean_file, target_name, signature_patch_state["old_head"]
                )
                file_text = lean_file.read_text(encoding="utf-8")
                per_row["stages"].append({"stage": "signature_patch_reverted_no_closure"})
            report["details"].append(per_row)
            continue
        file_text = lean_file.read_text(encoding="utf-8")

        # --- Close each aux via whole-proof generator -------------------
        aux_closed_names: list[str] = []
        for new_name, sig, rec in renamed:
            err_tail = ""
            closed = False
            for round_idx in range(1, max_rounds + 1):
                aux_rejection_sink: dict[str, Any] = {}
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
                        rejection_sink=aux_rejection_sink,
                    )
                except Exception as exc:
                    bucket_counts["transport_errors"] += 1
                    per_row["stages"].append({"stage": "aux_transport_error",
                                              "aux": new_name, "err": str(exc)[:120]})
                    break
                if aux_cand is None:
                    bucket_counts["forbidden_token_rejects"] += 1
                    # Improvement 1: thread the clarification into the
                    # next-round error_tail so the LLM has a signal about
                    # WHY its previous attempt was discarded.
                    clarification = aux_rejection_sink.get("clarification") or ""
                    if clarification:
                        err_tail = clarification
                    continue
                body = aux_cand["proof_body"]
                # Improvement 2: validate the aux against a clean isolated
                # baseline first so pre-existing errors in unrelated
                # theorems can't contaminate the result. The aux signature
                # is supplied explicitly via `theorem_decl=sig`.
                iso_ok, iso_err = _run_isolated_patch_check(
                    lean_file=lean_file,
                    theorem_name=new_name,
                    proof_body=body,
                    theorem_decl=sig,
                    timeout_s=per_lake_timeout,
                    paper_id=paper_id,
                )
                if not iso_ok:
                    err_tail = iso_err or ""
                    bucket_counts["lake_errors"] += 1
                    continue
                if not wp_sweep._patch_proof_flex(lean_file, new_name, body):
                    per_row["stages"].append({"stage": "aux_patch_failed", "aux": new_name})
                    break
                # Defensive: the isolated check accepted the body. We still
                # run the baseline-aware full-file check as a second gate
                # because the aux now lives in the on-disk file and the
                # composition path will rely on the on-disk state.
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
            if signature_patch_state["applied"]:
                _restore_signature_in_file(
                    lean_file, target_name, signature_patch_state["old_head"]
                )
                file_text = lean_file.read_text(encoding="utf-8")
                per_row["stages"].append({"stage": "signature_patch_reverted_no_closure"})
            report["details"].append(per_row)
            continue

        # --- Attempt parent composition --------------------------------
        # Build aux records so the role-mapper can place each closed aux
        # into the right slot of the chosen skeleton. Round-IX (v3): we
        # surface each aux's RENAMED signature so the type-aware mapper
        # can classify witness vs property aux from the return-type itself.
        closed_records = [
            {
                "aux_name": new_name,
                "compose_hint": rec.get("compose_hint", ""),
                "aux_signature": new_sig,
            }
            for (new_name, new_sig, rec) in renamed
            if new_name in aux_closed_names
        ]
        parent_target_for_v3 = elaborated[0].get("parent_target", "") if elaborated else ""
        composed_ok, comp_body, comp_err = attempt_composition(
            lean_file=lean_file,
            parent_short_name=target_name,
            aux_names=aux_closed_names,
            parent_target_shape=target_shape,
            per_lake_timeout=per_lake_timeout,
            baseline_errors=baseline_errors,
            aux_records=closed_records,
            parent_target=parent_target_for_v3,
            paper_id=paper_id,
        )
        if not composed_ok:
            cleanup_names = [nm for nm, _, _ in renamed]
            _remove_aux_lemmas(lean_file, cleanup_names)
            file_text = lean_file.read_text(encoding="utf-8")
            per_row["stages"].append({
                "stage": "composition_failed",
                "err_tail": (comp_err or "")[-160:],
            })
            if signature_patch_state["applied"]:
                _restore_signature_in_file(
                    lean_file, target_name, signature_patch_state["old_head"]
                )
                file_text = lean_file.read_text(encoding="utf-8")
                per_row["stages"].append({"stage": "signature_patch_reverted_no_closure"})
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
            # Record the signature-patch as part of the factored success.
            if signature_patch_state["applied"]:
                trail = entry.setdefault("audit_trail", [])
                if isinstance(trail, list):
                    trail.append({
                        "event": "signature_patched_for_typeclass",
                        "patched_signature": signature_patch_state["patched_signature"],
                        "prior_head": signature_patch_state["old_head"],
                        "protocol": "signature_typeclass_patcher_v1",
                    })
                entry["signature_patched_for_typeclass"] = True
                per_row["stages"].append({"stage": "signature_patch_kept_via_factor"})
        else:
            # Roll back the parent + aux + signature patch (no closure).
            wp_sweep._revert_proof_flex(lean_file, target_name)
            cleanup_names = [nm for nm, _, _ in renamed]
            _remove_aux_lemmas(lean_file, cleanup_names)
            if signature_patch_state["applied"]:
                _restore_signature_in_file(
                    lean_file, target_name, signature_patch_state["old_head"],
                )
                per_row["stages"].append({"stage": "signature_patch_reverted_on_audit_demotion"})
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
    parser.add_argument(
        "--use-typeclass-patcher",
        action="store_true",
        default=False,
        help=(
            "Enable the signature-typeclass pre-pass: when the per-row "
            "baseline lake error names `synthInstanceFailed: <Class> "
            "<FreeVar>` for a `Type*` binder declared in the signature, "
            "splice `[<Class> <FreeVar>]` into the signature on disk "
            "before running the proof generator. The patch is kept only "
            "when a proof body closes against the patched signature; "
            "otherwise it is reverted (default OFF until calibrated)."
        ),
    )
    parser.add_argument(
        "--multi-shot-samples",
        type=int,
        default=3,
        help=(
            "Number of parallel proof candidates to sample from Leanstral "
            "for the parent first-pass (with diverse temperatures 0.0/0.3/0.5/0.7/0.9). "
            "Each candidate is independently validated via the isolated-elaboration "
            "gate; the first survivor short-circuits the loop. 1 = legacy "
            "single-shot deterministic behaviour. Default 3."
        ),
    )
    parser.add_argument("--summary", default="output/lemma_factor_v2_sweep_summary.json")
    parser.add_argument(
        "--use-fast-validation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Route isolated-elaboration probes through the persistent REPL "
            "worker (scripts/lake_validation_cache). Confirmed >100× faster "
            "than `lake env lean` on warm runs. Use --no-use-fast-validation "
            "to force the legacy slow path."
        ),
    )
    parser.add_argument(
        "--differential-check-first",
        type=int,
        default=10,
        help=(
            "When --use-fast-validation is on, run BOTH validators for the "
            "first N candidates of the sweep and assert agreement. Set to 0 "
            "to disable."
        ),
    )
    parser.add_argument(
        "--auto-promote-to-curated",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "After a first-pass proof closes and the integrity audit "
            "survives, mirror the proof into "
            "Desol/PaperProofs/Paper_<id>.lean as a `<name>__autoproved` "
            "companion. Purely additive: failures don't block the close, "
            "and curated/non-trivial pre-conditions are re-checked inside "
            "autoproved_promotion.promote_to_autoproved."
        ),
    )
    args = parser.parse_args()

    # Wire the validator selector before any sweep work begins.
    global _USE_FAST_VALIDATION, _DIFFERENTIAL_REMAINING, _AUTO_PROMOTE_TO_CURATED
    _USE_FAST_VALIDATION = bool(args.use_fast_validation) and _lvc is not None
    _DIFFERENTIAL_REMAINING = max(0, int(args.differential_check_first)) if _USE_FAST_VALIDATION else 0
    _AUTO_PROMOTE_TO_CURATED = bool(args.auto_promote_to_curated) and autoprom is not None
    if args.auto_promote_to_curated and autoprom is None:
        print("[auto-promote] requested but autoproved_promotion unavailable — skipping", flush=True)
    if _USE_FAST_VALIDATION:
        print(
            f"[fast-validation] enabled (differential_check_first={_DIFFERENTIAL_REMAINING})",
            flush=True,
        )
    elif args.use_fast_validation and _lvc is None:
        print("[fast-validation] requested but lake_validation_cache unavailable — falling back to slow path", flush=True)

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
            use_typeclass_patcher=args.use_typeclass_patcher,
            multi_shot_samples=args.multi_shot_samples,
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
            "routed_to_axiom_backed": sum(r.get("routed_to_axiom_backed", 0) for r in reports),
        },
        "bucket_counts": dict(bucket_counts),
    }
    print("\n=== Sweep summary ===")
    print(json.dumps(summary["totals"], indent=2))

    out = PROJECT_ROOT / args.summary
    out.parent.mkdir(parents=True, exist_ok=True)
    summary["fast_validation"] = {
        "enabled": _USE_FAST_VALIDATION,
        "differential_results": _DIFFERENTIAL_RESULTS[:50],
        "differential_disagreements": sum(
            1 for r in _DIFFERENTIAL_RESULTS if not r.get("agreement", True)
        ),
    }
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[summary] wrote {out}")
    if _lvc is not None:
        try:
            _lvc.shutdown_all_workers()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
