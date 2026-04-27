# Scripts Directory

This directory intentionally contains both stable commands and experiments.
Use `script_registry.py` to tell them apart:

```bash
python scripts/script_registry.py --tier official_pipeline
python scripts/script_registry.py --check
```

The official pipeline commands are:

- `arxiv_to_lean.py`: single-paper pipeline.
- `formalize_paper_full.py`: full-paper reproducibility and closure harness.
- `run_paper_agnostic_suite.py`: fixed-config suite runner.
- `arxiv_cycle.py`: curated queue batch runner.
- `arxiv_cycle_daemon.py`: long-running arXiv queue daemon.
- `pipeline_worker.py`: queued job worker.

Everything else is either support code, reporting/CI, benchmarking, a developer
tool, a research experiment, or a legacy one-off. New top-level scripts must be
registered in `script_registry.py`; `tests/test_script_registry.py` enforces
that rule.
