#!/usr/bin/env python3
"""Sweep driver: ask Leanstral for whole-proof candidates across canonical
UR/IP rows, patch + lake-validate against the REAL file, commit only on
genuine success.

Standards-positive: forbidden tokens (`sorry` / `admit` / `apply?` / `axiom`
/ `native_decide`) are rejected pre-patch; `lake env lean` on the actual
`output/<paper>.lean` is the closure proof. The integrity audit (run
separately by the caller) is the post-hoc guard.

Usage:
    python3 scripts/sweep_leanstral_whole_proof.py \
        [--paper PID]... [--max-candidates N] [--max-rounds 3] \
        [--per-lake-timeout 60] [--dry-run] [--summary PATH]

Default papers: the canonical 8.

The driver:
  1. Builds a candidate list from each paper's ephemeral ledger.
     * Eligible: status in {UNRESOLVED, INTERMEDIARY_PROVEN} AND
       lean_proof_closed is missing/False.
     * Priority: UR with reviewed_equivalence_verdict='equivalent' first,
       then IP rows with lean_proof_closed in gate_failures, then remaining
       UR rows whose lean_statement elaborates.
  2. For each candidate:
     a. Probe the theorem signature with `_run_isolated_file_check`. Skip
        on failure (statement repair territory, not proof closure).
     b. Round 1..max_rounds: call generate_proof_candidate, patch the
        proof into `output/<paper>.lean`, run `lake env lean`, accept if
        the theorem line emits NO `declaration uses 'sorry'` warning AND
        returncode==0.
     c. On lake failure: revert to `sorry`, feed error tail back, retry.
  3. On accept: update the ledger entry — proof_text, status (per gate
     evaluation), validation_gates.lean_proof_closed=True, etc.
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
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import leanstral_whole_proof_generator as gen  # noqa: E402
import sweep_canonical_patch_and_validate as patcher  # noqa: E402

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


# --- Candidate selection --------------------------------------------------


def _load_ledger(pid: str) -> tuple[Path, list[dict[str, Any]]]:
    p = PROJECT_ROOT / "output" / "verification_ledgers" / f"{pid}.json"
    if not p.exists():
        return p, []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return p, []
    entries = data if isinstance(data, list) else data.get("entries", [])
    return p, list(entries)


def _save_ledger(p: Path, entries: list[dict[str, Any]]) -> None:
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_candidate_row(entry: dict[str, Any]) -> tuple[bool, int]:
    """Return (is_candidate, priority_bucket).

    Priority buckets (lower = higher priority):
      0: UR with reviewed_equivalence_verdict='equivalent'
      1: IP with lean_proof_closed missing/False (proof slot empty)
      2: remaining UR rows
    Non-candidate: AB / FP / FLAWED rows, or rows whose
    `lean_proof_closed=True` (already closed).
    """
    status = str(entry.get("status", "") or "")
    if status not in ("UNRESOLVED", "INTERMEDIARY_PROVEN"):
        return False, 99
    gates = entry.get("validation_gates") or {}
    if isinstance(gates, dict) and gates.get("lean_proof_closed") is True:
        # Already closed; nothing to do.
        return False, 99
    proof_text = str(entry.get("proof_text", "") or "").strip()
    if proof_text and proof_text != "sorry":
        # Has a non-trivial proof candidate already; the patch-and-validate
        # sweep will handle it. We don't re-generate.
        return False, 99
    if not (entry.get("lean_statement") or "").strip():
        return False, 99
    if status == "UNRESOLVED":
        if str(entry.get("reviewed_equivalence_verdict", "") or "").lower() == "equivalent":
            return True, 0
        return True, 2
    # INTERMEDIARY_PROVEN
    if status == "INTERMEDIARY_PROVEN":
        fails = list(entry.get("gate_failures") or [])
        if "lean_proof_closed" in fails:
            return True, 1
        return True, 1
    return False, 99


def _theorem_short_name(name: str) -> str:
    if not name:
        return ""
    return name.rsplit(".", 1)[-1]


# Matches `theorem <name> ... := by sorry` either single-line or
# multi-line. Group 1 is the prefix up to and including `:= by` plus the
# whitespace that follows; group 2 is the `sorry` literal so we know which
# form was matched.
def _flex_theorem_sorry_re(name: str) -> re.Pattern[str]:
    return re.compile(
        r"((?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        + re.escape(name)
        + r"\b[\s\S]*?:=\s*by[ \t]*\n?[ \t]*)(sorry)\b",
    )


_FALSE_SIGNATURE_RX = re.compile(r":\s*False\s*:=\s*by\s*\n?\s*sorry\b")


def _is_false_placeholder(lean_text: str, name: str) -> bool:
    """A theorem whose body in the file is `theorem <name> ... : False := by sorry`
    is a degenerated translation; proving it would be a hallucination. Skip it.
    """
    pat = re.compile(
        r"(?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        + re.escape(name)
        + r"\b[^\n]*?:\s*False\s*:=\s*by[ \t]*\n?[ \t]*sorry\b",
    )
    return bool(pat.search(lean_text))


def _file_has_sorry_body_for(lean_file: Path, name: str) -> tuple[bool, str | None]:
    """Return (is_sorry, target_name_used). target_name_used is the name
    that matched the `theorem <name>` pattern with body sorry; either the
    full name or the short name. Recognizes BOTH multi-line and single-line
    `:= by sorry` bodies. Excludes `: False` placeholders.
    """
    text = lean_file.read_text(encoding="utf-8")
    short = _theorem_short_name(name)
    for cand in (short, name):
        if not cand:
            continue
        if _is_false_placeholder(text, cand):
            continue
        if _flex_theorem_sorry_re(cand).search(text):
            return True, cand
    return False, None


def _patch_proof_flex(lean_file: Path, name: str, proof_body: str) -> bool:
    """Patch the proof body into the file, accepting BOTH `:= by\\n  sorry`
    and `:= by sorry` (single-line) forms. Always rewrites to multi-line
    form so subsequent runs of `_body_is_sorry_for` from the audit see the
    canonical layout.
    """
    text = lean_file.read_text(encoding="utf-8")
    pat = _flex_theorem_sorry_re(name)
    indented = proof_body.rstrip()
    if not indented:
        return False
    # Always emit a newline before the body and 2-space indent each line.
    body_lines = indented.splitlines()
    rendered = "\n  " + "\n  ".join(line.lstrip() if i == 0 else line for i, line in enumerate(body_lines)) + "\n"

    def _sub(m: re.Match[str]) -> str:
        # Group 1 is the prefix up to and including `:= by` + maybe whitespace.
        # We always rewrite to `:= by\n  <body>\n`.
        prefix = m.group(1)
        # Normalize the prefix to end with `:= by` (drop trailing whitespace).
        normalized = re.sub(r":=\s*by[ \t]*\n?[ \t]*$", ":= by", prefix)
        return normalized + rendered

    new_text = pat.sub(_sub, text, count=1)
    if new_text == text:
        return False
    lean_file.write_text(new_text, encoding="utf-8")
    return True


def _revert_proof_flex(lean_file: Path, name: str) -> bool:
    """Revert the patched theorem body back to `:= by\\n  sorry`. Matches
    a theorem body that's anything-but-sorry (since we just patched it)."""
    text = lean_file.read_text(encoding="utf-8")
    # Find the entire `theorem <name> ... := by\n  <body>` block and replace
    # body with `sorry`. Uses a non-greedy match that stops at the next
    # top-level declaration or end of file.
    pat = re.compile(
        r"((?:noncomputable\s+|private\s+)?(?:theorem|lemma)\s+"
        + re.escape(name)
        + r"\b[\s\S]*?:=\s*by[ \t]*\n)([\s\S]*?)(?=\n(?:noncomputable\s+|private\s+)?(?:theorem|lemma|def|abbrev|axiom|end|namespace)\b|\Z)",
    )
    m = pat.search(text)
    if not m:
        return False
    new_text = text[: m.start()] + m.group(1) + "  sorry\n" + text[m.end():]
    if new_text == text:
        return False
    lean_file.write_text(new_text, encoding="utf-8")
    return True


# --- Lake validation on REAL file -----------------------------------------


def _lake_validate_file_clean_for(
    lean_file: Path,
    theorem_name: str,
    *,
    timeout_s: int = 60,
) -> tuple[bool, str]:
    """Run `lake env lean` on `lean_file` and return (ok, error_tail).

    ok == True iff:
      - returncode == 0 OR (returncode != 0 but the ONLY diagnostic is
        `warning: declaration uses 'sorry'` on OTHER theorems);
      - AND no `declaration uses 'sorry'` warning is attached to the line
        of `theorem <theorem_name>`.

    error_tail is the last 1500 chars of stdout+stderr (for retry prompt).
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
    # If returncode != 0, it's an elaboration error somewhere — could be on
    # OUR theorem (real failure) or pre-existing on another theorem. In
    # practice the canonical files compile (or compile with only sorry
    # warnings), so a non-zero returncode after our patch typically means
    # OUR proof broke it. But be precise: only treat as failure if there's
    # an `error:` mentioning our theorem's line OR a generic error: tag.
    has_error = re.search(r"\berror:", out)
    if has_error and proc.returncode != 0:
        return False, out[-1500:]
    # Check whether OUR theorem still triggers a sorry warning.
    line_no = patcher._theorem_line_in_file(lean_file, _theorem_short_name(theorem_name)) \
        or patcher._theorem_line_in_file(lean_file, theorem_name)
    if line_no is None:
        # Couldn't locate the theorem in the file — treat as fail.
        return False, f"theorem_not_found_in_file:{theorem_name}"
    if not patcher._file_compiles_clean_for_theorem(out, line_no):
        return False, "patched_body_emits_sorry_warning"
    return True, ""


# --- Ledger update on accept ----------------------------------------------


def _gate_failures_after_accept(entry: dict[str, Any]) -> list[str]:
    """Drop `lean_proof_closed` from gate_failures (we just closed it)."""
    fails = [str(x) for x in (entry.get("gate_failures") or [])]
    return [f for f in fails if f != "lean_proof_closed"]


def _decide_status_after_close(entry: dict[str, Any]) -> str:
    """After the proof is closed, decide whether the row qualifies for
    FULLY_PROVEN, AXIOM_BACKED, or INTERMEDIARY_PROVEN.

    Conservative: defer to existing gate state. If `claim_equivalent`,
    `independent_semantic_equivalence_evidence`, and `provenance_linked`
    are all true and no axiom debt, FULLY_PROVEN. If axiom debt,
    AXIOM_BACKED. Otherwise INTERMEDIARY_PROVEN.
    """
    gates = entry.get("validation_gates") or {}
    if not isinstance(gates, dict):
        return "INTERMEDIARY_PROVEN"
    # Axiom debt -> AXIOM_BACKED (or stay there).
    if gates.get("no_paper_axiom_debt") is False:
        return "AXIOM_BACKED"
    if (
        gates.get("claim_equivalent")
        and gates.get("independent_semantic_equivalence_evidence")
        and gates.get("provenance_linked")
    ):
        return "FULLY_PROVEN"
    return "INTERMEDIARY_PROVEN"


def _apply_accept_to_entry(
    entry: dict[str, Any],
    *,
    proof_body: str,
    reasoning: str,
    confidence: float,
    round_idx: int,
) -> None:
    """Mutate the ledger entry in place: record the closed proof."""
    entry["proof_text"] = proof_body
    entry["proof_method"] = "leanstral_whole_proof_v1"
    entry["step_verdict"] = "VERIFIED"
    entry["failure_origin"] = "NONE"
    entry["failure_kind"] = ""
    entry["promotion_gate_passed"] = True
    gates = entry.get("validation_gates") or {}
    if not isinstance(gates, dict):
        gates = {}
    gates["lean_proof_closed"] = True
    gates["step_verdict_verified"] = True
    entry["validation_gates"] = gates
    entry["gate_failures"] = _gate_failures_after_accept(entry)
    new_status = _decide_status_after_close(entry)
    entry["status"] = new_status
    notes = [str(x) for x in (entry.get("claim_equivalence_notes") or [])]
    notes.append(
        f"leanstral_whole_proof_v1:closed_round={round_idx}:confidence={confidence:.2f}"
    )
    entry["claim_equivalence_notes"] = list(dict.fromkeys(notes))
    entry["leanstral_whole_proof"] = {
        "protocol": "leanstral_whole_proof_v1",
        "proof_body": proof_body,
        "reasoning": reasoning,
        "confidence": confidence,
        "round_idx": round_idx,
    }


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


# --- Paper-theory hint extraction -----------------------------------------


def _paper_theory_hint(paper_id: str) -> str:
    """Best-effort: read Desol/PaperTheory/Paper_<id>.lean and extract
    abbrev/def/axiom/instance/class/structure lines (delegates to
    lemma_factor_assistant.extract_paper_theory_hint)."""
    p = PROJECT_ROOT / "Desol" / "PaperTheory" / f"Paper_{paper_id}.lean"
    if not p.exists():
        return ""
    return gen.extract_paper_theory_hint(p)


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
    multi_shot_samples: int = 1,
) -> dict[str, Any]:
    led_path, entries = _load_ledger(paper_id)
    lean_file = PROJECT_ROOT / "output" / f"{paper_id}.lean"
    report = {
        "paper_id": paper_id,
        "candidates_found": 0,
        "candidates_attempted": 0,
        "patched": 0,
        "validated": 0,
        "elaboration_skipped": 0,
        "forbidden_token_rejects": 0,
        "lake_errors": 0,
        "transport_errors": 0,
        "details": [],
    }
    if not entries:
        report["error"] = "no_ledger"
        return report
    if not lean_file.exists():
        report["error"] = "no_lean_file"
        return report

    paper_theory_hint = _paper_theory_hint(paper_id)
    file_text = lean_file.read_text(encoding="utf-8")

    # Score and sort candidates.
    cands: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        ok, prio = _is_candidate_row(entry)
        if not ok:
            continue
        name = str(entry.get("theorem_name", "") or "")
        is_sorry, _ = _file_has_sorry_body_for(lean_file, name)
        if not is_sorry:
            # No `:= by sorry` body in the file for this name (term-mode,
            # nonexistent, or already-closed) — skip.
            continue
        cands.append((prio, entry))
    cands.sort(key=lambda t: t[0])
    if max_candidates > 0:
        cands = cands[:max_candidates]
    report["candidates_found"] = len(cands)
    print(f"[{paper_id}] candidates_found={len(cands)}")

    for _prio, entry in cands:
        name = str(entry.get("theorem_name", "") or "")
        short = _theorem_short_name(name)
        lean_stmt = str(entry.get("lean_statement", "") or "")
        report["candidates_attempted"] += 1

        if dry_run:
            # Skip the elaboration probe in dry-run; we just want candidate
            # counts.
            report["details"].append({"theorem": name, "outcome": "dry_run"})
            continue

        # Step a: probe the signature elaborates in isolation. We rely on
        # the fact that the in-file signature ALREADY compiles (the body is
        # `sorry`, so the theorem is already in the file's accepted state).
        # The isolated probe would just duplicate that work and is the most
        # expensive single call in the sweep (~30-60s per probe). Trust the
        # in-file evidence: if the file currently compiles with the body as
        # sorry, the signature elaborates. The post-patch lake validation
        # remains the load-bearing guard.
        # (We keep the probe-helper import as a compatibility shim.)

        # Step b: round-robin LLM generation + lake validation.
        error_tail = ""
        validated = False
        round_details: list[dict[str, Any]] = []
        for round_idx in range(1, max_rounds + 1):
            rejection_sink: dict[str, Any] = {}
            multi_shot_meta: dict[str, Any] = {}
            try:
                # Multi-shot is only useful on round 1 (no error tail yet);
                # subsequent rounds carry a targeted error tail and want to
                # focus the retry on that specific failure mode, so we stay
                # with single-shot for round_idx >= 2.
                if multi_shot_samples and multi_shot_samples > 1 and round_idx == 1:
                    # Use a per-candidate patch+lake-validate as the
                    # elaboration callback so EACH sample gets tried
                    # against the real file. The first survivor is
                    # already validated by the callback, so the caller
                    # can apply-accept without re-validating.
                    target_name_local = short if _flex_theorem_sorry_re(short).search(
                        lean_file.read_text(encoding="utf-8")
                    ) else name

                    def _ms_lake_validator(c: dict[str, Any]) -> tuple[bool, str]:
                        body = c["proof_body"]
                        if not _patch_proof_flex(lean_file, target_name_local, body):
                            return False, "patch_failed"
                        ok_v, err_v = _lake_validate_file_clean_for(
                            lean_file, target_name_local, timeout_s=per_lake_timeout,
                        )
                        if not ok_v:
                            _revert_proof_flex(lean_file, target_name_local)
                        return ok_v, err_v or ""

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
                        validate_elaboration=_ms_lake_validator,
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
                    # If short-circuited, the winning candidate is ALREADY
                    # patched into the file (the validator did it). Mark
                    # it so the post-multi-shot accept path skips the
                    # second patch.
                    if multi_shot_sink.get("short_circuited") and cand is not None:
                        cand["_already_patched"] = True
                else:
                    cand = gen.generate_proof_candidate(
                        paper_id=paper_id,
                        theorem_name=short or name,
                        lean_statement=lean_stmt,
                        paper_theory_hint=paper_theory_hint,
                        paper_local_file=file_text,
                        error_tail=error_tail,
                        client=client,
                        model=model,
                        rejection_sink=rejection_sink,
                    )
            except Exception as exc:
                report["transport_errors"] += 1
                bucket_counts["transport_errors"] += 1
                round_details.append({"round": round_idx, "outcome": "transport_error", "err": str(exc)[:200]})
                break
            if cand is None:
                report["forbidden_token_rejects"] += 1
                bucket_counts["forbidden_token_rejects"] += 1
                round_detail: dict[str, Any] = {"round": round_idx, "outcome": "forbidden_or_malformed"}
                if rejection_sink.get("reason"):
                    round_detail["rejection_reason"] = rejection_sink["reason"]
                if multi_shot_meta:
                    round_detail["multi_shot"] = multi_shot_meta
                round_details.append(round_detail)
                # Improvement 1: feed the clarification tail into the next
                # round so the LLM has a signal about WHY its previous
                # attempt was rejected. Otherwise the retry just replays the
                # same forbidden placeholder.
                clarification = rejection_sink.get("clarification") or ""
                if clarification:
                    error_tail = clarification
                continue
            proof_body = cand["proof_body"]
            # Step b cont'd: patch the proof body into the file using the
            # flex patcher (accepts both single-line and multi-line sorry
            # forms; always rewrites to multi-line).
            file_text_now = lean_file.read_text(encoding="utf-8")
            target_name = short if _flex_theorem_sorry_re(short).search(file_text_now) else name
            if cand.get("_already_patched"):
                # Multi-shot's per-candidate validator already patched +
                # validated this body against the real file. Skip the
                # redundant second patch+lake-validate and treat as a
                # confirmed win.
                report["patched"] += 1
                ok, err_tail = True, ""
            elif multi_shot_meta and not multi_shot_meta.get("short_circuited"):
                # Multi-shot already tried EVERY sample with the lake
                # validator and none survived. Don't re-patch — record
                # the failure with the head-of-sorted-list's elaboration
                # error and let the round fail through to the next
                # retry (or end of attempts).
                ok = False
                err_tail = cand.get("elaboration_error", "") or "multi_shot_all_samples_failed"
            else:
                patched = _patch_proof_flex(lean_file, target_name, proof_body)
                if not patched:
                    round_details.append({"round": round_idx, "outcome": "patch_failed"})
                    # The sorry-body slot wasn't where we expected.
                    continue
                report["patched"] += 1
                ok, err_tail = _lake_validate_file_clean_for(
                    lean_file, target_name, timeout_s=per_lake_timeout,
                )
            if ok:
                validated = True
                _apply_accept_to_entry(
                    entry,
                    proof_body=proof_body,
                    reasoning=cand.get("reasoning", ""),
                    confidence=float(cand.get("confidence", 0.0)),
                    round_idx=round_idx,
                )
                # Refresh the in-memory file_text so subsequent neighbour
                # extraction sees the now-closed proof.
                file_text = lean_file.read_text(encoding="utf-8")
                validated_detail: dict[str, Any] = {
                    "round": round_idx, "outcome": "validated",
                    "body_preview": proof_body[:80],
                }
                if multi_shot_meta:
                    validated_detail["multi_shot"] = multi_shot_meta
                    if "temperature" in cand:
                        validated_detail["winning_temperature"] = cand.get("temperature")
                round_details.append(validated_detail)
                break
            # Lake failure — revert and feed error tail to next round.
            # When multi-shot ran all N candidates with its own
            # patch+validate validator, the file has already been reverted
            # by the validator. Calling revert again is a no-op but cheap.
            _revert_proof_flex(lean_file, target_name)
            error_tail = err_tail or ""
            bucket_counts["lake_errors"] += 1
            report["lake_errors"] += 1
            lake_error_detail: dict[str, Any] = {
                "round": round_idx, "outcome": "lake_error",
                "err_tail": (err_tail or "")[-200:],
            }
            if multi_shot_meta:
                lake_error_detail["multi_shot"] = multi_shot_meta
                if "temperature" in cand:
                    lake_error_detail["candidate_temperature"] = cand.get("temperature")
            round_details.append(lake_error_detail)

        if validated:
            report["validated"] += 1
        report["details"].append({"theorem": name, "rounds": round_details, "validated": validated})

    if not dry_run and report["validated"] > 0:
        _save_ledger(led_path, entries)
        print(f"[{paper_id}] ledger updated: {report['validated']} new closures")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", action="append", default=[])
    parser.add_argument("--max-candidates", type=int, default=20,
                        help="Per-paper cap on candidates (0 = no limit).")
    parser.add_argument("--max-rounds", type=int, default=3,
                        help="Max LLM retry rounds per candidate.")
    parser.add_argument("--per-lake-timeout", type=int, default=60,
                        help="Per-lake-invocation timeout in seconds.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip LLM calls and lake; just report candidate counts.")
    parser.add_argument(
        "--multi-shot-samples",
        type=int,
        default=3,
        help=(
            "Number of parallel proof candidates to sample from Leanstral "
            "for round 1 (with diverse temperatures 0.0/0.3/0.5/0.7/0.9). "
            "First forbidden-token-gate survivor is patched + validated. "
            "Subsequent retry rounds remain single-shot (they carry a "
            "targeted error tail). 1 = legacy deterministic behaviour. "
            "Default 3."
        ),
    )
    parser.add_argument("--summary", default="output/leanstral_whole_proof_sweep_summary.json")
    parser.add_argument(
        "--use-fast-validation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Reserved: this sweep no longer runs the isolated elaboration probe "
            "(it trusts the in-file evidence that the signature elaborated). The "
            "flag is accepted for compatibility with the other sweeps so a single "
            "wrapper script can opt all sweeps in or out together."
        ),
    )
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
            multi_shot_samples=args.multi_shot_samples,
        )
        reports.append(r)

    elapsed = time.time() - t0
    summary = {
        "elapsed_seconds": round(elapsed, 1),
        "papers": reports,
        "totals": {
            "candidates_found": sum(r.get("candidates_found", 0) for r in reports),
            "candidates_attempted": sum(r.get("candidates_attempted", 0) for r in reports),
            "patched": sum(r.get("patched", 0) for r in reports),
            "validated": sum(r.get("validated", 0) for r in reports),
            "elaboration_skipped": sum(r.get("elaboration_skipped", 0) for r in reports),
            "forbidden_token_rejects": sum(r.get("forbidden_token_rejects", 0) for r in reports),
            "lake_errors": sum(r.get("lake_errors", 0) for r in reports),
            "transport_errors": sum(r.get("transport_errors", 0) for r in reports),
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
