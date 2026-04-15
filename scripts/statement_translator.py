"""Re-export shim — statement_translator split into scripts/translator/ package.

All existing ``from statement_translator import X`` usage continues to work.
Attribute writes (monkeypatch.setattr / patch()) are propagated to the
submodule via a custom ModuleType subclass so that test patches reach the
globals that translate_statement / _chat_complete actually look up.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# Ensure scripts/ is on sys.path so ``import translator`` resolves correctly.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import translator._translate as _translate_mod

from translator._translate import *  # noqa: F401, F403
from translator._translate import (  # noqa: F401  (private symbols used in tests)
    _validate_signature,
    _confidence_from_translation_state,
    _STUB_SYSTEM,
    _UNKNOWN_ID_RE,
    _chat_complete,
)


# ---------------------------------------------------------------------------
# Override module class so that setattr(statement_translator_module, name, val)
# — as used by unittest.mock.patch and pytest monkeypatch — propagates to
# the submodule.  Plain module-level __setattr__ is NOT invoked by Python's
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
        raise AttributeError(f"module 'statement_translator' has no attribute {name!r}")


# Swap the module object in sys.modules with a _BroadcastModule instance
# that carries all the already-imported names.
_current_dict = dict(sys.modules[__name__].__dict__)
_broadcast = _BroadcastModule(__name__, __doc__)
_broadcast.__dict__.update(_current_dict)
_broadcast._submodules = [_translate_mod]
sys.modules[__name__] = _broadcast
