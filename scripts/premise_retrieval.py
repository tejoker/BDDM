#!/usr/bin/env python3
"""Premise retrieval index for Lean goals.

This module provides a light-weight embedding pipeline suitable for offline use.
It can build an index from TOON inventory files and retrieve top-k premises for
prompt injection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    from sentence_transformers import SentenceTransformer as _ST
    _HAS_ST = True
except ImportError:
    _HAS_ST = False


TOKEN_RE = re.compile(r"[A-Za-z0-9_'.]+")

# Short tokens and ubiquitous Lean keywords that carry no discriminative signal.
_STOPWORDS: frozenset[str] = frozenset({
    "fun", "let", "have", "show", "this", "exact", "apply", "rfl", "simp",
    "intro", "intros", "true", "false", "type", "prop", "sort",
    "inst", "hf", "hg", "h0", "h1", "h2", "h3", "h4", "h5",
    "def", "theorem", "lemma", "instance", "class", "structure",
    "where", "with", "from", "and", "or", "not", "if", "then", "else",
    "return", "do", "pure", "bind",
})
_MIN_TOKEN_LEN = 4

# Default sentence-transformers model: small (80 MB), 384-dim, strong on technical text.
_DEFAULT_ST_MODEL = "all-MiniLM-L6-v2"

# Module-level cache so the model loads once per process.
_ST_MODEL_CACHE: dict[str, Any] = {}


@runtime_checkable
class Encoder(Protocol):
    """Minimal interface for a text encoder."""
    def encode(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class PremiseEntry:
    name: str
    statement: str
    namespace: str = ""
    source_file: str = ""


@dataclass
class RetrievalHit:
    name: str
    statement: str
    namespace: str
    score: float
    trust_tier: str = "unknown"  # "trusted", "conditional", "diagnostics", "unknown"


# ---------------------------------------------------------------------------
# Hash-based encoder (zero dependencies, kept as fallback)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return [
        t.lower()
        for t in TOKEN_RE.findall(text)
        if len(t) >= _MIN_TOKEN_LEN and t.lower() not in _STOPWORDS
    ]


def _hash_index(token: str, dims: int) -> int:
    h = hashlib.sha1(token.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % dims


def _embed_hash(text: str, dims: int) -> list[float]:
    vec = [0.0] * dims
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        idx = _hash_index(tok, dims)
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


# Keep old name as alias so callers that import _embed still work.
def _embed(text: str, dims: int) -> list[float]:
    return _embed_hash(text, dims)


# ---------------------------------------------------------------------------
# Sentence-transformers encoder (learned, semantic)
# ---------------------------------------------------------------------------

class _STEncoder:
    """Thin wrapper around a SentenceTransformer model."""

    def __init__(self, model_name: str = _DEFAULT_ST_MODEL) -> None:
        if not _HAS_ST:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            )
        global _ST_MODEL_CACHE
        if model_name not in _ST_MODEL_CACHE:
            print(f"[premise_retrieval] loading sentence-transformer '{model_name}' ...")
            _ST_MODEL_CACHE[model_name] = _ST(model_name)
        self._model = _ST_MODEL_CACHE[model_name]
        self.dims: int = self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = self._model.encode(texts, batch_size=64, show_progress_bar=False)
        # Normalize to unit vectors for cosine similarity via dot product.
        result: list[list[float]] = []
        for v in vecs:
            arr = list(float(x) for x in v)
            norm = math.sqrt(sum(x * x for x in arr))
            if norm > 1e-9:
                arr = [x / norm for x in arr]
            result.append(arr)
        return result


def get_st_encoder(model_name: str = _DEFAULT_ST_MODEL) -> _STEncoder:
    """Return a (cached) sentence-transformer encoder."""
    return _STEncoder(model_name)


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def parse_toon_nodes(path: str | Path) -> list[PremiseEntry]:
    """Parse `nodes[...]` rows from TOON inventory.

    Expected columns: name,status,namespace,file,notes
    """
    toon_path = Path(path)
    if not toon_path.exists():
        raise FileNotFoundError(f"toon file not found: {toon_path}")

    lines = toon_path.read_text(encoding="utf-8").splitlines()
    in_nodes = False
    rows: list[PremiseEntry] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("nodes["):
            in_nodes = True
            continue
        if in_nodes and line.startswith("beachheads["):
            break
        if not in_nodes or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(",", 4)]
        if len(parts) < 5:
            continue

        name, status, namespace, source_file, notes = parts
        if status.lower() != "exists":
            continue

        statement = notes.strip() or name
        rows.append(
            PremiseEntry(
                name=name,
                statement=statement,
                namespace=namespace,
                source_file=source_file,
            )
        )
    return rows


class PremiseRetriever:
    def __init__(
        self,
        *,
        entries: list[PremiseEntry],
        embeddings: list[list[float]],
        dims: int,
        encoder_name: str = "hash",
    ):
        self.entries = entries
        self.embeddings = embeddings
        self.dims = dims
        # Remember which encoder was used so query vectors match index vectors.
        self.encoder_name = encoder_name
        self._st_encoder: _STEncoder | None = None
        if encoder_name != "hash":
            try:
                self._st_encoder = get_st_encoder(encoder_name)
            except ImportError:
                pass

    @classmethod
    def build(
        cls,
        entries: list[PremiseEntry],
        dims: int = 384,
        encoder_name: str | None = None,
    ) -> PremiseRetriever:
        """Build a retrieval index.

        Args:
            entries: Premise corpus.
            dims: Embedding dimension. Ignored when a sentence-transformer
                encoder is used (the model determines its own dimension).
            encoder_name: Name of the sentence-transformers model to use, or
                ``None`` to auto-select (sentence-transformers if installed,
                otherwise hash). Pass ``"hash"`` to force hash embeddings.
        """
        if encoder_name is None:
            encoder_name = _DEFAULT_ST_MODEL if _HAS_ST else "hash"

        embeddings: list[list[float]] = []
        if encoder_name == "hash":
            for e in entries:
                text = f"{e.name} {e.namespace} {e.statement}"
                embeddings.append(_embed_hash(text, dims))
            return cls(entries=entries, embeddings=embeddings, dims=dims, encoder_name="hash")

        # Sentence-transformer path.
        st = get_st_encoder(encoder_name)
        actual_dims = st.dims
        texts = [f"{e.name} {e.namespace} {e.statement}" for e in entries]
        embeddings = st.encode(texts)
        return cls(
            entries=entries,
            embeddings=embeddings,
            dims=actual_dims,
            encoder_name=encoder_name,
        )

    @classmethod
    def load(cls, index_path: str | Path) -> PremiseRetriever:
        path = Path(index_path)
        if path.is_dir():
            return cls.load_np(path)
        payload = json.loads(path.read_text(encoding="utf-8"))

        entries: list[PremiseEntry] = []
        for row in payload["entries"]:
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            statement = str(row.get("statement") or row.get("notes") or name).strip()
            namespace = str(row.get("namespace", "")).strip()
            source_file = str(row.get("source_file") or row.get("file") or "").strip()
            entries.append(
                PremiseEntry(
                    name=name,
                    statement=statement,
                    namespace=namespace,
                    source_file=source_file,
                )
            )

        embeddings = payload["embeddings"]
        dims = int(payload["dims"])
        encoder_name = str(payload.get("encoder_name", "hash"))
        return cls(entries=entries, embeddings=embeddings, dims=dims, encoder_name=encoder_name)

    def save(self, index_path: str | Path) -> None:
        path = Path(index_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dims": self.dims,
            "encoder_name": self.encoder_name,
            "entries": [asdict(e) for e in self.entries],
            "embeddings": self.embeddings,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def save_np(self, dir_path: str | Path) -> None:
        """Save index to a directory using numpy (.npy) + JSONL for large corpora."""
        if not _HAS_NUMPY:
            raise ImportError("numpy is required: pip install numpy")
        p = Path(dir_path)
        p.mkdir(parents=True, exist_ok=True)
        arr = np.array(self.embeddings, dtype=np.float32)
        np.save(p / "embeddings.npy", arr)
        with (p / "entries.jsonl").open("w", encoding="utf-8") as f:
            for e in self.entries:
                f.write(json.dumps(asdict(e)) + "\n")
        (p / "meta.json").write_text(
            json.dumps({"dims": self.dims, "count": len(self.entries), "encoder_name": self.encoder_name}),
            encoding="utf-8",
        )

    @classmethod
    def load_np(cls, dir_path: str | Path) -> PremiseRetriever:
        """Load index from a directory produced by save_np."""
        if not _HAS_NUMPY:
            raise ImportError("numpy is required: pip install numpy")
        p = Path(dir_path)
        arr = np.load(p / "embeddings.npy")
        embeddings: list[list[float]] = arr.tolist()
        entries: list[PremiseEntry] = []
        for line in (p / "entries.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            entries.append(PremiseEntry(
                name=row.get("name", ""),
                statement=row.get("statement", ""),
                namespace=row.get("namespace", ""),
                source_file=row.get("source_file", ""),
            ))
        meta = json.loads((p / "meta.json").read_text(encoding="utf-8"))
        encoder_name = str(meta.get("encoder_name", "hash"))
        return cls(entries=entries, embeddings=embeddings, dims=int(meta["dims"]), encoder_name=encoder_name)

    def _encode_query(self, goal: str) -> list[float]:
        """Encode a query string with the same encoder used to build the index."""
        if self._st_encoder is not None:
            vecs = self._st_encoder.encode([goal])
            return vecs[0] if vecs else [0.0] * self.dims
        return _embed_hash(goal, self.dims)

    def query(self, goal: str, top_k: int = 12) -> list[RetrievalHit]:
        """Return top-k premises ranked by embedding similarity + name-match boost.

        Lean identifiers that appear verbatim in an entry's name receive a +0.5
        bonus so that e.g. a proof state mentioning `iIndepFun` will surface
        `ProbabilityTheory.iIndepFun_iff_iIndep` above unrelated `comap` lemmas.

        When the index was built with a sentence-transformer encoder, semantic
        similarity is used instead of token-hash overlap, which substantially
        improves recall for paraphrased premises.
        """
        if top_k < 1:
            return []

        # Extract camelCase / PascalCase tokens for name-match boost.
        # A token qualifies if it has at least one uppercase letter and length >= 5.
        lean_idents: list[str] = [
            t for t in TOKEN_RE.findall(goal)
            if len(t) >= 5 and any(c.isupper() for c in t)
        ]

        q = self._encode_query(goal)
        scored: list[tuple[int, float]] = []
        for i, emb in enumerate(self.embeddings):
            base = _dot(q, emb)
            boost = 0.0
            if lean_idents:
                name_lower = self.entries[i].name.lower()
                for ident in lean_idents:
                    if ident.lower() in name_lower:
                        boost = 0.5
                        break
            scored.append((i, base + boost))
        scored.sort(key=lambda x: x[1], reverse=True)

        hits: list[RetrievalHit] = []
        for idx, score in scored[:top_k]:
            e = self.entries[idx]
            hits.append(
                RetrievalHit(
                    name=e.name,
                    statement=e.statement,
                    namespace=e.namespace,
                    score=score,
                    trust_tier="unknown",  # Will be populated by tier-aware retrieval
                )
            )
        return hits

    def query_with_tier_preference(
        self, goal: str, kg_trusted_names: set[str] | None = None,
        kg_conditional_names: set[str] | None = None, top_k: int = 12
    ) -> list[RetrievalHit]:
        """Retrieve premises with preference for trusted KG layer, then conditional, then unknown.

        This implements Phase 3.3/B3 retrieval preference: prioritize theorems that have been
        already verified (trusted layer) over conditional or diagnostics layer entries.

        Args:
            goal: Lean proof state text
            kg_trusted_names: Set of theorem names in trusted KG layer (FULLY_PROVEN + promotion gate passed)
            kg_conditional_names: Set of theorem names in conditional KG layer (INTERMEDIARY_PROVEN)
            top_k: Number of results to return

        Returns:
            Top-k results ranked by: trusted tier score, then embedding score
        """
        kg_trusted_names = kg_trusted_names or set()
        kg_conditional_names = kg_conditional_names or set()

        # Get raw hits (before tier-aware sorting)
        raw_hits = self.query(goal, top_k=top_k * 3)  # Get more candidates to allow tier-based reranking

        # Assign tier and rerank
        for hit in raw_hits:
            if hit.name in kg_trusted_names:
                hit.trust_tier = "trusted"
            elif hit.name in kg_conditional_names:
                hit.trust_tier = "conditional"
            else:
                hit.trust_tier = "unknown"

        # Sort by tier priority (trusted > conditional > unknown), then by score
        tier_order = {"trusted": 0, "conditional": 1, "unknown": 2}
        raw_hits.sort(key=lambda h: (tier_order.get(h.trust_tier, 99), -h.score))

        return raw_hits[:top_k]


def load_kg_tier_names(kg_root: str | Path = "output/kg") -> tuple[set[str], set[str]]:
    """Load theorem names from KG manifests to use for tier-aware retrieval.

    Returns:
        (trusted_names: set[str], conditional_names: set[str])
    """
    kg_root = Path(kg_root)
    trusted_names: set[str] = set()
    conditional_names: set[str] = set()

    # Load from promotion manifest (all trusted theorems across all papers)
    manifest_path = kg_root / "manifests" / "promotion_manifest_all_papers.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest.get("all_papers", []):
                if entry.get("status") == "FULLY_PROVEN" and entry.get("promotion_gate_passed"):
                    trusted_names.add(entry["theorem_name"])
                elif entry.get("status") == "INTERMEDIARY_PROVEN":
                    conditional_names.add(entry["theorem_name"])
        except (json.JSONDecodeError, KeyError):
            pass

    return trusted_names, conditional_names


def fetch_mathlib_corpus(
    out_dir: str | Path,
    *,
    dims: int = 384,
    hf_dataset: str = "FrenzyMath/mathlib_informal_v4.16.0",
    kinds: tuple[str, ...] = ("theorem", "lemma", "proposition", "corollary"),
    encoder_name: str | None = None,
) -> None:
    """Download Mathlib4 corpus from HuggingFace and build a retrieval index.

    Requires: pip install datasets numpy sentence-transformers

    When sentence-transformers is installed, embeddings are computed with
    ``all-MiniLM-L6-v2`` by default (semantic similarity).  Pass
    ``encoder_name="hash"`` to force the legacy hash-based encoder.

    The index is saved as a directory (embeddings.npy + entries.jsonl + meta.json)
    suitable for large corpora.  Each entry's searchable text is:
      informal_description > informal_name > signature > name
    """
    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("pip install datasets to use fetch-mathlib") from exc

    out_path = Path(out_dir)
    print(f"[fetch-mathlib] downloading {hf_dataset} ...")
    ds = load_dataset(hf_dataset, split="train")
    print(f"[fetch-mathlib] {len(ds)} rows — filtering to {kinds} ...")

    entries: list[PremiseEntry] = []
    for row in ds:
        kind = (row.get("kind") or "").lower().strip()
        if kinds and kind not in kinds:
            continue
        raw_name = row.get("name") or ""
        # HF dataset stores name as list of components or as a string
        if isinstance(raw_name, list):
            name = ".".join(str(p) for p in raw_name if p)
            namespace = ".".join(str(p) for p in raw_name[:-1]) if len(raw_name) > 1 else ""
        else:
            name = str(raw_name).strip()
            dot = name.rfind(".")
            namespace = name[:dot] if dot > 0 else ""
        if not name:
            continue
        def _str(val: object) -> str:
            if isinstance(val, list):
                return ".".join(str(v) for v in val if v)
            return str(val or "").strip()

        informal_desc = _str(row.get("informal_description")).strip()
        informal_name = _str(row.get("informal_name")).strip()
        signature = _str(row.get("signature")).strip()
        module = _str(row.get("module_name")).strip()
        # Index on signature (Lean syntax) so token overlap with proof states works.
        # Fall back to informal text only when no signature is available.
        statement = (signature + " " + informal_name).strip() if signature else (informal_desc or informal_name or name)
        entries.append(PremiseEntry(
            name=name,
            statement=statement,
            namespace=namespace,
            source_file=module,
        ))

    effective_encoder = encoder_name if encoder_name is not None else (_DEFAULT_ST_MODEL if _HAS_ST else "hash")
    print(f"[fetch-mathlib] building index for {len(entries)} entries, encoder={effective_encoder} ...")
    retriever = PremiseRetriever.build(entries, dims=dims, encoder_name=encoder_name)
    retriever.save_np(out_path)
    print(f"[fetch-mathlib] done — index saved to {out_path} (encoder={retriever.encoder_name}, dims={retriever.dims})")


def download_precomputed(*, url: str, out_path: str | Path) -> None:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, target)  # noqa: S310 - explicit trusted URL input


def _cmd_fetch_mathlib(args: argparse.Namespace) -> int:
    fetch_mathlib_corpus(
        args.out,
        dims=args.dims,
        hf_dataset=args.dataset,
        encoder_name=getattr(args, "encoder_name", None),
    )
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    entries = parse_toon_nodes(args.toon)
    retriever = PremiseRetriever.build(entries, dims=args.dims)
    retriever.save(args.out)
    print(f"[ok] built index entries={len(entries)} dims={args.dims} -> {args.out}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    retriever = PremiseRetriever.load(args.index)
    hits = retriever.query(args.goal, top_k=args.top_k)
    for i, h in enumerate(hits, start=1):
        print(f"{i}. {h.name} ({h.namespace}) score={h.score:.4f}")
        print(f"   {h.statement}")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    download_precomputed(url=args.url, out_path=args.out)
    print(f"[ok] downloaded precomputed index -> {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Mathlib premise retrieval index")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_fm = sub.add_parser(
        "fetch-mathlib",
        help="Download Mathlib4 corpus from HuggingFace and build retrieval index",
    )
    p_fm.add_argument(
        "--out",
        required=True,
        help="Output directory for the numpy-format index",
    )
    p_fm.add_argument(
        "--dims", type=int, default=384,
        help="Embedding dimension (only used for hash encoder; sentence-transformers set their own)",
    )
    p_fm.add_argument(
        "--dataset",
        default="FrenzyMath/mathlib_informal_v4.16.0",
        help="HuggingFace dataset name",
    )
    p_fm.add_argument(
        "--encoder",
        dest="encoder_name",
        default=None,
        help=(
            "Encoder: sentence-transformers model name (e.g. 'all-MiniLM-L6-v2'), "
            "or 'hash' to force hash embeddings. "
            "Defaults to all-MiniLM-L6-v2 if sentence-transformers is installed."
        ),
    )
    p_fm.set_defaults(func=_cmd_fetch_mathlib)

    p_build = sub.add_parser("build", help="Build retrieval index from TOON inventory")
    p_build.add_argument("--toon", required=True, help="Path to .toon inventory file")
    p_build.add_argument("--out", required=True, help="Output JSON index path")
    p_build.add_argument("--dims", type=int, default=1536, help="Embedding dimension")
    p_build.set_defaults(func=_cmd_build)

    p_query = sub.add_parser("query", help="Query top-k premises")
    p_query.add_argument("--index", required=True, help="Index JSON path")
    p_query.add_argument("--goal", required=True, help="Lean goal text")
    p_query.add_argument("--top-k", type=int, default=12)
    p_query.set_defaults(func=_cmd_query)

    p_dl = sub.add_parser("download", help="Download precomputed embedding index")
    p_dl.add_argument("--url", required=True, help="URL to JSON index artifact")
    p_dl.add_argument("--out", required=True, help="Output path for downloaded index")
    p_dl.set_defaults(func=_cmd_download)

    return p


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())