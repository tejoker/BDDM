from __future__ import annotations

from domain_packs import get_domain_pack


def test_recurring_domain_packs_are_registered() -> None:
    expected = {
        "spde": "spde",
        "pde": "pde",
        "probability": "probability",
        "graph_theory": "graph_theory",
        "combinatorics": "combinatorics",
        "number_theory": "number_theory",
    }
    for query, name in expected.items():
        pack = get_domain_pack(query)
        assert pack.name == name
        assert "Mathlib" in pack.imports
        assert pack.micro_tactics


def test_domain_pack_aliases_route_to_specific_packs() -> None:
    assert get_domain_pack("analysis_pde").name == "pde"
    assert get_domain_pack("stochastic_pde").name == "spde"
    assert get_domain_pack("nt").name == "number_theory"
