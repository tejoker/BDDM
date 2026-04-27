from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

from lean_repl_server import _extract_json_message


def test_extract_json_message_with_clean_payload() -> None:
    payload = '{ "env": 1, "messages": [] }\n'
    obj, rest = _extract_json_message(payload)
    assert obj is not None
    assert obj.get("env") == 1
    assert rest.strip() == ""


def test_extract_json_message_with_noisy_prefix() -> None:
    payload = "building...\nwarning...\n{ \"proofState\": 7, \"goals\": [\"⊢ True\"] }\n"
    obj, rest = _extract_json_message(payload)
    assert obj is not None
    assert obj.get("proofState") == 7
    assert rest.strip() == ""


def test_extract_json_message_incomplete_keeps_buffer() -> None:
    payload = '{ "proofState": 7, "goals": ['
    obj, rest = _extract_json_message(payload)
    assert obj is None
    assert rest == payload
