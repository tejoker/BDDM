from __future__ import annotations

from kg_writer import _extract_relation_edges, canonical_relation_id


def test_extract_relation_edges_equivalent_and_implies() -> None:
    nodes = [
        {
            "paper_id": "p1",
            "theorem_name": "t1",
            "canonical_theorem_id": "cth_same",
            "canonical_statement": "theorem _ : x = x",
            "claim_shape": "equality",
            "evidence_id": "ev:p1|t1",
        },
        {
            "paper_id": "p2",
            "theorem_name": "t2",
            "canonical_theorem_id": "cth_same",
            "canonical_statement": "theorem _ : x = x",
            "claim_shape": "equality",
            "evidence_id": "ev:p2|t2",
        },
        {
            "paper_id": "p3",
            "theorem_name": "t3",
            "canonical_theorem_id": "cth_other",
            "canonical_statement": "theorem _ : x + y + z = x + y + z",
            "claim_shape": "equality",
            "evidence_id": "ev:p3|t3",
        },
    ]
    edges = _extract_relation_edges(nodes)
    edge_set = {
        (e["src_theorem"], e["dst_theorem"], e["edge_type"])
        for e in edges
    }
    assert ("p1|t1", "p2|t2", "equivalent_to") in edge_set
    assert ("p2|t2", "p1|t1", "equivalent_to") in edge_set
    eq = [
        e
        for e in edges
        if e["src_theorem"] == "p1|t1" and e["dst_theorem"] == "p2|t2" and e["edge_type"] == "equivalent_to"
    ][0]
    assert set(eq["evidence_ids"]) == {"ev:p1|t1", "ev:p2|t2"}
    assert eq["canonical_relation_id"] == canonical_relation_id(
        src="p1|t1",
        dst="p2|t2",
        edge_type="equivalent_to",
    )
