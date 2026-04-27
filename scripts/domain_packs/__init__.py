from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DomainPack:
    name: str
    imports: list[str] = field(default_factory=list)
    open_scopes: list[str] = field(default_factory=list)
    # Token normalizations applied before validation/proof (Lean-side text rewrites).
    rewrites: dict[str, str] = field(default_factory=dict)
    # Short deterministic tactics worth trying for this domain.
    micro_tactics: list[str] = field(default_factory=list)


def get_domain_pack(domain: str) -> DomainPack:
    d = (domain or "").strip().lower()
    if d in {"probability", "prob", "stochastic"}:
        from .probability import PACK as _P
        return _P
    if d in {"analysis"}:
        from .analysis import PACK as _A
        return _A
    if d in {"pde", "analysis_pde", "partial_differential_equations"}:
        from .pde import PACK as _PDE
        return _PDE
    if d in {"spde", "stochastic_pde", "stochastic_partial_differential_equations"}:
        from .spde import PACK as _SPDE
        return _SPDE
    if d in {"number_theory", "nt", "arithmetic"}:
        from .number_theory import PACK as _NT
        return _NT
    if d in {"algebra", "group_theory"}:
        from .algebra import PACK as _G
        return _G
    if d in {"graph_theory", "graph"}:
        from .graph_theory import PACK as _GT
        return _GT
    if d in {"combinatorics"}:
        from .combinatorics import PACK as _C
        return _C
    # Default: minimal Mathlib + Aesop.
    return DomainPack(
        name=d or "default",
        imports=["Mathlib", "Aesop"],
        open_scopes=["MeasureTheory", "ProbabilityTheory", "Filter", "Set"],
        rewrites={},
        micro_tactics=["simp_all", "aesop", "tauto", "omega", "linarith", "nlinarith", "norm_num"],
    )

