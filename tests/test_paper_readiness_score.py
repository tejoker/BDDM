from __future__ import annotations

import json
from pathlib import Path

from paper_readiness_score import score_readiness


def test_readiness_score_basic(tmp_path: Path) -> None:
    ledger_root = tmp_path / "ledgers"
    ledger_root.mkdir(parents=True)
    rows = {
        "entries": [
            {
                "theorem_name": "t_good",
                "lean_statement": "theorem t_good : 1 = 1",
                "validation_gates": {
                    "translation_fidelity_ok": True,
                    "assumptions_grounded": True,
                    "dependency_trust_complete": True,
                },
                "assumptions": [
                    {"trust_class": "TRUST_MATHLIB", "grounding": "GROUNDED_MATHLIB"},
                ],
            },
            {
                "theorem_name": "t_bad",
                "lean_statement": "theorem literal_schema_translation : Prop := by sorry",
                "validation_gates": {
                    "translation_fidelity_ok": False,
                    "assumptions_grounded": False,
                    "dependency_trust_complete": False,
                },
                "assumptions": [
                    {"trust_class": "TRUST_PLACEHOLDER", "grounding": "UNGROUNDED"},
                ],
            },
        ]
    }
    (ledger_root / "2304.00001.json").write_text(json.dumps(rows), encoding="utf-8")
    payload = score_readiness(paper_id="2304.00001", ledger_root=ledger_root)
    assert payload["total_theorems"] == 2
    assert 0.0 <= payload["readiness_score"] <= 1.0
    assert payload["readiness_class"] in {"A", "B", "C"}

