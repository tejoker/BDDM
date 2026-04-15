"""Re-export shim — mcts_search split into scripts/mcts/ package.

All existing ``from mcts_search import X`` and ``import mcts_search`` usage
continues to work unchanged.  Attribute writes (monkeypatch.setattr / patch())
are propagated to all submodules via a custom ModuleType subclass so that
test patches reach the globals that run_mcts / run_state_mcts actually look up.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# Ensure scripts/ is on sys.path so ``import mcts`` resolves to the sibling
# mcts/ package rather than the stdlib mcts (which doesn't exist, but
# belt-and-suspenders).
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mcts._classic as _classic_mod
import mcts._state as _state_mod

# ---------------------------------------------------------------------------
# Re-export all public symbols so that ``from mcts_search import X`` works.
# ---------------------------------------------------------------------------
from mcts._classic import *  # noqa: F401, F403
from mcts._classic import (  # noqa: F401  (private symbols used in tests/scripts)
    _logit,
    _sigmoid,
    _TacticPolicyScorer,
    _TACTIC_POLICY,
    _DEFAULT_CALIBRATION_TEMPERATURE,
    _CALIBRATION_PATH,
    _PLATT_PARAMS_CACHE,
    _get_platt_params,
    _append_value_sample,
    _response_to_text,
    _draft_uct_score,
    _select_draft_leaf,
    _backpropagate_draft,
    _step_records_to_dicts,
    _evaluate_draft_result,
    _draft_path,
    _expand_draft_node,
    _isolate_project_for_worker,
    _run_draft_mcts_worker,
    _collect_proof_trace,
    _extract_draft_best_value,
    _HAS_REPLDOJO,
    _REPLDOJO_IMPORT_ERROR,
    REPLDojo,
    TacticState,
    LeanError,
    ProofFinished,
    ProofGivenUp,
    LeanREPLServer,
)
from mcts._state import *    # noqa: F401, F403
from mcts._state import (    # noqa: F401  (private symbols)
    _ARITH_HINT_RE,
    _CONTRADICTION_HINT_RE,
    _QUANTIFIER_RE,
    _STATE_TOKEN_RE,
    _normalized_state_tokens,
    _state_uct,
    _select_state_leaf,
    _goal_value,
    _expand_state_node,
    _backpropagate_state,
    _best_proof_path,
    _kg_record_proof,
    _build_compounding_retriever,
    _retrieve_compounding_context,
)


# ---------------------------------------------------------------------------
# Override module class so that setattr(mcts_search_module, name, val) —
# as used by unittest.mock.patch and pytest monkeypatch — propagates to
# all submodules.  Plain module-level __setattr__ is NOT invoked by Python's
# attribute machinery; a ModuleType subclass IS.
# ---------------------------------------------------------------------------

class _BroadcastModule(types.ModuleType):
    """ModuleType subclass that broadcasts setattr writes to submodules."""

    _submodules: list = []

    def __setattr__(self, name: str, value) -> None:
        # Write to this module's own __dict__ first.
        super().__setattr__(name, value)
        # Propagate to every submodule that already has the name so that
        # patched names are visible in the submodule globals where functions
        # actually look them up.
        for mod in self.__dict__.get("_submodules", []):
            if name in mod.__dict__:
                mod.__dict__[name] = value

    def __getattr__(self, name: str):
        for mod in self.__dict__.get("_submodules", []):
            try:
                return getattr(mod, name)
            except AttributeError:
                continue
        raise AttributeError(f"module 'mcts_search' has no attribute {name!r}")


# Swap the module object in sys.modules with a _BroadcastModule instance
# that carries all the already-imported names.
_current_dict = dict(sys.modules[__name__].__dict__)
_broadcast = _BroadcastModule(__name__, __doc__)
_broadcast.__dict__.update(_current_dict)
_broadcast._submodules = [_classic_mod, _state_mod]
sys.modules[__name__] = _broadcast


if __name__ == "__main__":
    from mcts._classic import main as _main
    import sys as _sys
    _sys.exit(_main())
