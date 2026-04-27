"""Re-translate the 5 schema-placeholder theorems in 2304.09598 using Leanstral."""
from __future__ import annotations
import json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# ── LaTeX source for the 5 theorems ──────────────────────────────────────────
THEOREMS = [
    {
        "name": "Prop_Actions",
        "latex": (
            r"Let $\Delta_1$ and $\Delta_2$ be any two segments in an arbitrary multisegment $\alpha$, "
            r"then we can construct a new multisegment $\beta$ by replacing each $\Delta_1$ and $\Delta_2$ "
            r"in $\alpha$ with respectively "
            r"$\Delta_1 \cap \Delta_2$ and $\Delta_1 \cup \Delta_2$ if $\Delta_1 \cap \Delta_2 \neq \emptyset$ "
            r"and $\Delta_1 \neq \Delta_2$; or $\Delta_1 \cup \Delta_2$ if $\Delta_1 \cup \Delta_2$ is a segment; "
            r"or $\Delta_1$ and $\Delta_2$ otherwise. Then we have that $\alpha \leq \beta$."
        ),
        "kind": "prop",
    },
    {
        "name": "Cor_BoundaryRTandMS",
        "latex": (
            r"Suppose that $\alpha$ and $\beta$ are multisegments with respective corresponding conjugacy "
            r"classes denoted by $C$ and $D$ (with identical top rows). Then there exists a partial ordering "
            r"between the two multisegments if and only if there exists a partial ordering on their "
            r"corresponding conjugacy classes, that is, $\alpha \leq \beta$ if and only if $C \leq D$."
        ),
        "kind": "corollary",
    },
    {
        "name": "Exa_QFM",
        "latex": (
            r"There exists a ladder multisegment $\alpha$ for which there is a complete ordering of the "
            r"segments based around their base and end values, with segments ordered by both base and end "
            r"values satisfying the ladder multisegment condition."
        ),
        "kind": "example",
    },
    {
        "name": "Lem_A1",
        "latex": (
            r"Let $\alpha$ be an arbitrary multisegment containing a sub-multisegment $\alpha_1$ of the form "
            r"$\alpha_1 = \{ [-e,b], [-e+1, b+1], \dots, [-b-1, e-1], [-b, e] \}$. "
            r"If $\alpha_1$ contains both of the shortest segments containing the minimum and maximum values, "
            r"$-e$ and $e$, of the multisegment $\alpha$ then it will not be possible to generate $[b,e]$ "
            r"or a segment containing $b, \dots, e$ from any sub-multisegment other than $\alpha_1$."
        ),
        "kind": "lemma",
    },
    {
        "name": "Lem_A1b",
        "latex": (
            r"Let $\alpha$ be an arbitrary multisegment containing a sub-multisegment $\alpha_1$ of the form "
            r"$\alpha_1 = \{ [-e,b], [-e+1, b+1], \dots, [-b-1, e-1], [-b, e] \}$. "
            r"If $\alpha_1$ contains both of the shortest segments containing the minimum and maximum values, "
            r"$-e$ and $e$, of the multisegment $\alpha$ then removing copies of $\alpha_1$ will induce an "
            r"endoscopic decomposition, that is, $\alpha = \alpha_1 \sqcup (\alpha - \alpha_1)$ and "
            r"$\tilde{\alpha} = \widetilde{\alpha_1} \sqcup \widetilde{(\alpha - \alpha_1)}$."
        ),
        "kind": "lemma",
    },
]

IMPORTS = (
    "import Mathlib\n"
    "import Aesop\n\n"
    "open MeasureTheory ProbabilityTheory\n\n"
    "-- Domain axioms for multisegments (Moeglin-Waldspurger combinatorics)\n"
    "axiom Segment : Type\n"
    "axiom Multisegment : Type\n"
    "axiom segLE : Multisegment → Multisegment → Prop\n"
    "axiom ConjugacyClass : Type\n"
    "axiom ccLE : ConjugacyClass → ConjugacyClass → Prop\n"
    "axiom msToCC : Multisegment → ConjugacyClass\n"
    "axiom IsLadderMultiseg : Multisegment → Prop\n"
    "axiom IsSubMultiseg : Multisegment → Multisegment → Prop\n"
    "axiom msUnion : Multisegment → Multisegment → Multisegment\n"
    "axiom msDiff : Multisegment → Multisegment → Multisegment\n"
    "axiom msDual : Multisegment → Multisegment\n"
    "axiom msDisjointUnion : Multisegment → Multisegment → Multisegment\n"
    "axiom CanGenSegFrom : Multisegment → ℤ → ℤ → Prop\n"
    "axiom HasIdenticalTopRows : Multisegment → Multisegment → Prop\n"
)

PROJECT_ROOT = Path(__file__).parent.parent
LEAN_FILE = PROJECT_ROOT / "output" / "2304.09598.lean"
LEDGER_FILE = PROJECT_ROOT / "output" / "verification_ledgers" / "2304.09598.json"


def make_client():
    api_key = os.environ.get("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY not set")
    try:
        from mistralai.client import Mistral
    except ImportError:
        from mistralai import Mistral  # type: ignore
    return Mistral(api_key=api_key)


def translate_one(client, model: str, thm: dict, project_root: Path) -> str | None:
    from translator._translate import translate_statement
    result = translate_statement(
        latex_statement=thm["latex"],
        client=client,
        model=model,
        project_root=project_root,
        imports=IMPORTS,
        max_repair_rounds=5,
        translation_candidates=3,
        temperature=0.3,
        run_adversarial_check=True,
        run_roundtrip_check=True,
        use_schema_stage=True,
        deterministic_hard_mode=True,
        strict_assumption_slot_coverage=False,
        enable_schema_template_synthesis=True,
        enable_schema_self_check=True,
    )
    sig = getattr(result, "lean_signature", "") or ""
    validated = getattr(result, "validated", False)
    err = getattr(result, "last_error", "") or ""
    print(f"  validated={validated}  rounds={getattr(result,'rounds_used',0)}  err={err[:80]}")
    if not sig or not validated:
        print(f"  WARNING: translation not validated — using best candidate anyway")
        # Still use if it looks like a real theorem declaration
        if not sig or "p_c1" in sig or "(0 : ℕ) = 0" in sig:
            return None
    # Normalize name
    sig = re.sub(
        r"^(\s*(?:noncomputable\s+)?(?:private\s+)?(?:theorem|lemma|def)\s+)\S+",
        lambda m: m.group(1) + thm["name"],
        sig, count=1, flags=re.MULTILINE,
    )
    return sig.strip()


def patch_lean_file(lean_file: Path, name: str, new_sig: str) -> bool:
    text = lean_file.read_text(encoding="utf-8")
    # Find the theorem block
    pattern = re.compile(
        r"(-- \[theorem\] " + re.escape(name.replace("_", ":")).replace(r"\:", "[_:]") + r".*?\n)"
        r"((?:-- .*\n)*)"  # optional comment lines
        r"(theorem|lemma)\s+" + re.escape(name) + r"[^\n]*(?:\n[^\n]+)*?:= by\n\s+(?:exact h_c\d+|trivial|rfl)\n",
        re.MULTILINE,
    )
    # Simpler: just find the theorem declaration block by name and replace body
    decl_re = re.compile(
        r"(theorem|lemma)\s+" + re.escape(name) + r"(\s|\().*?:= by\n\s+(?:exact h_c\d+|trivial|rfl)(?:\nexact \w+)?",
        re.DOTALL,
    )
    m = decl_re.search(text)
    if not m:
        print(f"  WARN: could not find placeholder declaration for {name} in lean file")
        return False
    new_decl = new_sig + " := by\n  sorry"
    new_text = text[:m.start()] + new_decl + text[m.end():]
    lean_file.write_text(new_text, encoding="utf-8")
    return True


def update_ledger(ledger_file: Path, name: str, new_sig: str) -> None:
    data = json.loads(ledger_file.read_text(encoding="utf-8"))
    entries = data if isinstance(data, list) else data.get("entries", [])
    updated = 0
    for e in entries:
        n = e.get("theorem_name", "")
        short = n.replace("ArxivPaper.", "").replace("ArxivPaperActionable.", "")
        if short == name or n == name:
            e["lean_statement"] = new_sig
            e["status"] = "UNRESOLVED"
            e["proof_method"] = "unknown"
            e["promotion_gate_passed"] = False
            e["gate_failures"] = ["lean_proof_closed", "step_verdict_verified"]
            e["validation_gates"] = {}
            e["error_message"] = "re-translated: pending proof"
            updated += 1
    ledger_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"  ledger updated: {updated} entries for {name}")


def main():
    model = os.environ.get("MISTRAL_MODEL", "labs-leanstral-2603")
    client = make_client()
    print(f"Using model: {model}\n")

    results = {}
    for thm in THEOREMS:
        name = thm["name"]
        print(f"[{name}] translating...")
        sig = translate_one(client, model, thm, PROJECT_ROOT)
        if sig:
            print(f"  OK:\n  {sig[:120]}")
            results[name] = sig
        else:
            print(f"  FAILED — keeping placeholder, needs manual review")
        print()

    print(f"\n{'='*60}")
    print(f"Successfully translated: {len(results)}/{len(THEOREMS)}")

    for name, sig in results.items():
        print(f"\nPatching {name} into lean file...")
        patched = patch_lean_file(LEAN_FILE, name, sig)
        print(f"  lean file patch: {'OK' if patched else 'FAILED'}")
        update_ledger(LEDGER_FILE, name, sig)

    if results:
        print(f"\nTranslated signatures:")
        for name, sig in results.items():
            print(f"\n-- {name}")
            print(sig)


if __name__ == "__main__":
    main()
