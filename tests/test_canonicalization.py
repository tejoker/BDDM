from __future__ import annotations

from canonicalization import (
    build_manual_conflict_queue,
    canonical_claim_shape,
    canonical_theorem_id,
    canonicalize_lean_statement,
    cluster_near_duplicates,
)


def test_canonicalize_drops_theorem_name() -> None:
    s1 = "theorem foo (n : Nat) : n = n := by rfl"
    s2 = "theorem bar (n : Nat) : n = n := by exact rfl"
    c1 = canonicalize_lean_statement(s1)
    c2 = canonicalize_lean_statement(s2)
    assert c1 == c2
    assert "foo" not in c1
    assert "bar" not in c1


def test_canonical_id_stable_across_names() -> None:
    s1 = "theorem a1 (x : Nat) : x <= x := by exact Nat.le_refl x"
    s2 = "theorem a2 (x : Nat) : x <= x := by sorry"
    assert canonical_theorem_id(lean_statement=s1) == canonical_theorem_id(lean_statement=s2)


def test_claim_shape_detection() -> None:
    assert canonical_claim_shape("theorem t : 1 = 1 := by rfl") == "equality"
    assert canonical_claim_shape("theorem t : 1 <= 2 := by omega") == "inequality"


def test_alpha_equivalence_and_binder_order_normalized() -> None:
    s1 = "theorem foo (x : Nat) (y : Nat) : x = x := by rfl"
    s2 = "theorem bar (b : Nat) (a : Nat) : b = b := by rfl"
    assert canonicalize_lean_statement(s1) == canonicalize_lean_statement(s2)
    assert canonical_theorem_id(lean_statement=s1) == canonical_theorem_id(lean_statement=s2)


def test_near_duplicate_cluster_and_conflict_queue() -> None:
    nodes = [
        {
            "paper_id": "p1",
            "theorem_name": "t1",
            "canonical_theorem_id": "c1",
            "canonical_statement": "theorem _ : x + y = y + x",
            "claim_shape": "equality",
        },
        {
            "paper_id": "p2",
            "theorem_name": "t2",
            "canonical_theorem_id": "c2",
            "canonical_statement": "theorem _ : y + x = x + y",
            "claim_shape": "equality",
        },
    ]
    clusters = cluster_near_duplicates(nodes, min_jaccard=0.5)
    assert len(clusters) >= 1
    queue = build_manual_conflict_queue(nodes, min_jaccard=0.5)
    assert queue["items_total"] >= 1
