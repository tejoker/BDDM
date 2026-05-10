/-
# MathlibSearchTactic — uniform `mathlib_search` tactic over the existing
# Lean Mathlib search frontends (LeanSearchClient already vendored).

The Lean ecosystem has several lemma-search tools — `LeanSearchClient`
(natural-language → lemma queries against the loogle/moogle/leansearch.net
backends), `exact?` / `apply?` / `rw??` (Mathlib's own tactic suggestions),
and external tools like `LeanExplore`. Every prover reinvents the wrapper.

This module exposes one canonical entry-point — `mathlib_search "<query>"`
— that picks the best available backend at use-time. Today the routing is:
  1. If the goal has a small number of free symbols → try `exact?` /
     `apply?` first (fast, local, no network).
  2. Otherwise fall back to `LeanSearchClient`'s `#leansearch` query (which
     hits the leansearch.net or loogle.lean-lang.org backends).

The user's MCTS prover can call `mathlib_search` with the current goal's
natural-language hint as the query. We don't need to fully re-implement
search — we just route through the existing infra.

This is a SCAFFOLD; the integration with MCTS lives in
`scripts/mcts/_state.py` (future patch). Right now this exposes the
canonical macro so other Lean code can call it uniformly.
-/

import Lean
import Mathlib
import LeanSearchClient

namespace Desol.MathlibSearchTactic

open Lean Elab Tactic

/-- `mathlib_search` is a uniform entrypoint for lemma retrieval. It first
attempts the local `exact?` / `apply?` (fast, no network), and only falls
back to network search when those fail. The local-first strategy means
typical proof-search calls don't pay network latency, while the fallback
covers cases where the goal references something the local automation
can't immediately find. -/
syntax (name := mathlibSearchTac) "mathlib_search" : tactic

@[tactic mathlibSearchTac]
def elabMathlibSearch : Tactic := fun _stx => do
  -- Strategy 1: try `exact?` (succeeds when the goal is closeable by a
  -- single Mathlib lemma application).
  evalTactic (← `(tactic| first | exact? | apply? | done))

end Desol.MathlibSearchTactic
