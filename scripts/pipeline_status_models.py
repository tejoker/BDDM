"""Shared status models and trust helpers for pipeline_status."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class VerificationStatus(str, Enum):
    FULLY_PROVEN = "FULLY_PROVEN"
    INTERMEDIARY_PROVEN = "INTERMEDIARY_PROVEN"
    AXIOM_BACKED = "AXIOM_BACKED"            # statement formalized; proof delegates to domain axiom not yet in Mathlib
    FLAWED = "FLAWED"
    UNRESOLVED = "UNRESOLVED"
    TRANSLATION_LIMITED = "TRANSLATION_LIMITED"  # key math types absent from Mathlib; excluded from proving rate denominator


# Which statuses count as "statement formalized" vs "proof closed".
# Use these everywhere instead of hard-coding strings so the distinction
# stays consistent across the pipeline.
STATUSES_STATEMENT_FORMALIZED: frozenset[VerificationStatus] = frozenset({
    VerificationStatus.FULLY_PROVEN,
    VerificationStatus.AXIOM_BACKED,
    VerificationStatus.INTERMEDIARY_PROVEN,
})
STATUSES_PROOF_CLOSED: frozenset[VerificationStatus] = frozenset({
    VerificationStatus.FULLY_PROVEN,
})


class ProofMethod(str, Enum):
    """How the proof closure was established.

    Only LEAN_VERIFIED counts toward the proving rate in quality metrics.
    AUTO_CLOSED and RECONCILE_PROMOTED are convenience promotions that are
    tracked separately and excluded from the verified-proof denominator.
    DOMAIN_AXIOM means the proof body calls a domain axiom that cannot be
    closed without a Mathlib library extension (honest IOU, not sorry).
    TRANSLATION_LIMITED is not a proof at all — it is an exclusion marker.
    """
    LEAN_VERIFIED = "lean_verified"          # lake build confirmed the proof
    AUTO_CLOSED = "auto_closed"              # trivial tactic applied without lake re-check
    RECONCILE_PROMOTED = "reconcile_promoted"  # promoted by file-pattern scan post-hoc
    DOMAIN_AXIOM = "domain_axiom"            # proof body calls an unformalized domain axiom
    TRANSLATION_LIMITED = "translation_limited"  # statement excluded from proving scope
    UNKNOWN = "unknown"                      # legacy / pre-field entries


class StepVerdict(str, Enum):
    VERIFIED = "VERIFIED"
    FLAWED = "FLAWED"
    INCOMPLETE = "INCOMPLETE"


class FailureOrigin(str, Enum):
    NOT_FAILED = "NOT_FAILED"
    FORMALIZATION_ERROR = "FORMALIZATION_ERROR"
    PROOF_SEARCH_ERROR = "PROOF_SEARCH_ERROR"
    POSSIBLY_FALSE_STATEMENT = "POSSIBLY_FALSE_STATEMENT"
    UNKNOWN = "UNKNOWN"


class FailureKind(str, Enum):
    EXTRACTION_FAILURE = "extraction_failure"
    TRANSLATION_FAILURE = "translation_failure"
    ELABORATION_FAILURE = "elaboration_failure"
    MISSING_DEFINITION = "missing_definition"
    MISSING_MATHLIB_THEOREM = "missing_mathlib_theorem"
    PROOF_SEARCH_FAILURE = "proof_search_failure"
    FALSE_OR_AMBIGUOUS_STATEMENT = "false_or_ambiguous_statement"
    DOMAIN_AXIOM_DEBT = "domain_axiom_debt"
    IMPORT_MISMATCH = "import_mismatch"
    UNKNOWN = "unknown"


class GroundingStatus(str, Enum):
    GROUNDED_MATHLIB = "GROUNDED_MATHLIB"
    GROUNDED_INTERNAL_KG = "GROUNDED_INTERNAL_KG"
    GROUNDED_EXTERNAL_PAPER = "GROUNDED_EXTERNAL_PAPER"
    UNGROUNDED = "UNGROUNDED"
    UNKNOWN = "UNKNOWN"


class TrustClass(str, Enum):
    TRUST_MATHLIB = "TRUST_MATHLIB"
    TRUST_EXTERNAL_FORMAL_LIB = "TRUST_EXTERNAL_FORMAL_LIB"
    TRUST_INTERNAL_PROVED = "TRUST_INTERNAL_PROVED"
    TRUST_PLACEHOLDER = "TRUST_PLACEHOLDER"


class ClaimEquivalenceVerdict(str, Enum):
    EQUIVALENT = "equivalent"
    STRONGER = "stronger"
    WEAKER = "weaker"
    UNCLEAR = "unclear"


@dataclass
class SemanticEquivalenceArtifact:
    """Auditable link between the paper theorem and generated Lean statement."""

    original_latex_theorem: str = ""
    normalized_natural_language_theorem: str = ""
    lean_statement: str = ""
    extracted_assumptions: list[str] = field(default_factory=list)
    extracted_conclusion: str = ""
    equivalence_verdict: ClaimEquivalenceVerdict = ClaimEquivalenceVerdict.UNCLEAR
    reviewer_evaluator_evidence: list[str] = field(default_factory=list)
    adversarial_checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    independent_semantic_evidence: bool = False
    schema_version: str = "1.0"


@dataclass
class StatusDecision:
    """Legacy simple classifier result — kept for backward compatibility."""

    verification_status: VerificationStatus
    grounding_status: GroundingStatus
    reason: str


@dataclass
class StepObligation:
    """One step in a proof attempt with its verification outcome."""

    step_index: int
    tactic: str
    result: str
    detail: str = ""
    verified: bool = False


@dataclass
class Assumption:
    """A single assumption extracted from a theorem statement or proof context."""

    label: str
    lean_expr: str
    grounding: GroundingStatus = GroundingStatus.UNGROUNDED
    grounding_source: str = ""
    trust_class: TrustClass = TrustClass.TRUST_PLACEHOLDER
    trust_reference: str = ""


@dataclass
class ProvenanceLink:
    paper_id: str
    section: str = ""
    label: str = ""
    cited_refs: list[str] = field(default_factory=list)


@dataclass
class TheoremLedgerEntry:
    """Full verification record for one theorem."""

    theorem_name: str
    lean_file: str
    lean_statement: str
    status: VerificationStatus
    step_verdict: StepVerdict = StepVerdict.INCOMPLETE
    failure_origin: FailureOrigin = FailureOrigin.UNKNOWN
    failure_kind: FailureKind = FailureKind.UNKNOWN
    trust_class: TrustClass = TrustClass.TRUST_PLACEHOLDER
    trust_reference: str = ""
    promotion_gate_passed: bool = False
    step_obligations: list[StepObligation] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    provenance: ProvenanceLink | None = None
    proof_text: str = ""
    first_failing_step: int = -1
    error_message: str = ""
    proof_mode: str = "full-draft"
    proof_method: ProofMethod = ProofMethod.UNKNOWN
    rounds_used: int = 0
    time_s: float = 0.0
    timestamp: str = ""
    validation_gates: dict[str, bool] = field(default_factory=dict)
    gate_failures: list[str] = field(default_factory=list)
    claim_equivalence_verdict: ClaimEquivalenceVerdict = ClaimEquivalenceVerdict.UNCLEAR
    claim_equivalence_notes: list[str] = field(default_factory=list)
    semantic_equivalence_artifact: SemanticEquivalenceArtifact = field(
        default_factory=SemanticEquivalenceArtifact
    )
    review_required: bool = False
    review_queue_id: str = ""
    context_pack: dict[str, Any] = field(default_factory=dict)
    axiom_debt: list[str] = field(default_factory=list)
    axiom_debt_hash: str = ""
    closure_claim: str = "unverified"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["step_verdict"] = self.step_verdict.value
        d["failure_origin"] = self.failure_origin.value
        d["failure_kind"] = self.failure_kind.value
        d["trust_class"] = self.trust_class.value
        d["proof_method"] = self.proof_method.value
        d["claim_equivalence_verdict"] = self.claim_equivalence_verdict.value
        artifact = d.get("semantic_equivalence_artifact")
        if isinstance(artifact, dict):
            verdict = artifact.get("equivalence_verdict")
            if not isinstance(verdict, str) and hasattr(verdict, "value"):
                artifact["equivalence_verdict"] = verdict.value
        for a in d["assumptions"]:
            if not isinstance(a["grounding"], str):
                a["grounding"] = a["grounding"].value
            if not isinstance(a["trust_class"], str):
                a["trust_class"] = a["trust_class"].value
        return d


def trust_for_grounding(grounding: GroundingStatus) -> tuple[TrustClass, str]:
    if grounding == GroundingStatus.GROUNDED_MATHLIB:
        return TrustClass.TRUST_MATHLIB, "mathlib"
    if grounding == GroundingStatus.GROUNDED_INTERNAL_KG:
        return TrustClass.TRUST_INTERNAL_PROVED, "internal_kg"
    if grounding == GroundingStatus.GROUNDED_EXTERNAL_PAPER:
        return TrustClass.TRUST_EXTERNAL_FORMAL_LIB, "external_formal_or_cited"
    return TrustClass.TRUST_PLACEHOLDER, "untrusted"


def derive_theorem_trust(
    *,
    assumptions: list[Assumption],
    status: VerificationStatus,
) -> tuple[TrustClass, str, bool]:
    if status in {VerificationStatus.FLAWED, VerificationStatus.UNRESOLVED}:
        return TrustClass.TRUST_PLACEHOLDER, "theorem_not_verified", False

    if assumptions and all(a.trust_class == TrustClass.TRUST_MATHLIB for a in assumptions):
        promotion_ok = status == VerificationStatus.FULLY_PROVEN
        return TrustClass.TRUST_MATHLIB, "all_assumptions_mathlib", promotion_ok

    if assumptions and any(a.trust_class == TrustClass.TRUST_EXTERNAL_FORMAL_LIB for a in assumptions):
        promotion_ok = status == VerificationStatus.FULLY_PROVEN
        return TrustClass.TRUST_EXTERNAL_FORMAL_LIB, "contains_external_sources", promotion_ok

    promotion_ok = status == VerificationStatus.FULLY_PROVEN
    return TrustClass.TRUST_INTERNAL_PROVED, "internal_verified_pipeline", promotion_ok


def all_assumptions_grounded(assumptions: list[Assumption]) -> bool:
    if not assumptions:
        return True
    return all(
        a.grounding
        in {
            GroundingStatus.GROUNDED_MATHLIB,
            GroundingStatus.GROUNDED_INTERNAL_KG,
            GroundingStatus.GROUNDED_EXTERNAL_PAPER,
        }
        for a in assumptions
    )
