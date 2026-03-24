# DESol

Phase 1 environment setup for Lean 4 + Python + Mistral.

## What is configured

- Lean 4 via Elan (`lean` + `lake`)
- A Lean project scaffold (`lakefile.toml`, `lean-toolchain`)
- Python 3.11 conda environment: `desol-py311`
- Python packages:
	- `lean-dojo` (Lean proof-state tooling)
	- `mistralai` (official Mistral API client)
	- `python-dotenv` (`.env` loading)

## Prerequisites

- Linux
- Miniconda/Conda installed
- Elan installed

## Activate toolchains

Lean binaries are managed by Elan:

```bash
~/.elan/bin/lean --version
~/.elan/bin/lake --version
```

Activate Python env:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate desol-py311
```

## API key setup

Create a local `.env` file (do not commit it):

```bash
cp .env.example .env
```

Then set:

```env
MISTRAL_API_KEY=your_real_key_here
MISTRAL_MODEL=labs-leanstral-2603
```

## Smoke test

Run:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate desol-py311
python scripts/smoke_test.py
```

This verifies:

- Lean and Lake are callable
- `lean_dojo` imports successfully
- Mistral client initializes
- If `MISTRAL_API_KEY` is present, performs a tiny chat completion against `MISTRAL_MODEL` (defaults to `labs-leanstral-2603`)

## Lean project sanity check

```bash
~/.elan/bin/lake build
```

## Phase 2: URM Ponder Loop (Micro-Search)

Run the iterative think/continue/tactic loop:

```bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate desol-py311
python scripts/ponder_loop.py \
	--lean-state "goal: prove True" \
	--max-turns 5 \
	--show-thoughts
```

Or pass a file that contains your current Lean proof state:

```bash
python scripts/ponder_loop.py --lean-state-file path/to/state.txt
```

Behavior:

- Re-queries when `<continue>` appears.
- Appends assistant `<think>` trace to message history each turn.
- Stops when exactly one `<tactic>...</tactic>` is produced.

## Phase 3: ACT Controller

The ponder loop now includes explicit external halting controls:

- Adaptive ACT budget: if `--max-turns 0` (default), the loop computes its own budget from Lean-state complexity.
- Optional fixed ACT budget: set `--max-turns N` if you want a hard budget for experiments.
- Confidence halting: model includes `CONFIDENCE: x.xx` inside `<think>`, and if score is above `--confidence-threshold` (default `0.9`), the next prompt immediately asks for a tactic.
- Forced tactic near ACT limit: final ACT step asks for exactly one `<tactic>...</tactic>`.
- Trivial-state bypass: for very short/simple Lean states (default `--trivial-state-chars 80`), the loop skips heavy pondering and directly requests a tactic.

Useful flags:

- `--confidence-threshold 0.9`
- `--trivial-state-chars 80`
- `--min-act-turns 2`
- `--max-act-turns 8`

## Phase 4: MCTS Skeleton (Macro-Search)

The new script [scripts/mcts_search.py](scripts/mcts_search.py) implements:

- `MCTSNode` storing Lean state, incoming tactic, visits, and total value.
- UCT selection: standard `Q + c * sqrt(log(N_parent)/N_child)`.
- Expansion: calls the model for 3-5 distinct tactic options and compiles them in LeanDojo.
- Evaluation: asks the model for a scalar value in `[0, 1]` using `<value>...</value>`.
- Backpropagation: sends that value up the selected path.
- LeanDojo preflight before search starts.
- Automatic fallback mode when LeanDojo tracing fails.

Example run:

```bash
python scripts/mcts_search.py \
	--project-root . \
	--file Desol/Basic.lean \
	--theorem basic_demo_true \
	--iterations 20
```

Fallback controls:

- `--fallback-mode model`: if LeanDojo preflight fails, run model-only macro-search.
- `--fallback-mode none`: fail fast if LeanDojo is unavailable.
- `--skip-preflight`: bypass preflight and run selected mode directly.
- `--auto-patch-leandojo`: apply known `ExtractData.lean` compatibility patches before preflight.

## Phase 5: Hello World Integration Test

Run the end-to-end plumbing test on a simple target theorem (`a + b = b + a`):

```bash
python scripts/hello_world_integration.py \
	--project-root . \
	--telemetry-file logs/hello_world_api_telemetry.json
```

This dry run verifies:

- root node initialization
- URM `<think>` loop execution
- tactic extraction
- tactic verification in Lean via `lake env lean`
- tree child insertion and backprop update
- API telemetry persistence to JSON

### Execute Tactics Directly In Lean (LeanDojo)

This command connects the ponder loop to LeanDojo and executes tactics on a real theorem state:

```bash
python scripts/prove_with_ponder.py \
	--project-root . \
	--file Desol/Basic.lean \
	--theorem basic_demo_true
```

Useful knobs:

- `--max-steps`: maximum Lean tactic applications.
- `--max-attempts-per-state`: retries when Lean rejects a tactic.
- `--ponder-max-turns`: internal think/continue cycles before forcing a tactic.