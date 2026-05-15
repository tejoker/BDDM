# BDDM convenience targets. The CI workflow `canonical_integrity.yml`
# runs the same audits — this Makefile makes them runnable in one shot
# locally for contributors who don't want to hand-stitch the commands.

.PHONY: audit reproduce-canonical-evidence audit-fully-proven audit-gate-consistency audit-olean-health test test-fast

# Aggregate gate: every read-only integrity audit + the reproducibility
# verifier. Exits non-zero on the first failing check. Mirrors the jobs
# defined in `.github/workflows/canonical_integrity.yml`.
audit: reproduce-canonical-evidence audit-fully-proven audit-gate-consistency audit-olean-health

reproduce-canonical-evidence:
	python3 scripts/reproduce_canonical_evidence.py

audit-fully-proven:
	python3 scripts/audit_fully_proven_integrity.py --include-ip-ab --fail-on-demote

audit-gate-consistency:
	python3 scripts/audit_gate_consistency.py --fail-on-rebuild

audit-olean-health:
	python3 scripts/audit_paper_theory_olean_health.py

# Convenience: run the project test suite. `test-fast` skips slow
# (lake/REPL/Mistral/HTTP) tests for a quick local turnaround.
test:
	python3 -m pytest tests/ -x -q

test-fast:
	python3 -m pytest tests/ -x -q -m 'not slow'
