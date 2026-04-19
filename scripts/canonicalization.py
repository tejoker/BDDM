#!/usr/bin/env python3
"""Canonicalization helpers for theorem identity across papers.

The goal here is *stable identity*, not perfect semantic equivalence.
We normalize theorem signatures into a deterministic textual form and
derive a hash-based canonical theorem ID.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import time
from typing import Any


_DECL_RE = re.compile(r"^\s*(theorem|lemma)\s+([A-Za-z_][A-Za-z0-9_']*)", re.MULTILINE)
_BINDER_GROUP_RE = re.compile(r"(\([^\(\)]*\)|\{[^\{\}]*\}|\[[^\[\]]*\])")
_ID_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_']*")
_CANON_TOKEN_RE = re.compile(r"[A-Za-z0-9_']+")
_ALPHA_TOKEN_RE = re.compile(r"\bv\d+\b")
_STOPWORDS = frozenset(
    {
        "theorem",
        "lemma",
        "prop",
        "type",
        "forall",
        "exists",
        "true",
        "false",
        "and",
        "or",
        "by",
        "let",
        "have",
    }
)


def _normalize_ws(text: str) -> str:
    return " ".join((text or "").split())


def _strip_decl_body(sig: str) -> str:
    out = re.sub(r":=\s*by\b.*$", "", sig or "", flags=re.DOTALL).strip()
    return re.sub(r":=\s*$", "", out).strip()


def _split_signature(sig: str) -> tuple[str, str]:
    depth = 0
    for i, ch in enumerate(sig):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            nxt = sig[i + 1] if i + 1 < len(sig) else ""
            if nxt not in ("=", ":"):
                return sig[:i].strip(), sig[i + 1 :].strip()
    return sig.strip(), ""


def _split_group(group: str) -> tuple[list[str], str]:
    core = group[1:-1].strip()
    if ":" not in core:
        return [], core
    left, right = core.split(":", 1)
    names = [n.strip() for n in left.split() if _ID_TOKEN_RE.fullmatch(n.strip())]
    return names, right.strip()


def _normalize_binders(head: str) -> tuple[str, dict[str, str]]:
    """Normalize binder ordering and produce alpha-renaming map."""
    groups: list[tuple[str, tuple[str, ...], str]] = []

    for m in _BINDER_GROUP_RE.finditer(head):
        grp = m.group(1)
        opener = grp[:1]
        names, typ = _split_group(grp)
        if names and typ:
            names_sorted = sorted(names)
            groups.append((opener, tuple(names_sorted), _normalize_ws(typ)))
        else:
            groups.append((opener, tuple(), _normalize_ws(grp[1:-1])))

    # Stable binder ordering by type and bracket class.
    rank = {"(": 0, "{": 1, "[": 2}
    groups.sort(key=lambda g: (rank.get(g[0], 9), g[2], g[1]))
    renames: dict[str, str] = {}
    name_counter = itertools.count(1)
    for _opener, names, _typ in groups:
        for n in names:
            if n not in renames:
                renames[n] = f"v{next(name_counter)}"

    out_parts: list[str] = []
    closer = {"(": ")", "{": "}", "[": "]"}
    for opener, names, typ in groups:
        if names and typ:
            mapped = [renames[n] for n in names]
            out_parts.append(f"{opener}{' '.join(mapped)} : {typ}{closer.get(opener, ')')}")
        elif typ:
            out_parts.append(f"{opener}{typ}{closer.get(opener, ')')}")
    return " ".join(out_parts).strip(), renames


def _alpha_rename(text: str, renames: dict[str, str]) -> str:
    if not renames:
        return text

    def _sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        return renames.get(tok, tok)

    return _ID_TOKEN_RE.sub(_sub, text)


def _reindex_alpha(binders: str, target: str) -> tuple[str, str]:
    """Reindex vN vars by first appearance in target for alpha stability."""
    mapping: dict[str, str] = {}
    idx = itertools.count(1)

    for tok in _ALPHA_TOKEN_RE.findall(target):
        if tok not in mapping:
            mapping[tok] = f"u{next(idx)}"
    for tok in _ALPHA_TOKEN_RE.findall(binders):
        if tok not in mapping:
            mapping[tok] = f"u{next(idx)}"

    def _sub(m: re.Match[str]) -> str:
        t = m.group(0)
        return mapping.get(t, t)

    return _ALPHA_TOKEN_RE.sub(_sub, binders), _ALPHA_TOKEN_RE.sub(_sub, target)


def _sort_binders_text(binders: str) -> str:
    groups = _BINDER_GROUP_RE.findall(binders or "")
    if not groups:
        return binders
    groups_sorted = sorted(" ".join(g.split()) for g in groups)
    return " ".join(groups_sorted)


def _canon_tokens(stmt: str) -> set[str]:
    toks = {t.lower() for t in _CANON_TOKEN_RE.findall(stmt or "") if len(t) >= 2}
    return {t for t in toks if t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / max(1, union)


def canonicalize_lean_statement(lean_statement: str) -> str:
    """Normalize a Lean theorem statement into a stable canonical text."""
    sig = _strip_decl_body(lean_statement or "")
    sig = _normalize_ws(sig)
    if not sig:
        return ""

    # Remove declaration name so paper-local naming does not affect identity.
    sig = _DECL_RE.sub(r"\1 _", sig, count=1)

    # Normalize common unicode variants and punctuation spacing.
    sig = sig.replace("≤", "<=").replace("≥", ">=")
    sig = re.sub(r"\s*:\s*", " : ", sig)
    sig = re.sub(r"\s*,\s*", ", ", sig)
    sig = re.sub(r"\s+", " ", sig).strip()

    head, target = _split_signature(sig)
    binders, renames = _normalize_binders(head)
    target = _alpha_rename(target, renames)
    binders, target = _reindex_alpha(binders, target)
    binders = _sort_binders_text(binders)
    target = _normalize_ws(target)

    if binders and target:
        return f"theorem _ {binders} : {target}"
    if target:
        return f"theorem _ : {target}"
    return _normalize_ws(head)


def canonical_claim_shape(lean_statement: str) -> str:
    """Extract a coarse claim shape token for fast grouping."""
    sig = canonicalize_lean_statement(lean_statement)
    if not sig:
        return "unknown"
    if ":" not in sig:
        return "unknown"
    target = sig.split(":", 1)[1]
    if any(tok in target for tok in ("<=", ">=", "<", ">")):
        return "inequality"
    # Equality should not consume <= or >= cases above.
    if re.search(r"(?<![<>])=(?![>])", target):
        return "equality"
    if "↔" in target or "<->" in target:
        return "iff"
    if "∧" in target or " and " in target:
        return "conjunction"
    if "∨" in target or " or " in target:
        return "disjunction"
    if "∃" in target or "Exists" in target:
        return "existential"
    if "∀" in target or "forall" in target.lower():
        return "universal"
    return "proposition"


def canonical_theorem_id(
    *,
    lean_statement: str,
    theorem_name: str = "",
    paper_id: str = "",
) -> str:
    """Deterministically compute canonical theorem id."""
    canonical = canonicalize_lean_statement(lean_statement)
    if not canonical:
        canonical = _normalize_ws(theorem_name) or "unknown_theorem"
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"cth_{digest}"


def canonical_record(
    *,
    lean_statement: str,
    theorem_name: str = "",
    paper_id: str = "",
) -> dict[str, Any]:
    """Return canonical metadata bundle suitable for KG node embedding."""
    canonical = canonicalize_lean_statement(lean_statement)
    return {
        "canonical_theorem_id": canonical_theorem_id(
            lean_statement=lean_statement,
            theorem_name=theorem_name,
            paper_id=paper_id,
        ),
        "canonical_statement": canonical,
        "claim_shape": canonical_claim_shape(lean_statement),
    }


def cluster_near_duplicates(
    nodes: list[dict[str, Any]],
    *,
    min_jaccard: float = 0.92,
) -> list[dict[str, Any]]:
    """Group semantically-near theorem statements for manual conflict handling."""
    by_shape: dict[str, list[dict[str, Any]]] = {}
    for n in nodes:
        shape = str(n.get("claim_shape", "unknown"))
        by_shape.setdefault(shape, []).append(n)

    clusters: list[dict[str, Any]] = []
    cluster_id = 0
    for shape, group in by_shape.items():
        used: set[int] = set()
        toks = [_canon_tokens(str(n.get("canonical_statement", ""))) for n in group]
        for i in range(len(group)):
            if i in used:
                continue
            members = [i]
            for j in range(i + 1, len(group)):
                if j in used:
                    continue
                sim = _jaccard(toks[i], toks[j])
                if sim >= min_jaccard:
                    members.append(j)
            if len(members) <= 1:
                continue
            for m in members:
                used.add(m)
            row_members = [group[m] for m in members]
            cluster_id += 1
            clusters.append(
                {
                    "cluster_id": f"near_{cluster_id:05d}",
                    "claim_shape": shape,
                    "size": len(row_members),
                    "members": [
                        {
                            "paper_id": str(r.get("paper_id", "")),
                            "theorem_name": str(r.get("theorem_name", "")),
                            "canonical_theorem_id": str(r.get("canonical_theorem_id", "")),
                            "canonical_statement": str(r.get("canonical_statement", "")),
                        }
                        for r in row_members
                    ],
                }
            )
    clusters.sort(key=lambda c: (-int(c.get("size", 0)), str(c.get("cluster_id", ""))))
    return clusters


def build_manual_conflict_queue(
    nodes: list[dict[str, Any]],
    *,
    min_jaccard: float = 0.92,
) -> dict[str, Any]:
    """Create a conflict queue payload for human resolution workflow."""
    near = cluster_near_duplicates(nodes, min_jaccard=min_jaccard)
    queue_items: list[dict[str, Any]] = []
    for c in near:
        queue_items.append(
            {
                "queue_id": f"conflict_{c['cluster_id']}",
                "reason": "near_duplicate_semantic_cluster",
                "status": "pending_review",
                "cluster_id": c["cluster_id"],
                "claim_shape": c["claim_shape"],
                "members": c["members"],
            }
        )
    return {
        "generated_at_unix": int(time.time()),
        "items_total": len(queue_items),
        "items": queue_items,
    }
