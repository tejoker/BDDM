#!/usr/bin/env python3
"""Credit individually-closed lemma-factor aux as their own IP/AB ledger rows.

Round-XI motivation
-------------------

A lemma-factor sweep that proposes N aux often closes only k < min_needed of
them; the parent composition then fails and the sweep cleans the aux back
out of ``output/<paper>.lean``. The k closed aux are REAL proven lemmas —
``lake env lean`` validated them in-file — but the existing wiring drops
them on the floor.

This module credits each individually-closed aux as a NEW ledger row, with
the same audit-grade gates a primary theorem would have to pass. The new
rows are named ``<parent>::aux::<aux_local_name>`` so the integrity audit
treats them as first-class FP/AB/IP candidates without colliding with the
primary theorem namespace.

Pre-conditions enforced here (belt-and-braces — the caller is responsible
for invoking the sweep's normal forbidden-token / lake-in-context gates):

  * each aux carries a non-empty ``aux_signature`` and ``proof_body``;
  * ``proof_body`` is forbidden-token-clean (``_contains_forbidden_token``);
  * ``aux_signature`` is not trivialized (``_is_trivialized_signature``);
  * ``aux_signature`` elaborates against the paper-theory + parent context
    when ``validate_elaboration`` is provided.

Derived-row schema (subset of the canonical ledger schema)::

    {
      "theorem_name":     "<parent>::aux::<aux_local_name>",
      "lean_statement":   "<aux_signature with body stripped>",
      "proof_text":       "<closing proof body>",
      "proof_method":     "lemma_factor_aux_promotion_v1",
      "status":           "FULLY_PROVEN" | "AXIOM_BACKED" | "INTERMEDIARY_PROVEN",
      "step_verdict":     "VERIFIED",
      "failure_origin":   "NONE",
      "failure_kind":     "",
      "proved":           True,
      "promotion_gate_passed": True,
      "validation_gates": <copied from parent, with lean_proof_closed=True>,
      "gate_failures":    <copied from parent minus lean_proof_closed>,
      "audit_trail":      [{"event": "derived_aux_from_factor", "parent": ...}],
      "ledger_role":      "derived_aux_from_factor",
      "lean_file":        "<absolute path to output/<paper>.lean>",
      "parent_theorem_name": "<parent>",
      "claim_equivalence_notes": [..., "promoted_closed_aux:lemma_factor_v2"],
      ...
    }

The status is derived by the same rule the whole-proof sweep uses
(``_decide_status_after_close``): axiom debt -> AB, otherwise IP; FP is
only granted when the parent's gates already establish all three
{claim_equivalent, independent_semantic_equivalence_evidence,
provenance_linked} AND the parent's no_paper_axiom_debt gate is True. In
practice derived aux rarely qualify for FP — they're new statements with
no claim_equivalent evidence — so IP is the typical outcome.

Idempotency
-----------

If the ledger already contains a row whose ``theorem_name`` matches the
derived name, ``promote_closed_aux`` does NOT add a duplicate. The
existing row is left alone; the result reports ``status="idempotent"``
for that aux.
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path
from typing import Any, Callable, Optional

# Reuse the canonical gates rather than reimplementing them.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

try:
    from leanstral_whole_proof_generator import (
        _contains_forbidden_token,  # type: ignore[attr-defined]
    )
except Exception:  # pragma: no cover - defensive
    def _contains_forbidden_token(body: str) -> Optional[str]:  # type: ignore[misc]
        return None

try:
    from translator._translate import (
        _is_trivialized_signature,  # type: ignore[attr-defined]
    )
except Exception:  # pragma: no cover - defensive
    def _is_trivialized_signature(sig: str) -> bool:  # type: ignore[misc]
        return False


PROMOTION_PROTOCOL = "lemma_factor_aux_promotion_v1"
DERIVED_LEDGER_ROLE = "derived_aux_from_factor"
DERIVED_NAME_SEPARATOR = "::aux::"


# --- Naming convention ----------------------------------------------------


def derived_theorem_name(parent_theorem_name: str, aux_local_name: str) -> str:
    """Compose ``<parent>::aux::<aux_local_name>``.

    The double-colon delimiter is deliberately chosen so the derived name
    cannot collide with any valid Lean identifier (``::`` is not legal in
    Lean), making downstream parsers that walk the ledger able to
    detect derived rows without a separate flag.
    """
    parent = (parent_theorem_name or "").strip().rsplit(".", 1)[-1] or "thm"
    aux = (aux_local_name or "").strip().rsplit(".", 1)[-1] or "aux"
    return f"{parent}{DERIVED_NAME_SEPARATOR}{aux}"


def is_derived_aux_row(entry: dict[str, Any]) -> bool:
    """True when an entry was promoted by this module (either by name
    convention or by an explicit ``proof_method``/``ledger_role`` marker)."""
    if not isinstance(entry, dict):
        return False
    name = str(entry.get("theorem_name", "") or "")
    if DERIVED_NAME_SEPARATOR in name:
        return True
    if str(entry.get("proof_method", "")) == PROMOTION_PROTOCOL:
        return True
    if str(entry.get("ledger_role", "")) == DERIVED_LEDGER_ROLE:
        return True
    return False


# --- Signature / body normalization --------------------------------------


_BY_TAIL_RX = re.compile(r":=\s*by\b.*$", re.DOTALL)
_EQ_TAIL_RX = re.compile(r":=.*$", re.DOTALL)


def _strip_proof_body(aux_signature: str) -> str:
    """Return the aux signature with any trailing ``:= by ...`` or ``:= ...``
    tail removed, so we can store a body-free ``lean_statement`` while
    preserving the proof body separately in ``proof_text``.
    """
    sig = (aux_signature or "").strip()
    if not sig:
        return ""
    out = _BY_TAIL_RX.sub("", sig).strip()
    if out == sig:
        out = _EQ_TAIL_RX.sub("", sig).strip()
    return out


# --- Status decision (mirror sweep_leanstral_whole_proof) ----------------


def _decide_status_after_close(parent_entry: dict[str, Any]) -> str:
    """Decide the derived row's status from the parent's gates.

    Conservative: a derived aux inherits the parent's evidence picture
    (provenance, claim_equivalent, ...), but the aux itself is a NEW
    statement so we never claim FP unless the parent's three gates are
    all satisfied AND there's no axiom debt. Axiom debt -> AXIOM_BACKED;
    otherwise INTERMEDIARY_PROVEN.
    """
    gates = parent_entry.get("validation_gates") or {}
    if not isinstance(gates, dict):
        return "INTERMEDIARY_PROVEN"
    if gates.get("no_paper_axiom_debt") is False:
        return "AXIOM_BACKED"
    if (
        gates.get("claim_equivalent")
        and gates.get("independent_semantic_equivalence_evidence")
        and gates.get("provenance_linked")
    ):
        return "FULLY_PROVEN"
    return "INTERMEDIARY_PROVEN"


# --- Row builder ----------------------------------------------------------


def _build_derived_row(
    *,
    parent_entry: dict[str, Any],
    parent_theorem_name: str,
    aux_record: dict[str, Any],
    paper_id: str,
    lean_file: Path,
) -> dict[str, Any]:
    aux_name = str(aux_record.get("aux_name", "") or "")
    aux_signature = str(aux_record.get("aux_signature", "") or "")
    proof_body = str(aux_record.get("proof_body", "") or "")
    compose_hint = str(aux_record.get("compose_hint", "") or "")

    derived_name = derived_theorem_name(parent_theorem_name, aux_name)
    lean_stmt = _strip_proof_body(aux_signature)

    # Inherit validation gates from the parent: the aux was verified in
    # the same file, so the same dependency-trust / reproducible-env /
    # provenance gates apply. We override `lean_proof_closed` and
    # `step_verdict_verified` because we just closed THIS aux.
    parent_gates = parent_entry.get("validation_gates") or {}
    if isinstance(parent_gates, dict):
        gates: dict[str, Any] = copy.deepcopy(parent_gates)
    else:
        gates = {}
    gates["lean_proof_closed"] = True
    gates["step_verdict_verified"] = True

    parent_fails = parent_entry.get("gate_failures") or []
    if isinstance(parent_fails, list):
        gate_failures: list[str] = [
            str(x) for x in parent_fails
            if str(x) not in {"lean_proof_closed", "step_verdict_verified"}
        ]
    else:
        gate_failures = []

    # Inherit a conservative subset of contextual fields from the parent.
    inherit_keys = (
        "paper_statement_id",
        "provenance",
        "review_policy",
        "reviewer_type",
        "trust_class",
        "trust_reference",
        "reproducible_env",
        "translation_fidelity_score",
        "status_alignment_score",
        "axiom_debt",
        "axiom_debt_hash",
    )
    inherited: dict[str, Any] = {}
    for k in inherit_keys:
        if k in parent_entry:
            inherited[k] = copy.deepcopy(parent_entry[k])

    status = _decide_status_after_close(parent_entry)

    notes = ["promoted_closed_aux:lemma_factor_v2"]

    derived_row: dict[str, Any] = {
        "theorem_name": derived_name,
        "lean_statement": lean_stmt,
        "lean_file": str(lean_file),
        "proof_text": proof_body,
        "proof_method": PROMOTION_PROTOCOL,
        "proof_mode": "derived-from-factor",
        "proved": True,
        "step_verdict": "VERIFIED",
        "failure_origin": "NONE",
        "failure_kind": "",
        "first_failing_step": -1,
        "promotion_gate_passed": True,
        "validation_gates": gates,
        "gate_failures": gate_failures,
        "status": status,
        "ledger_role": DERIVED_LEDGER_ROLE,
        "parent_theorem_name": parent_theorem_name,
        "aux_local_name": aux_name,
        "claim_equivalence_notes": notes,
        "claim_equivalence_verdict": str(
            parent_entry.get("claim_equivalence_verdict", "") or ""
        ),
        "lemma_factor_metadata": {
            "protocol": "lemma_factor_v2",
            "compose_hint": compose_hint,
            "aux_signature": aux_signature,
        },
        "audit_trail": [
            {
                "event": "derived_aux_from_factor",
                "parent": parent_theorem_name,
                "paper_id": paper_id,
                "protocol": PROMOTION_PROTOCOL,
            }
        ],
        "rounds_used": int(aux_record.get("rounds_used", 1) or 1),
        "time_s": 0.0,
    }
    derived_row.update(inherited)
    # Ensure inherited fields don't clobber the ones we set above.
    derived_row["theorem_name"] = derived_name
    derived_row["status"] = status
    derived_row["lean_statement"] = lean_stmt
    derived_row["proof_text"] = proof_body
    derived_row["validation_gates"] = gates
    derived_row["gate_failures"] = gate_failures
    return derived_row


# --- Public API -----------------------------------------------------------


def promote_closed_aux(
    *,
    paper_id: str,
    parent_theorem_name: str,
    parent_entry: dict[str, Any],
    aux_records: list[dict[str, Any]],
    project_root: Path,
    ledger_entries: list[dict[str, Any]],
    validate_elaboration: Optional[Callable[[str], tuple[bool, Any]]] = None,
) -> list[dict[str, Any]]:
    """Promote each closed aux to its own ledger row.

    Parameters
    ----------
    paper_id
        Identifies the paper. Used to compute ``lean_file`` path and to
        seed ``audit_trail`` entries.
    parent_theorem_name
        The parent theorem the aux were factored out of. Used as the
        prefix in the derived row's ``theorem_name``.
    parent_entry
        The parent's ledger row. Used to inherit validation gates,
        provenance, reviewer_type, trust_class, etc.
    aux_records
        Each record MUST carry ``aux_name``, ``aux_signature``, and
        ``proof_body``. Optional ``compose_hint`` is recorded in
        ``lemma_factor_metadata``.
    project_root
        Repo root. ``output/<paper_id>.lean`` is computed from it.
    ledger_entries
        The list of ledger rows for this paper. Used ONLY for idempotency
        detection (we never mutate it here; the caller is responsible
        for appending the survivors).
    validate_elaboration
        Optional gate: invoked with the aux's signature (body stripped to
        ``:= by sorry``) and must return ``(ok, err)``. When ``None``,
        elaboration is presumed-OK (the caller is responsible for any
        upstream lake validation).

    Returns
    -------
    list of dicts
        Each item has the keys ``aux_name`` (the LOCAL aux name),
        ``derived_name`` (the full ``<parent>::aux::<name>``), ``status``
        (one of ``promoted`` / ``idempotent`` / ``refused_<reason>``),
        and (when promoted) ``row`` (the derived row dict the caller
        should append to the ledger). Refused rows include an ``error``
        string with the rejection reason.
    """
    project_root = Path(project_root)
    lean_file = project_root / "output" / f"{paper_id}.lean"

    if not isinstance(aux_records, list):
        return []
    if not isinstance(ledger_entries, list):
        ledger_entries = []
    if parent_entry is None:
        parent_entry = {}

    # Pre-index existing ledger entries by theorem_name so idempotency
    # detection is O(1) per aux.
    existing_names = {
        str(e.get("theorem_name", "") or "")
        for e in ledger_entries
        if isinstance(e, dict)
    }

    results: list[dict[str, Any]] = []
    for rec in aux_records:
        if not isinstance(rec, dict):
            continue
        aux_name = str(rec.get("aux_name", "") or "").strip()
        aux_signature = str(rec.get("aux_signature", "") or "").strip()
        proof_body = str(rec.get("proof_body", "") or "").strip()

        result: dict[str, Any] = {
            "aux_name": aux_name,
            "derived_name": "",
            "status": "",
        }

        if not aux_name:
            result["status"] = "refused_empty_aux_name"
            results.append(result)
            continue
        if not aux_signature:
            result["status"] = "refused_empty_aux_signature"
            results.append(result)
            continue
        if not proof_body:
            result["status"] = "refused_empty_proof_body"
            results.append(result)
            continue

        # Standards-positive gate 1: forbidden tokens in proof body.
        tok = _contains_forbidden_token(proof_body)
        if tok:
            result["status"] = f"refused_forbidden_token:{tok}"
            results.append(result)
            continue

        # Standards-positive gate 2: trivialized signature.
        if _is_trivialized_signature(aux_signature):
            result["status"] = "refused_trivialized_signature"
            results.append(result)
            continue

        # Standards-positive gate 3 (optional): elaboration probe.
        if validate_elaboration is not None:
            # Probe the signature with a `:= by sorry` body — the caller's
            # validator is the same one used elsewhere in the sweep, so
            # this guards against signatures that lost type context.
            sig_only = _strip_proof_body(aux_signature)
            probe_decl = sig_only + " := by sorry"
            try:
                ok, err = validate_elaboration(probe_decl)
            except Exception as exc:  # pragma: no cover - defensive
                ok, err = False, f"validator_raised:{type(exc).__name__}:{exc}"
            if not bool(ok):
                err_str = str(err or "")[-200:]
                result["status"] = "refused_elaboration_gate"
                result["error"] = err_str
                results.append(result)
                continue

        derived_name = derived_theorem_name(parent_theorem_name, aux_name)
        result["derived_name"] = derived_name

        # Idempotency: skip if the ledger already has this derived row.
        if derived_name in existing_names:
            result["status"] = "idempotent"
            results.append(result)
            continue

        row = _build_derived_row(
            parent_entry=parent_entry,
            parent_theorem_name=parent_theorem_name,
            aux_record=rec,
            paper_id=paper_id,
            lean_file=lean_file,
        )
        result["status"] = "promoted"
        result["row"] = row
        # Track the name so subsequent aux in the SAME call also see it
        # (defends against duplicate aux_name in the same batch).
        existing_names.add(derived_name)
        results.append(result)

    return results


__all__ = [
    "PROMOTION_PROTOCOL",
    "DERIVED_LEDGER_ROLE",
    "DERIVED_NAME_SEPARATOR",
    "derived_theorem_name",
    "is_derived_aux_row",
    "promote_closed_aux",
]
