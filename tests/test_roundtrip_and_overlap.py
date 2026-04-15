import mcts_search
import statement_translator


def test_extract_proof_state_features_hypothesis_overlap():
    goals = [
        "n : Nat",
        "h_add : n + 0 = n",
        "⊢ n + 0 = n",
    ]
    feat = mcts_search.extract_proof_state_features(goals)
    assert feat.hypothesis_overlap_ratio > 0.3


def test_roundtrip_translation_check_flags_mismatch(monkeypatch):
    calls = {"count": 0}

    def fake_chat_complete(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return None, "n + 0 = n"
        return None, '{"equivalent": false, "notes": ["dropped domain"]}'

    monkeypatch.setattr(statement_translator, "_chat_complete", fake_chat_complete)

    back, flags = statement_translator.roundtrip_translation_check(
        latex_statement="For all n, n + 0 = n",
        lean_signature="theorem t (n : Nat) : n + 0 = n := by",
        client=object(),
        model="dummy",
    )
    assert back == "n + 0 = n"
    assert "roundtrip_semantic_mismatch" in flags
    assert "dropped domain" in flags
