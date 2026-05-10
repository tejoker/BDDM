/-
# AxiomBudget — a partial-formalization status as a first-class Lean artifact.

A common pattern in paper→Lean projects: the proof closes in Lean, but it
relies on paper-local definitions and axioms (e.g. `Multisegment`, `Volterra
estimate`) that have no Mathlib counterpart yet. The Mathlib4 community
currently has no canonical way to declare such a theorem as "proved modulo
named axioms" with audit tooling — every project rolls its own.

This module provides:
  * `[paper_axiom <paper-id>]` attribute — tags an `axiom` declaration with
    its arxiv paper of origin.
  * `[paper_definition_stub <paper-id>]` attribute — tags a `def` whose body
    is a transparent grounding (`:= 0`, `:= True`, `:= Set.univ`, etc.) so
    audits can flag rows that depend on it.
  * `release_eligible` predicate — true iff a theorem's transitive
    declaration set has zero `paper_axiom` and zero `paper_definition_stub`
    dependencies.
  * `auditAxioms` command — given a theorem name, lists the paper-local
    axioms / stubs it depends on (via Lean 4's `Lean.collectAxioms`).

The proposal we'd send upstream is: standardise this schema (or something
isomorphic) in `Mathlib.Tactic.AxiomBudget` so every paper-formalization
project doesn't reinvent the same plumbing.
-/

import Lean
import Mathlib

namespace Desol.AxiomBudget

open Lean Elab Command Meta

/-- An axiom declaration that comes from a research paper that doesn't have
a Mathlib counterpart yet. The argument is the paper id (typically an arxiv
id like `2304.09598`). Theorems that transitively depend on a `paper_axiom`
must be reported as proved-modulo-paper-axioms, not as unconditional. -/
syntax (name := paperAxiomAttr) "paper_axiom " str : attr

/-- A definition stub whose body is a transparent grounding (constant zero,
trivial Prop, universe set, etc.) — emitted by the paper-theory builder so
proof search can elaborate paper-local identifiers without committing to
their actual semantics. Theorems that depend on a stubbed def must be
flagged in audits. -/
syntax (name := paperDefStubAttr) "paper_definition_stub " str : attr

/-- Internal storage for paper-axiom tags. Maps declaration name → paper id. -/
initialize paperAxiomExt : SimplePersistentEnvExtension (Name × String) (Std.HashMap Name String) ←
  registerSimplePersistentEnvExtension {
    addEntryFn := fun s (n, p) => s.insert n p
    addImportedFn := fun ass =>
      ass.foldl (init := {}) fun s arr => arr.foldl (init := s) fun s' (n, p) => s'.insert n p
  }

/-- Internal storage for paper-stub-def tags. -/
initialize paperDefStubExt : SimplePersistentEnvExtension (Name × String) (Std.HashMap Name String) ←
  registerSimplePersistentEnvExtension {
    addEntryFn := fun s (n, p) => s.insert n p
    addImportedFn := fun ass =>
      ass.foldl (init := {}) fun s arr => arr.foldl (init := s) fun s' (n, p) => s'.insert n p
  }

/-- Register a declaration as `paper_axiom <paper-id>`. -/
initialize registerBuiltinAttribute {
  name := `paper_axiom_attr
  descr := "Tag a paper-local axiom with its arxiv paper id."
  add := fun decl stx _ => MetaM.run' do
    let some lit := stx.getArgs[1]? | throwError "paper_axiom: expected a string literal"
    let paperId := lit.isStrLit?.getD ""
    if paperId.isEmpty then
      throwError "paper_axiom: paper id must be a non-empty string literal"
    modifyEnv fun env => paperAxiomExt.addEntry env (decl, paperId)
}

/-- Register a declaration as `paper_definition_stub <paper-id>`. -/
initialize registerBuiltinAttribute {
  name := `paper_definition_stub_attr
  descr := "Tag a paper-local definition stub with its arxiv paper id."
  add := fun decl stx _ => MetaM.run' do
    let some lit := stx.getArgs[1]? | throwError "paper_definition_stub: expected a string literal"
    let paperId := lit.isStrLit?.getD ""
    if paperId.isEmpty then
      throwError "paper_definition_stub: paper id must be a non-empty string literal"
    modifyEnv fun env => paperDefStubExt.addEntry env (decl, paperId)
}

/-- Look up paper-axiom tags by declaration name. Returns the paper id or
none if untagged. -/
def paperAxiomFor? (env : Environment) (n : Name) : Option String :=
  (paperAxiomExt.getState env).get? n

def paperDefStubFor? (env : Environment) (n : Name) : Option String :=
  (paperDefStubExt.getState env).get? n

/-- The transitively-reached paper-local declarations of a theorem.
Returns `(axioms, stubs)` keyed by declaration name → paper id. -/
def paperLocalDeps (env : Environment) (thm : Name) : MetaM (List (Name × String) × List (Name × String)) := do
  let used ← Lean.collectAxioms thm
  let mut paperAxioms : List (Name × String) := []
  let mut paperStubs : List (Name × String) := []
  for n in used do
    if let some pid := paperAxiomFor? env n then
      paperAxioms := paperAxioms.concat (n, pid)
    if let some pid := paperDefStubFor? env n then
      paperStubs := paperStubs.concat (n, pid)
  return (paperAxioms, paperStubs)

/-- A theorem is `release_eligible` iff its axiom-closure contains zero
paper-local axioms and zero paper-definition stubs. This is the predicate
that `evaluate_promotion_gates` (in pipeline_status.py) ought to consult
when deciding FULLY_PROVEN vs AXIOM_BACKED. -/
def releaseEligible (env : Environment) (thm : Name) : MetaM Bool := do
  let (ax, st) ← paperLocalDeps env thm
  return ax.isEmpty && st.isEmpty

/-- `#audit_axioms <theorem>` — print the paper-local axioms and stubs that
`<theorem>` depends on. -/
syntax (name := auditAxiomsCmd) "#audit_axioms " ident : command

@[command_elab auditAxiomsCmd]
def elabAuditAxioms : CommandElab := fun stx => do
  let nameStx := stx[1]
  let n ← liftCoreM <| Lean.Elab.realizeGlobalConstNoOverloadWithInfo nameStx
  let env ← getEnv
  let (ax, st) ← liftTermElabM <| Meta.MetaM.run' do paperLocalDeps env n
  if ax.isEmpty && st.isEmpty then
    logInfo m!"{n}: release_eligible (no paper-local axioms or stubs)"
  else
    let axLines := ax.foldl (init := "") fun acc (a, p) => acc ++ s!"\n  paper_axiom {p}: {a}"
    let stLines := st.foldl (init := "") fun acc (s, p) => acc ++ s!"\n  paper_definition_stub {p}: {s}"
    logInfo m!"{n}: NOT release_eligible — depends on:{axLines}{stLines}"

end Desol.AxiomBudget
