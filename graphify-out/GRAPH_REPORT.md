# Graph Report - .  (2026-04-12)

## Corpus Check
- 59 files · ~80,385 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1135 nodes · 2790 edges · 62 communities detected
- Extraction: 60% EXTRACTED · 40% INFERRED · 0% AMBIGUOUS · INFERRED: 1129 edges (avg confidence: 0.51)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Benchmark MiniF2F Suite|Benchmark MiniF2F Suite]]
- [[_COMMUNITY_MCTS Ponder Loop Core|MCTS Ponder Loop Core]]
- [[_COMMUNITY_MCTS Tree Search Engine|MCTS Tree Search Engine]]
- [[_COMMUNITY_Premise Retrieval Build|Premise Retrieval Build]]
- [[_COMMUNITY_ArXiv Cycle Daemon|ArXiv Cycle Daemon]]
- [[_COMMUNITY_Pipeline Status Ledger|Pipeline Status Ledger]]
- [[_COMMUNITY_Statement Translator TC|Statement Translator TC]]
- [[_COMMUNITY_Bridge Proof Execution|Bridge Proof Execution]]
- [[_COMMUNITY_Lean REPL Dojo Backend|Lean REPL Dojo Backend]]
- [[_COMMUNITY_ArXiv to Lean Pipeline|ArXiv to Lean Pipeline]]
- [[_COMMUNITY_ArXiv Fetcher Cycle|ArXiv Fetcher Cycle]]
- [[_COMMUNITY_MCTS Core Tests|MCTS Core Tests]]
- [[_COMMUNITY_Lean REPL Dojo Tests|Lean REPL Dojo Tests]]
- [[_COMMUNITY_Premise Encoder Backend|Premise Encoder Backend]]
- [[_COMMUNITY_Batch Proof Orchestration|Batch Proof Orchestration]]
- [[_COMMUNITY_Mathlib Contribution|Mathlib Contribution]]
- [[_COMMUNITY_TC Graph Builder|TC Graph Builder]]
- [[_COMMUNITY_Benchmark Distributed Config|Benchmark Distributed Config]]
- [[_COMMUNITY_Benchmark Results|Benchmark Results]]
- [[_COMMUNITY_CLI Health Check Tests|CLI Health Check Tests]]
- [[_COMMUNITY_Proof Backend Tests|Proof Backend Tests]]
- [[_COMMUNITY_Tactic Training Pipeline|Tactic Training Pipeline]]
- [[_COMMUNITY_Import Validator|Import Validator]]
- [[_COMMUNITY_Step Entailment Z3|Step Entailment Z3]]
- [[_COMMUNITY_Tactic Constraint Tests|Tactic Constraint Tests]]
- [[_COMMUNITY_KG Mathlib Seeder|KG Mathlib Seeder]]
- [[_COMMUNITY_ArXiv Cycle Runner|ArXiv Cycle Runner]]
- [[_COMMUNITY_E2E Integration Tests|E2E Integration Tests]]
- [[_COMMUNITY_Step Entailment Tests|Step Entailment Tests]]
- [[_COMMUNITY_KG REST API|KG REST API]]
- [[_COMMUNITY_Tactic Training Functions|Tactic Training Functions]]
- [[_COMMUNITY_MiniF2F Regression Tests|MiniF2F Regression Tests]]
- [[_COMMUNITY_Quality Score Inference|Quality Score Inference]]
- [[_COMMUNITY_Conjecture Generator|Conjecture Generator]]
- [[_COMMUNITY_Research Conjecture Script|Research Conjecture Script]]
- [[_COMMUNITY_Worker Result Merger|Worker Result Merger]]
- [[_COMMUNITY_Backend Flags Tests|Backend Flags Tests]]
- [[_COMMUNITY_Ponder Backend Tests|Ponder Backend Tests]]
- [[_COMMUNITY_Promotion Policy Tests|Promotion Policy Tests]]
- [[_COMMUNITY_Roundtrip Translation Tests|Roundtrip Translation Tests]]
- [[_COMMUNITY_Tactic Training Tests|Tactic Training Tests]]
- [[_COMMUNITY_Backend Health Mocks|Backend Health Mocks]]
- [[_COMMUNITY_Export Tactic Triples|Export Tactic Triples]]
- [[_COMMUNITY_KG API Endpoints|KG API Endpoints]]
- [[_COMMUNITY_Distributed Cache Tests|Distributed Cache Tests]]
- [[_COMMUNITY_Test Conftest|Test Conftest]]
- [[_COMMUNITY_Scripts Init|Scripts Init]]
- [[_COMMUNITY_Statement Translator Gate|Statement Translator Gate]]
- [[_COMMUNITY_MCTS Node Roundtrip|MCTS Node Roundtrip]]
- [[_COMMUNITY_Calibration Temperature Scale|Calibration Temperature Scale]]
- [[_COMMUNITY_MCTS Run Regression|MCTS Run Regression]]
- [[_COMMUNITY_Conjecture JSON Parser|Conjecture JSON Parser]]
- [[_COMMUNITY_UCB1 Tactic Policy|UCB1 Tactic Policy]]
- [[_COMMUNITY_Reproducibility Readme|Reproducibility Readme]]
- [[_COMMUNITY_Premise Retrieval Rationale A|Premise Retrieval Rationale A]]
- [[_COMMUNITY_Premise Retrieval Rationale B|Premise Retrieval Rationale B]]
- [[_COMMUNITY_UCT Score Tests|UCT Score Tests]]
- [[_COMMUNITY_REPL Dojo Parsing|REPL Dojo Parsing]]
- [[_COMMUNITY_Step Entailment Assess|Step Entailment Assess]]
- [[_COMMUNITY_Benchmark Problem Result|Benchmark Problem Result]]
- [[_COMMUNITY_Theorem Extractor Entry|Theorem Extractor Entry]]
- [[_COMMUNITY_Requirements Dependencies|Requirements Dependencies]]

## God Nodes (most connected - your core abstractions)
1. `REPLDojo` - 113 edges
2. `TacticState` - 109 edges
3. `PremiseRetriever` - 106 edges
4. `LeanREPLServer` - 95 edges
5. `ProofFinished` - 90 edges
6. `LeanError` - 90 edges
7. `TacticState` - 80 edges
8. `LeanError` - 80 edges
9. `PremiseEntry` - 80 edges
10. `ProofFinished` - 78 edges

## Surprising Connections (you probably didn't know these)
- `Assumption Grounding Policy` --conceptually_related_to--> `load_kg_tier_names()`  [INFERRED]
  OBJECTIVES.md → scripts/premise_retrieval.py
- `sanitize_tactic_candidate Tests` --semantically_similar_to--> `train_sft Function`  [INFERRED] [semantically similar]
  tests/test_tactic_constraints.py → scripts/tactic_training.py
- `LeanDojo Migration PR Plan` --references--> `Z3 SMT Backend (optional)`  [EXTRACTED]
  knowledge/leandojo_migration_pr_plan.md → scripts/step_entailment_checker.py
- `Mathlib Embeddings Index README` --references--> `PremiseRetriever Class`  [EXTRACTED]
  data/mathlib_embeddings/README.md → scripts/premise_retrieval.py
- `Bridge proof integration tests.  Tests the full bridge-proof pipeline with a syn` --uses--> `BridgeExecutionResult`  [INFERRED]
  tests/test_bridge_proofs.py → scripts/bridge_proofs.py

## Hyperedges (group relationships)
- **Proof Backend Selection and Health Check Pipeline** — test_proof_backend_ProofBackendFlags, test_proof_backend_resolve_backend_choice, test_prove_with_ponder_backend_open_dojo, test_proof_backend_BackendStartupSummary [EXTRACTED 0.95]
- **Tactic Training SFT→RL Pipeline** — scripts_tactic_training_export_triples, scripts_tactic_training_train_sft, scripts_tactic_training_train_rl_refinement [EXTRACTED 0.95]
- **MCTS Proof Search Benchmark Loop** — scripts_benchmark_minif2f_attempt_proof, scripts_benchmark_minif2f_run_benchmark, test_regression_minif2f_REGRESSION_CASES [INFERRED 0.82]
- **arXiv to Lean 4 Full Ingestion Pipeline** — arxiv_fetcher_fetch_source, latex_preprocessor_collect_definitions, theorem_extractor_extract_theorems, statement_translator_translate_statement, prove_with_ponder_main, pipeline_status_build_ledger_entry [EXTRACTED 0.95]
- **KG Trust Classification and Promotion Pipeline** — pipeline_status_verification_status, kg_writer_classification, kg_writer_propagate_ungroundedness, kg_writer_build_kg, kg_writer_promotion_manifest [EXTRACTED 0.95]
- **Assumption Grounding via Bridge Proofs** — bridge_proofs_suggest_bridge_candidates, bridge_proofs_check_entailment_z3, bridge_proofs_execute_bridge_proof_lean, bridge_proofs_execute_bridge_chain [EXTRACTED 0.90]
- **Proof State Abstraction Layer (TacticState, ProofFinished, LeanError shared across backends)** — lean_repl_dojo_TacticState, lean_repl_server_TacticState, lean_repl_dojo_ProofFinished, lean_repl_server_LeanREPLServer, lean_repl_dojo_REPLDojo [EXTRACTED 1.00]
- **KG Verification and Promotion Pipeline (retrieval, proof, import validation, ledger)** — premise_retrieval_query_with_tier_preference, import_validator_validate_ledger_entry, objectives_verification_contract, premise_retrieval_load_kg_tier_names [INFERRED 0.85]
- **Batch Proof Orchestration (sorry-theorem extraction, prove_one, bridge loop, distributed cache)** — prove_arxiv_batch_SorryTheorem, prove_arxiv_batch_prove_one, prove_arxiv_batch_bridge_loop, distributed_proof_cache_DistributedProofCache [EXTRACTED 0.90]

## Communities

### Community 0 - "Benchmark MiniF2F Suite"
Cohesion: 0.07
Nodes (129): Load miniF2F problems from HuggingFace.      Returns a list of dicts with keys:, Extract the Lean 4 theorem/lemma statement from a miniF2F row.      Some rows co, Extract the import header from a miniF2F row, if present., Write a miniF2F problem to a per-worker scratch Lean file.      Each worker gets, Extract the theorem name from a Lean 4 statement., Run one proof attempt with a real Lean execution loop.      Architecture:, Run the full miniF2F benchmark and return structured results., Map raw error text to a compact diagnostic category. (+121 more)

### Community 1 - "MCTS Ponder Loop Core"
Cohesion: 0.05
Nodes (73): MCTS Node Data Structure, Worker Results Merger, adaptive_act_budget(), _balanced_delimiters(), build_parser(), build_system_prompt(), _chat_complete(), _estimate_state_complexity() (+65 more)

### Community 2 - "MCTS Tree Search Engine"
Cohesion: 0.06
Nodes (66): analyze_tree(), _append_value_sample(), apply_calibration(), backpropagate(), _backpropagate_draft(), _backpropagate_state(), best_path_from_root(), _best_proof_path() (+58 more)

### Community 3 - "Premise Retrieval Build"
Cohesion: 0.05
Nodes (40): build(), build_parser(), _cmd_build(), _cmd_download(), _cmd_fetch_mathlib(), _cmd_query(), _dot(), download_precomputed() (+32 more)

### Community 4 - "ArXiv Cycle Daemon"
Cohesion: 0.05
Nodes (55): _build_parser(), cleanup_paper(), _count_proven(), load_processed(), load_queue(), main(), mark_processed(), preflight_check() (+47 more)

### Community 5 - "Pipeline Status Ledger"
Cohesion: 0.06
Nodes (58): Enum, _all_assumptions_grounded(), Assumption, _auto_reproducible_env(), build_ledger_entry(), _clamp01(), classify_theorem_result(), derive_step_verdict() (+50 more)

### Community 6 - "Statement Translator TC"
Cohesion: 0.06
Nodes (56): Mathlib TC Hierarchy Parser (Phase 1), adversarial_translation_check(), _build_class_stubs(), _build_parser(), _build_repair_hint(), _build_stubs(), _check_vacuous(), _confidence_from_translation_state() (+48 more)

### Community 7 - "Bridge Proof Execution"
Cohesion: 0.06
Nodes (44): BridgeExecutionResult Dataclass, BridgeCandidate, BridgeChainPlan, BridgeExecutionResult, BridgePlan, build_bridge_plan(), _build_lean_bridge_script(), _build_z3_formula() (+36 more)

### Community 8 - "Lean REPL Dojo Backend"
Cohesion: 0.05
Nodes (48): LeanError (REPLDojo), ProofFinished (REPLDojo), REPLDojo Class, TacticState (REPLDojo), _extract_lean_error(), _extract_unsolved_goals(), _find_decl_line(), Incremental Lake Build Protocol (+40 more)

### Community 9 - "ArXiv to Lean Pipeline"
Cohesion: 0.09
Nodes (26): _build_parser(), _extract_cited_refs_from_tex(), _force_decl_name(), _get_cache(), _lean_name(), main(), pipeline_results_to_json(), PipelineResult (+18 more)

### Community 10 - "ArXiv Fetcher Cycle"
Cohesion: 0.08
Nodes (37): CycleResult Dataclass, _build_parser(), _fetch_bytes(), fetch_source(), find_main_tex(), main(), Download arxiv source tarball for *paper_id* and extract .tex files to *out_dir*, Heuristic: return the .tex file most likely to be the main document.      Priori (+29 more)

### Community 11 - "MCTS Core Tests"
Cohesion: 0.09
Nodes (10): _node(), test_fit_platt_calibrator_perfect_signal(), test_node_children_default_empty(), test_node_mean_value(), test_node_mean_value_zero_visits(), test_node_tactic_history_default_empty(), test_node_ucb_infinite_for_unvisited(), test_uct_exploration_increases_score_for_less_visited() (+2 more)

### Community 12 - "Lean REPL Dojo Tests"
Cohesion: 0.1
Nodes (10): _error_result(), _make_dojo(), _ok_result(), test_repldojo_enter_returns_tactic_state(), test_repldojo_restores_file_on_exit(), test_repldojo_run_tac_lean_error(), test_repldojo_run_tac_proof_finished(), test_repldojo_run_tac_unsolved_goals() (+2 more)

### Community 13 - "Premise Encoder Backend"
Cohesion: 0.1
Nodes (23): Encoder, Minimal interface for a text encoder., BackendStartupSummary, build_backend_health_report(), build_backend_startup_summary(), classify_backend_init_error(), detect_extractdata_patch_status(), emit_backend_parity_event() (+15 more)

### Community 14 - "Batch Proof Orchestration"
Cohesion: 0.15
Nodes (19): SorryTheorem Dataclass, Batch Proof Main (CLI), _bridge_hints_from_ledger_entry(), Bridge Proof Execution Loop, _collect_bridge_targets(), _collect_lean_files(), _extract_sorry_theorems(), _load_ledger_entry_for_theorem() (+11 more)

### Community 15 - "Mathlib Contribution"
Cohesion: 0.17
Nodes (17): check_novelty(), _elan_env(), _exact_statement_search(), generate_contribution(), _infer_imports(), _name_check(), Ask LeanStral if this statement is semantically equivalent to a known Mathlib th, Three-stage novelty check.      Stage 1: Name collision (#check @name)     Stage (+9 more)

### Community 16 - "TC Graph Builder"
Cohesion: 0.18
Nodes (16): build_ancestor_map(), build_graph(), generate_system_prompt_rules(), main(), _parse_parents(), BFS transitive closure: {name: [all ancestors]}., TOON (Token-Optimized ONe-shot) packing.      Greedily packs as many docstring c, Run #check on a Lean4 name to confirm it exists in Mathlib. (+8 more)

### Community 17 - "Benchmark Distributed Config"
Cohesion: 0.12
Nodes (17): conftest.py Scripts Path Setup, BASELINES Literature Benchmarks, BenchmarkResult Dataclass, DistributedProofCache Usage in Benchmark, _attempt_proof Function, run_benchmark Function, BridgeExecutionResult, TestExecuteBridgeChain Suite (+9 more)

### Community 18 - "Benchmark Results"
Cohesion: 0.23
Nodes (11): _attempt_proof(), BenchmarkResult, _categorize_error(), _extract_header(), _extract_lean_statement(), _extract_theorem_name(), _load_minif2f(), main() (+3 more)

### Community 19 - "CLI Health Check Tests"
Cohesion: 0.25
Nodes (1): TestCLIHealthCheckReal

### Community 20 - "Proof Backend Tests"
Cohesion: 0.14
Nodes (1): Unit tests for proof backend scaffold (phase-1 migration).

### Community 21 - "Tactic Training Pipeline"
Cohesion: 0.33
Nodes (13): _build_parser(), _evaluate_binary(), export_triples(), _featurize(), _iter_ledger_entries(), _iter_triples(), main(), _outcome_from_result() (+5 more)

### Community 22 - "Import Validator"
Cohesion: 0.22
Nodes (13): ImportValidationResult Dataclass, _build_check_file(), _build_elaborate_file(), _build_parser(), _elan_env(), ImportValidationResult, main(), Run import validation on a ledger entry dict. Returns updated entry.      If val (+5 more)

### Community 23 - "Step Entailment Z3"
Cohesion: 0.27
Nodes (12): EntailmentAssessment Dataclass, assess_proof_draft(), assess_step_entailment(), _build_z3_expr(), ConstraintAtom, _dispatch_route(), EntailmentAssessment, _extract_atoms() (+4 more)

### Community 24 - "Tactic Constraint Tests"
Cohesion: 0.17
Nodes (1): Tests for syntax-constrained tactic filtering and error-class dispatch.

### Community 25 - "KG Mathlib Seeder"
Cohesion: 0.26
Nodes (11): _build_kg_entry(), _build_parser(), _generate_description(), _infer_namespace(), _load_index_entries(), main(), Ask Leanstral to describe a Mathlib lemma in one sentence., Write GROUNDED_MATHLIB KG entries from the embedding index.      Returns total e (+3 more)

### Community 26 - "ArXiv Cycle Runner"
Cohesion: 0.4
Nodes (10): _already_completed_ok(), _build_parser(), _compute_sha256(), CycleResult, _load_manifest_results(), _load_paper_ids(), _load_verification_counts(), main() (+2 more)

### Community 27 - "E2E Integration Tests"
Cohesion: 0.25
Nodes (6): test_full_pipeline_proves_simple_theorem(), test_independent_lean_verify_correct_proof(), test_independent_lean_verify_wrong_proof(), test_repldojo_proves_trivial_theorem(), test_repldojo_reports_lean_error_on_bad_tactic(), test_repldojo_tactic_state_on_partial_proof()

### Community 28 - "Step Entailment Tests"
Cohesion: 0.48
Nodes (6): _ob(), Unit tests for routed entailment checking., test_explicit_lean_error_is_flawed(), test_linear_atoms_are_checked_or_marked_unknown_without_solver(), test_nonlinear_steps_are_not_misreported_as_z3_verified(), test_quantified_steps_are_routed_to_lean_required()

### Community 29 - "KG REST API"
Cohesion: 0.29
Nodes (2): Enqueue an arXiv paper for pipeline processing (non-blocking).      Spawns ``arx, verify()

### Community 30 - "Tactic Training Functions"
Cohesion: 0.38
Nodes (7): _featurize Hash-Based Vectorizer, train_rl_refinement REINFORCE Function, train_sft Function, classify_lean_error Tests, repair_hint_for_error_class Tests, sanitize_tactic_candidate Tests, train_sft and train_rl_refinement Tests

### Community 31 - "MiniF2F Regression Tests"
Cohesion: 0.4
Nodes (4): miniF2F regression test set.  These 5 problems were solved by state-MCTS (v2 ben, State-MCTS must still solve each regression problem., _run(), test_regression()

### Community 32 - "Quality Score Inference"
Cohesion: 0.4
Nodes (1): Tests for automatic translation/status quality score inference.

### Community 33 - "Conjecture Generator"
Cohesion: 0.7
Nodes (4): _extract_json_payload(), _loads_lenient_json(), main(), _response_text()

### Community 34 - "Research Conjecture Script"
Cohesion: 0.7
Nodes (4): main(), _normalize_lean_decl(), _sanitize_name(), _write_conjecture_lean()

### Community 35 - "Worker Result Merger"
Cohesion: 0.6
Nodes (4): _load_results(), main(), merge_results(), Load per-problem result JSON files from a worker output directory.

### Community 36 - "Backend Flags Tests"
Cohesion: 0.4
Nodes (5): BackendStartupSummary Dataclass, ProofBackendFlags Dataclass, emit_backend_parity_event Function, resolve_backend_choice Function, _open_dojo Parity Event Tests

### Community 37 - "Ponder Backend Tests"
Cohesion: 0.5
Nodes (1): Integration tests for prove_with_ponder backend opening/parity logging.

### Community 38 - "Promotion Policy Tests"
Cohesion: 0.5
Nodes (4): ProvenanceLink Dataclass, VerificationStatus Enum, build_ledger_entry Promotion Gate Tests, infer_quality_scores Tests

### Community 39 - "Roundtrip Translation Tests"
Cohesion: 0.67
Nodes (0): 

### Community 40 - "Tactic Training Tests"
Cohesion: 0.67
Nodes (0): 

### Community 41 - "Backend Health Mocks"
Cohesion: 0.67
Nodes (3): TestCLIHealthCheckReal Suite, classify_backend_init_error Function, check_backend_health Tests

### Community 42 - "Export Tactic Triples"
Cohesion: 0.67
Nodes (3): export_triples Function, train_stub Function, export_triples Tests

### Community 43 - "KG API Endpoints"
Cohesion: 0.67
Nodes (3): KG FastAPI App, GET /kg/query Filtered KG Query Endpoint, POST /verify Background Pipeline Trigger

### Community 44 - "Distributed Cache Tests"
Cohesion: 1.0
Nodes (0): 

### Community 45 - "Test Conftest"
Cohesion: 1.0
Nodes (1): Shared pytest fixtures for DESol tests.

### Community 46 - "Scripts Init"
Cohesion: 1.0
Nodes (1): DESol scripts package for Lean theorem prover MCTS framework.

### Community 47 - "Statement Translator Gate"
Cohesion: 1.0
Nodes (2): TestStatementTranslatorDefGate Suite, roundtrip_translation_check Tests

### Community 48 - "MCTS Node Roundtrip"
Cohesion: 1.0
Nodes (2): MCTSNode Unit Tests, extract_proof_state_features Tests

### Community 49 - "Calibration Temperature Scale"
Cohesion: 1.0
Nodes (2): Platt Calibration Tests, temperature_scale Cross-Module Tests

### Community 50 - "MCTS Run Regression"
Cohesion: 1.0
Nodes (2): run_mcts Integration Tests, miniF2F Regression Cases

### Community 51 - "Conjecture JSON Parser"
Cohesion: 1.0
Nodes (2): _extract_json_payload, conjecture_generator Main

### Community 52 - "UCB1 Tactic Policy"
Cohesion: 1.0
Nodes (2): Tactic Policy Scorer (SFT/RL weights), UCB1 Selection Strategy

### Community 53 - "Reproducibility Readme"
Cohesion: 1.0
Nodes (2): miniF2F 28.7% pass@1 Benchmark Results, Reproducibility README (miniF2F pinned result)

### Community 54 - "Premise Retrieval Rationale A"
Cohesion: 1.0
Nodes (1): Build a retrieval index.          Args:             entries: Premise corpus.

### Community 55 - "Premise Retrieval Rationale B"
Cohesion: 1.0
Nodes (1): Load index from a directory produced by save_np.

### Community 56 - "UCT Score Tests"
Cohesion: 1.0
Nodes (1): uct_score Tests

### Community 57 - "REPL Dojo Parsing"
Cohesion: 1.0
Nodes (1): lean_repl_dojo Parsing Helper Tests

### Community 58 - "Step Entailment Assess"
Cohesion: 1.0
Nodes (1): assess_step_entailment Tests

### Community 59 - "Benchmark Problem Result"
Cohesion: 1.0
Nodes (1): ProblemResult Dataclass

### Community 60 - "Theorem Extractor Entry"
Cohesion: 1.0
Nodes (1): TheoremEntry Dataclass

### Community 61 - "Requirements Dependencies"
Cohesion: 1.0
Nodes (1): Project Python Dependencies

## Knowledge Gaps
- **192 isolated node(s):** `Unit tests for proof backend scaffold (phase-1 migration).`, `Tests for syntax-constrained tactic filtering and error-class dispatch.`, `miniF2F regression test set.  These 5 problems were solved by state-MCTS (v2 ben`, `State-MCTS must still solve each regression problem.`, `Integration tests for prove_with_ponder backend opening/parity logging.` (+187 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Distributed Cache Tests`** (2 nodes): `test_distributed_proof_cache_roundtrip()`, `test_distributed_proof_cache.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Test Conftest`** (2 nodes): `Shared pytest fixtures for DESol tests.`, `conftest.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scripts Init`** (2 nodes): `DESol scripts package for Lean theorem prover MCTS framework.`, `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Statement Translator Gate`** (2 nodes): `TestStatementTranslatorDefGate Suite`, `roundtrip_translation_check Tests`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `MCTS Node Roundtrip`** (2 nodes): `MCTSNode Unit Tests`, `extract_proof_state_features Tests`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Calibration Temperature Scale`** (2 nodes): `Platt Calibration Tests`, `temperature_scale Cross-Module Tests`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `MCTS Run Regression`** (2 nodes): `run_mcts Integration Tests`, `miniF2F Regression Cases`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Conjecture JSON Parser`** (2 nodes): `_extract_json_payload`, `conjecture_generator Main`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `UCB1 Tactic Policy`** (2 nodes): `Tactic Policy Scorer (SFT/RL weights)`, `UCB1 Selection Strategy`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Reproducibility Readme`** (2 nodes): `miniF2F 28.7% pass@1 Benchmark Results`, `Reproducibility README (miniF2F pinned result)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Premise Retrieval Rationale A`** (1 nodes): `Build a retrieval index.          Args:             entries: Premise corpus.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Premise Retrieval Rationale B`** (1 nodes): `Load index from a directory produced by save_np.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `UCT Score Tests`** (1 nodes): `uct_score Tests`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `REPL Dojo Parsing`** (1 nodes): `lean_repl_dojo Parsing Helper Tests`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Step Entailment Assess`** (1 nodes): `assess_step_entailment Tests`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Benchmark Problem Result`** (1 nodes): `ProblemResult Dataclass`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Theorem Extractor Entry`** (1 nodes): `TheoremEntry Dataclass`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Requirements Dependencies`** (1 nodes): `Project Python Dependencies`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PremiseRetriever` connect `Benchmark MiniF2F Suite` to `MCTS Ponder Loop Core`, `MCTS Tree Search Engine`, `Premise Retrieval Build`, `Bridge Proof Execution`, `Benchmark Results`?**
  _High betweenness centrality (0.174) - this node is a cross-community bridge._
- **Why does `arXiv to Lean End-to-End Pipeline` connect `ArXiv Fetcher Cycle` to `MCTS Ponder Loop Core`, `ArXiv Cycle Daemon`, `Pipeline Status Ledger`, `Statement Translator TC`?**
  _High betweenness centrality (0.149) - this node is a cross-community bridge._
- **Why does `main()` connect `MCTS Ponder Loop Core` to `ArXiv Fetcher Cycle`?**
  _High betweenness centrality (0.116) - this node is a cross-community bridge._
- **Are the 106 inferred relationships involving `REPLDojo` (e.g. with `Unit tests for lean_repl_dojo.py.  Tests cover the pure parsing helpers and the` and `returncode=0 but 'declaration uses sorry' at the decl line → LeanError.`) actually correct?**
  _`REPLDojo` has 106 INFERRED edges - model-reasoned connections that need verification._
- **Are the 106 inferred relationships involving `TacticState` (e.g. with `Unit tests for lean_repl_dojo.py.  Tests cover the pure parsing helpers and the` and `returncode=0 but 'declaration uses sorry' at the decl line → LeanError.`) actually correct?**
  _`TacticState` has 106 INFERRED edges - model-reasoned connections that need verification._
- **Are the 99 inferred relationships involving `PremiseRetriever` (e.g. with `Unit tests for premise_retrieval.py.` and `HasGaussianLaw.integrable should rank highly for a gaussian-related query.`) actually correct?**
  _`PremiseRetriever` has 99 INFERRED edges - model-reasoned connections that need verification._
- **Are the 76 inferred relationships involving `LeanREPLServer` (e.g. with `TestStateMCTSMocked` and `TestStatementTranslatorDefGate`) actually correct?**
  _`LeanREPLServer` has 76 INFERRED edges - model-reasoned connections that need verification._