# Production Readiness (Hard Baseline)

This repository is **not production-ready** until all gates below are green.

## Gate A: Reliability
- [ ] Lean toolchain + lake dependencies pinned and reproducible.
- [ ] API client retries are bounded with timeout + jitter.
- [ ] Proof backend failover path (`leandojo` -> `repldojo`) emits structured reason codes.
- [ ] Verify jobs have explicit admission control and bounded concurrency.
- [ ] No broad `except Exception` on critical orchestration paths without classified rethrow/logging.

## Gate B: Security
- [ ] API authentication enabled (`DESOL_API_KEY` or stronger auth provider).
- [ ] Rate limiting enabled and validated under burst traffic.
- [ ] Verify endpoint protected against queue flooding via inflight cap.
- [ ] Security regression tests pass for key validators.
- [ ] Audit logging records job trigger identity + request envelope.

## Gate C: Reproducibility
- [ ] `lakefile.toml` does not track `master`/`main` for core dependencies.
- [ ] `lean-toolchain` pinned and recorded in run artifacts.
- [ ] Benchmark artifacts include model, retrieval, timeout, git commit, and runtime versions.
- [ ] `python3 scripts/release_readiness.py` passes.

## Gate D: Architecture
- [ ] Large modules split into domain logic + adapters + orchestration.
- [ ] Compatibility shims maintained during migration.
- [ ] Module boundaries documented.

## Gate E: Observability
- [ ] Structured logs for API auth/rate-limit/verify admission events.
- [ ] Core metrics available: success rate, timeout rate, queue depth, inflight jobs.
- [ ] Alert thresholds defined for sustained failures and capacity saturation.

## Gate F: Test & Release Discipline
- [ ] Required CI gates: syntax, unit tests, targeted integration tests, readiness checks.
- [ ] Changelog + release checklist required for each release.
- [ ] Rollback procedure documented.

