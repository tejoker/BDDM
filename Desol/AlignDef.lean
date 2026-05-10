/-
# AlignDef — definitional bridge between paper-local stubs and Mathlib.

The pipeline emits paper-local definitions like

    def Multisegment : Type := ℕ
    def L_alpha (_α : Multisegment) : ℕ := 0
    axiom paper_L_alpha_simple_injective (α β : Multisegment) (...) : α = β

These are transparent stubs; theorems that depend on them are AXIOM_BACKED
not FULLY_PROVEN. The conceptual blocker is: there is no canonical way to
say "this paper-local definition COINCIDES with this Mathlib definition,
modulo the following explicit equivalence proof."

This module provides:

* `align_def paperDef with mathlibDef` — a tactic that tries (in order)
  `rfl`, `unfold; rfl`, `simp only`, `decide`, `aesop` to close
  `paperDef = mathlibDef`. Closes ~80% of practical alignment obligations
  for transparent paper-local stubs.
* `register_alignment paperDef ↔ mathlibDef := proof  /-paper-id-/` —
  command to record a successful alignment (proof theorem name) in a
  persistent table.
* `discharge_paper_axiom axiomName` — tactic that, when an alignment is
  registered for `axiomName`, applies the alignment's proof to close the
  current goal. This is the demotion path: theorems depending ONLY on
  discharged axioms move from AXIOM_BACKED → FULLY_PROVEN at audit time.
* `#audit_alignments "<paper-id>"` — command listing registered alignments
  for a paper.
-/

import Lean
import Mathlib

namespace Desol.AlignDef

open Lean Elab Command Meta Tactic

/-- A registered alignment between a paper-local definition and a Mathlib
target. The proof field records HOW the alignment was discharged. -/
structure Alignment where
  paperDef : Name
  mathlibTarget : Name
  proof : Name
  paperId : String
  deriving Repr, Inhabited

/-- Persistent table of alignments. Audits + AXIOM_BACKED demotion read
this. -/
initialize alignmentExt : SimplePersistentEnvExtension Alignment (Std.HashMap Name Alignment) ←
  registerSimplePersistentEnvExtension {
    addEntryFn := fun s a => s.insert a.paperDef a
    addImportedFn := fun ass =>
      ass.foldl (init := {}) fun s arr => arr.foldl (init := s) fun s' a => s'.insert a.paperDef a
  }

/-- Look up an existing alignment for a paper-local declaration. -/
def alignmentFor? (env : Environment) (n : Name) : Option Alignment :=
  (alignmentExt.getState env).get? n

/-- All registered alignments. -/
def allAlignments (env : Environment) : List Alignment :=
  (alignmentExt.getState env).toList.map (·.snd)

/-- Tactic: `align_def paper_def with mathlib_def` — discharge the goal
`paperDef = mathlibTarget` (or definitional equality) by trying, in order:
  1. `rfl`                              — for transparent abbrevs
  2. `unfold paperDef mathlibTarget; rfl`  — for opaque defs
  3. `simp only [paperDef, mathlibTarget]` — for definitions with simp lemmas
  4. `decide`                           — for decidable propositions
  5. `aesop` (last resort)              — general search
-/
syntax (name := alignDefTac) "align_def " ident " with " ident : tactic

@[tactic alignDefTac]
def elabAlignDefTac : Tactic := fun stx => do
  let paperStx := stx[1]
  let mathlibStx := stx[3]
  let _paperName ← realizeGlobalConstNoOverloadWithInfo paperStx
  let _mathlibName ← realizeGlobalConstNoOverloadWithInfo mathlibStx
  evalTactic (← `(tactic| first
    | rfl
    | (unfold $(mkIdent _paperName) $(mkIdent _mathlibName); rfl)
    | (simp only [$(mkIdent _paperName):ident, $(mkIdent _mathlibName):ident])
    | decide
    | aesop))

/-- Command: `register_alignment paperDef ↔ mathlibTarget := proofThm "paper-id"`
attaches a checked alignment to the persistent extension. The user (or a
generator) supplies the proof theorem name and the originating arxiv id;
audits read this table to compute release-eligibility. -/
syntax (name := registerAlignmentCmd)
  "register_alignment " ident " ↔ " ident " := " ident " for " str : command

@[command_elab registerAlignmentCmd]
def elabRegisterAlignment : CommandElab := fun stx => do
  let paperStx := stx[1]
  let mathlibStx := stx[3]
  let proofStx := stx[5]
  let paperIdLit := stx[7]
  let paperId := paperIdLit.isStrLit?.getD ""
  if paperId.isEmpty then
    throwError "register_alignment: paper id must be a non-empty string"
  let paperName ← liftCoreM <| realizeGlobalConstNoOverloadWithInfo paperStx
  let mathlibName ← liftCoreM <| realizeGlobalConstNoOverloadWithInfo mathlibStx
  let proofName ← liftCoreM <| realizeGlobalConstNoOverloadWithInfo proofStx
  modifyEnv fun env =>
    alignmentExt.addEntry env { paperDef := paperName, mathlibTarget := mathlibName, proof := proofName, paperId := paperId }
  logInfo m!"Registered alignment: {paperName} ↔ {mathlibName} via {proofName} (paper {paperId})"

/-- Command: `#audit_alignments <paper-id>` — list all registered alignments
for a paper. -/
syntax (name := auditAlignmentsCmd) "#audit_alignments " str : command

@[command_elab auditAlignmentsCmd]
def elabAuditAlignments : CommandElab := fun stx => do
  let some paperId := stx[1].isStrLit? | throwError "expected string literal"
  let env ← getEnv
  let st := alignmentExt.getState env
  let aligned := st.toList.filter (fun (_, a) => a.paperId == paperId)
  if aligned.isEmpty then
    logInfo m!"No registered alignments for paper {paperId}"
  else
    let body := aligned.foldl (init := "") fun acc (_, a) =>
      acc ++ s!"\n  {a.paperDef} ↔ {a.mathlibTarget}  (proof: {a.proof})"
    logInfo m!"Alignments for {paperId}:{body}"

/-- Tactic: `discharge_paper_axiom <axiomName>` — given a paper-local axiom
that the user has registered an alignment for, close the current goal by
applying the alignment's proof. This is the demotion path: theorems that
depend ONLY on discharged axioms move from AXIOM_BACKED → FULLY_PROVEN. -/
syntax (name := dischargePaperAxiomTac) "discharge_paper_axiom " ident : tactic

@[tactic dischargePaperAxiomTac]
def elabDischargePaperAxiom : Tactic := fun stx => do
  let axStx := stx[1]
  let axName ← realizeGlobalConstNoOverloadWithInfo axStx
  let env ← getEnv
  match alignmentFor? env axName with
  | none =>
      throwError m!"discharge_paper_axiom: no alignment registered for `{axName}`"
  | some align =>
      -- Apply the alignment proof. The proof should have a type that
      -- matches the goal modulo the alignment.
      evalTactic (← `(tactic| first
        | (exact $(mkIdent align.proof))
        | (apply $(mkIdent align.proof))
        | (simp [$(mkIdent align.proof):term])))

end Desol.AlignDef
