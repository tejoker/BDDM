/-
# Self-test for `align_def` and `register_alignment`.

Demonstrates the alignment infrastructure on a tiny synthetic example:
a paper-local abbrev `MyPaperNat` that's just `ℕ`, with an alignment
registered against `Nat` and discharged via the `align_def` tactic.

This is the smoke-test for the AXIOM_BACKED → FULLY_PROVEN demotion path
at the syntax-elaboration level. The actual demotion happens in the
Python audit (`audit_axioms.py`) which reads the alignment table and
filters paper-local axioms accordingly.
-/

import Mathlib
import Desol.AlignDef

open Desol.AlignDef

namespace Desol.AlignDefTest

-- A paper-local "type" — pretends to be unique to a paper but actually just ℕ.
-- Use abbrev so arithmetic instances auto-unfold.
abbrev MyPaperNat : Type := Nat

-- The alignment proof.
theorem MyPaperNat_eq_Nat : MyPaperNat = Nat := rfl

-- Register the alignment so audits can find it.
register_alignment MyPaperNat ↔ Nat := MyPaperNat_eq_Nat for "0000.99999"

-- The `align_def` tactic closes the alignment obligation directly.
example : MyPaperNat = Nat := by align_def MyPaperNat with Nat

end Desol.AlignDefTest
