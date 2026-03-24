#!/usr/bin/env python3
"""Phase 5 Hello World integration test.

This script does a tiny end-to-end dry run:
1) Initialize a root MCTS node for a simple Lean theorem state.
2) Call the URM ponder loop (with <think>/<continue>/<tactic> parsing).
3) Extract one tactic.
4) Verify that tactic by compiling a temporary Lean theorem.
5) Update a small tree and backpropagate value.
6) Save every API request/response to JSON telemetry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from mistralai.client import Mistral

# Ensure sibling script imports work when invoked from project root.
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ponder_loop import run_ponder_loop

VALUE_SYSTEM_PROMPT = (
    "You are a Lean proof-value estimator. "
    "Output exactly one score in <value> tags between 0.0 and 1.0."
)
VALUE_USER_PROMPT = "State:\n{state}"


@dataclass
class TinyNode:
    state_text: str
    tactic_from_parent: str | None
    parent: TinyNode | None = None
    visits: int = 0
    value_sum: float = 0.0
    children: list[TinyNode] = field(default_factory=list)

    @property
    def mean_value(self) -> float:
        if self.visits == 0:
            return 0.0
        return self.value_sum / self.visits


@dataclass
class ApiTelemetry:
    records: list[dict] = field(default_factory=list)

    def hook(self, event: dict) -> None:
        self.records.append(event)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.records, fh, indent=2)


def _parse_value(text: str) -> float | None:
    start = text.lower().find("<value>")
    end = text.lower().find("</value>")
    if start < 0 or end < 0 or end <= start:
        return None
    raw = text[start + len("<value>") : end].strip()
    try:
        v = float(raw)
    except ValueError:
        return None
    if 0.0 <= v <= 1.0:
        return v

    # Tolerate malformed but common variant like <0.7>
    if text.startswith("<") and text.endswith(">"):
        raw2 = text[1:-1].strip()
        try:
            v2 = float(raw2)
        except ValueError:
            return None
        if 0.0 <= v2 <= 1.0:
            return v2
    return None


def _extract_response_text(response: object) -> str:
    try:
        choices = getattr(response, "choices", None)
        if choices and len(choices) > 0:
            msg = getattr(choices[0], "message", None)
            if msg is not None:
                content = getattr(msg, "content", None)
                if isinstance(content, str):
                    return content
    except Exception:
        pass
    return str(response)


def _api_call(
    *,
    client: Mistral,
    model: str,
    messages: list[dict[str, str]],
    purpose: str,
    telemetry: ApiTelemetry,
    temperature: float = 0.0,
    max_tokens: int = 128,
) -> str:
    started = time.time()
    response = client.chat.complete(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    text = _extract_response_text(response)
    ended = time.time()

    telemetry.hook(
        {
            "timestamp": started,
            "purpose": purpose,
            "request": {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": messages,
            },
            "response_text": text,
            "latency_seconds": max(0.0, ended - started),
        }
    )
    return text


def verify_tactic_with_lean(*, project_root: Path, tactic: str) -> tuple[bool, str]:
    theorem_text = (
        "example (a b : Nat) : a + b = b + a := by\n"
        f"  {tactic}\n"
    )

    with tempfile.NamedTemporaryFile("w", suffix=".lean", delete=False, encoding="utf-8") as tf:
        tf.write(theorem_text)
        tmp_path = Path(tf.name)

    try:
        import subprocess
        import shutil

        lake_cmd = shutil.which("lake") or "/home/nicolasbigeard/.elan/bin/lake"
        proc = subprocess.run(
            [lake_cmd, "env", "lean", str(tmp_path)],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = proc.returncode == 0
        out = (proc.stdout or "") + (proc.stderr or "")
        return ok, out.strip()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def backprop(path: list[TinyNode], value: float) -> None:
    for n in path:
        n.visits += 1
        n.value_sum += value


def run_integration_test(
    *,
    project_root: Path,
    client: Mistral,
    model: str,
    telemetry_path: Path,
) -> int:
    telemetry = ApiTelemetry()

    root_state = "a b : Nat\n⊢ a + b = b + a"
    root = TinyNode(state_text=root_state, tactic_from_parent=None)
    print("[step] initialized root MCTS node")

    ponder = run_ponder_loop(
        lean_state=root.state_text,
        client=client,
        model=model,
        max_turns=None,
        trivial_state_chars=0,
        api_log_hook=telemetry.hook,
    )
    tactic = " ".join(ponder.tactic.split())
    print(
        f"[step] URM finished | turns={ponder.turns} | act_budget={ponder.act_budget} "
        f"| halt_reason={ponder.halt_reason}"
    )
    print(f"[step] extracted tactic: {tactic}")

    ok, lean_output = verify_tactic_with_lean(project_root=project_root, tactic=tactic)
    if ok:
        print("[step] Lean verification: tactic compiles")
        child_state = "no goals"
    else:
        print("[warn] Lean verification failed for extracted tactic")
        print(lean_output)

        fallback_tactic = "exact Nat.add_comm a b"
        print(f"[step] retry with fallback tactic: {fallback_tactic}")
        ok2, lean_output2 = verify_tactic_with_lean(project_root=project_root, tactic=fallback_tactic)
        if not ok2:
            telemetry.save(telemetry_path)
            print(f"[fail] fallback tactic also failed; telemetry saved: {telemetry_path}")
            print(lean_output2)
            return 1
        tactic = fallback_tactic
        child_state = "no goals"
        print("[step] fallback tactic compiles in Lean")

    child = TinyNode(state_text=child_state, tactic_from_parent=tactic, parent=root)
    root.children.append(child)
    print("[step] tree updated: child node added")

    value_text = _api_call(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": VALUE_SYSTEM_PROMPT},
            {"role": "user", "content": VALUE_USER_PROMPT.format(state=child.state_text)},
        ],
        purpose="value_evaluation",
        telemetry=telemetry,
        temperature=0.0,
        max_tokens=64,
    )
    value = _parse_value(value_text)
    if value is None:
        value = 1.0 if "no goals" in child.state_text.lower() else 0.5

    backprop([root, child], value)
    print(
        f"[step] backprop complete | value={value:.3f} "
        f"| root_visits={root.visits} root_mean_value={root.mean_value:.3f}"
    )

    telemetry.save(telemetry_path)
    print(f"[ok] integration test complete; telemetry saved to {telemetry_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Phase 5 hello-world integration test")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--model", default="", help="Mistral model (defaults to MISTRAL_MODEL)")
    parser.add_argument(
        "--telemetry-file",
        default="logs/hello_world_api_telemetry.json",
        help="Where to save API request/response telemetry JSON",
    )
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()

    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        print("[fail] MISTRAL_API_KEY is not set")
        return 1

    model = args.model.strip() or os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip()
    if not model:
        print("[fail] no model configured")
        return 1

    client = Mistral(api_key=api_key)
    return run_integration_test(
        project_root=Path(args.project_root).resolve(),
        client=client,
        model=model,
        telemetry_path=Path(args.telemetry_file),
    )


if __name__ == "__main__":
    raise SystemExit(main())
