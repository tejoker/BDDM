from pathlib import Path

from distributed_proof_cache import DistributedProofCache


def test_distributed_proof_cache_roundtrip(tmp_path: Path):
    db = tmp_path / "proof_cache.sqlite"
    cache = DistributedProofCache(db)

    key = cache.build_key(
        theorem_statement="theorem t : True := by",
        mode="state-mcts",
        model="m",
        retrieval_top_k=12,
    )
    assert cache.get(key) is None

    payload = {"success": True, "proof": "trivial", "error": ""}
    cache.set(key, payload)
    got = cache.get(key)

    assert got is not None
    assert got["success"] is True
    assert got["proof"] == "trivial"

    stats = cache.stats()
    assert stats["entries"] == 1
