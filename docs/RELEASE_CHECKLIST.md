# Release Checklist

## 1. Pre-merge
- [ ] `python3 -m py_compile scripts/*.py scripts/mcts/*.py scripts/translator/*.py`
- [ ] Targeted regression tests for touched modules.
- [ ] Security-sensitive changes reviewed by at least one maintainer.

## 2. Pre-release
- [ ] `python3 scripts/release_readiness.py`
- [ ] Bench artifact schema validated and attached.
- [ ] Lean toolchain and dependency revisions documented in release notes.
- [ ] API auth/rate-limit configuration verified for target environment.

## 3. Release notes
- [ ] Include breaking changes and migration steps.
- [ ] Include benchmark delta and confidence caveats.
- [ ] Include known operational risks and fallback modes.

## 4. Post-release
- [ ] Verify health endpoint and spot-check `/verify` admission behavior.
- [ ] Confirm no sustained `429` saturation or backend init failures.
- [ ] Capture first 24h incident notes.

