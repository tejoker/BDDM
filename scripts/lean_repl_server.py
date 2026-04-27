#!/usr/bin/env python3
"""Persistent Lean 4 REPL server using leanprover-community/repl.

Replaces lean_repl_dojo.py for state-level proof search.
Communicates via JSON over stdin/stdout with the repl process.

Protocol (https://github.com/leanprover-community/repl):
  Send:  {"cmd": "...", "env": N}          → elaborate a command in env N
  Send:  {"tactic": "...", "proofState": N} → apply tactic to proof state N
  Recv:  {"env": N, ...}                   → success, new env index
  Recv:  {"proofState": N, "goals": [...]} → tactic success, new proof state
  Recv:  {"messages": [{"severity": "error", ...}]}  → elaboration error
  Recv:  {"proofState": N, "goals": []}   → proof complete (no goals)

Usage:
    with LeanREPLServer(project_root=Path(".")) as server:
        state_id = server.start_proof("theorem foo (n : Nat) : n + 0 = n")
        result = server.run_tac(state_id, "omega")
        if isinstance(result, ProofFinished):
            print("proved")
        elif isinstance(result, TacticState):
            print("remaining goals:", result.goals)
        elif isinstance(result, LeanError):
            print("error:", result.error)
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union


# ── Mirror of lean_dojo types ─────────────────────────────────────────────────

@dataclass(frozen=True)
class TacticState:
    goals: list[str]
    proof_state_id: int

    @property
    def pp(self) -> str:
        return "\n".join(self.goals)

    @property
    def num_goals(self) -> int:
        return len(self.goals)


@dataclass(frozen=True)
class ProofFinished:
    proof_state_id: int
    message: Optional[str] = None


@dataclass(frozen=True)
class LeanError:
    error: str


TacticResult = Union[TacticState, ProofFinished, LeanError]

_JSON_DECODER = json.JSONDecoder()


def _extract_json_message(buffer: str) -> tuple[dict | None, str]:
    """Extract the first full JSON object from a possibly noisy stream buffer.

    Returns (obj, remainder). If no complete object is available yet, obj is None
    and remainder contains the preserved suffix for continued accumulation.
    """
    start = buffer.find("{")
    if start < 0:
        return None, buffer[-4096:]

    probe = start
    while probe >= 0:
        frag = buffer[probe:]
        try:
            obj, consumed = _JSON_DECODER.raw_decode(frag)
            remainder = frag[consumed:]
            return obj, remainder
        except json.JSONDecodeError as exc:
            # Looks incomplete: keep buffering from this JSON start.
            if exc.pos >= max(0, len(frag) - 1):
                return None, buffer[probe:]
            # Malformed prefix, search for the next candidate object.
            nxt = buffer.find("{", probe + 1)
            if nxt < 0:
                return None, buffer[probe:]
            probe = nxt

    return None, buffer[-4096:]


# ── REPL server ───────────────────────────────────────────────────────────────

class LeanREPLServer:
    """Persistent Lean REPL process.  Thread-safe for single-threaded use."""

    def __init__(
        self,
        project_root: Path,
        timeout: float = 120.0,
        env: Optional[dict[str, str]] = None,
    ):
        self.project_root = Path(project_root)
        self.timeout = timeout
        self._env = env
        self._proc: Optional[subprocess.Popen] = None
        self._seq = 0          # monotone counter for matching responses
        self._env_id = 0       # current Lean environment index
        self._lock = threading.Lock()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def __enter__(self) -> "LeanREPLServer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    def _repl_binary(self) -> list[str]:
        """Resolve the repl executable — direct path only, no probe calls."""
        # Direct binary inside .lake/packages (built by `lake build repl`)
        direct = self.project_root / ".lake" / "packages" / "repl" / ".lake" / "build" / "bin" / "repl"
        if direct.exists():
            return ["lake", "env", str(direct)]
        # Fallback: lake exe repl (slower but more portable)
        return ["lake", "exe", "repl"]

    def _proc_env(self) -> dict:
        proc_env = os.environ.copy()
        proc_env["PATH"] = str(Path.home() / ".elan" / "bin") + ":" + proc_env.get("PATH", "")
        if self._env:
            proc_env.update(self._env)
        proc_env.pop("DESOL_FORCE_REPL_DOJO", None)
        return proc_env

    def start(self) -> None:
        if self._proc is not None:
            return
        cmd = self._repl_binary()
        self._proc = subprocess.Popen(
            cmd,
            cwd=self.project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._proc_env(),
            text=True,
            bufsize=1,
        )
        try:
            if self._proc.stdout is not None:
                os.set_blocking(self._proc.stdout.fileno(), False)
            if self._proc.stderr is not None:
                os.set_blocking(self._proc.stderr.fileno(), False)
        except Exception:
            # Best-effort; fallback still works with select+readline.
            pass
        # The repl prints nothing on startup — just wait briefly
        time.sleep(0.3)
        if self._proc.poll() is not None:
            stderr = self._proc.stderr.read()
            raise RuntimeError(f"REPL process died on startup: {stderr[:400]}")

    def stop(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def restart(self) -> None:
        self.stop()
        self.start()

    # ── low-level send/recv ───────────────────────────────────────────────────

    def _send(self, payload: dict) -> dict:
        """Send one JSON command and return the parsed response."""
        line = json.dumps(payload) + "\n\n"
        last_err = ""
        for attempt in range(2):
            if self._proc is None or self._proc.poll() is not None:
                self.restart()
            assert self._proc is not None
            deadline = time.monotonic() + self.timeout
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except BrokenPipeError:
                last_err = "broken pipe while writing to repl stdin"
                if attempt == 0:
                    self.restart()
                    continue
                raise TimeoutError(f"REPL write failed: {last_err}")

            out_buf = ""
            err_buf = ""
            while time.monotonic() < deadline:
                if self._proc.poll() is not None:
                    break
                remaining = max(0.0, deadline - time.monotonic())
                watch = []
                if self._proc.stdout is not None:
                    watch.append(self._proc.stdout)
                if self._proc.stderr is not None:
                    watch.append(self._proc.stderr)
                if not watch:
                    break
                ready, _, _ = select.select(watch, [], [], min(0.25, remaining))
                if not ready:
                    continue
                for stream in ready:
                    if stream is self._proc.stdout and self._proc.stdout is not None:
                        try:
                            raw = os.read(self._proc.stdout.fileno(), 65536)
                        except BlockingIOError:
                            raw = b""
                        if raw:
                            out_buf += raw.decode("utf-8", errors="replace")
                            obj, out_buf = _extract_json_message(out_buf)
                            if obj is not None:
                                return obj
                    elif stream is self._proc.stderr and self._proc.stderr is not None:
                        try:
                            raw = os.read(self._proc.stderr.fileno(), 65536)
                        except BlockingIOError:
                            raw = b""
                        if raw:
                            err_buf += raw.decode("utf-8", errors="replace")

            if out_buf.strip():
                obj, _ = _extract_json_message(out_buf)
                if obj is not None:
                    return obj
                last_err = f"invalid/non-json payload from REPL stdout: {out_buf.strip()[-220:]}"
            else:
                last_err = "empty response from REPL"
            if err_buf.strip():
                last_err = f"{last_err}; stderr: {err_buf.strip()[-220:]}"

            if self._proc is not None and self._proc.poll() is not None and self._proc.stderr is not None:
                try:
                    stderr_tail = self._proc.stderr.read() or ""
                    if isinstance(stderr_tail, bytes):
                        stderr_tail = stderr_tail.decode("utf-8", errors="replace")
                    stderr_tail = stderr_tail[-500:]
                    if stderr_tail.strip():
                        last_err = stderr_tail.strip()
                except Exception:
                    pass
            if attempt == 0:
                self.restart()
                continue

        raise TimeoutError(f"REPL did not respond within {self.timeout}s ({last_err})")

    # ── high-level API ────────────────────────────────────────────────────────

    def elaborate(self, cmd: str, env: Optional[int] = None) -> dict:
        """Elaborate a Lean command.

        If env is None and this is not an import command, uses self._env_id.
        Import commands (starting with 'import') must be sent without env.
        """
        payload: dict = {"cmd": cmd}
        if env is not None:
            payload["env"] = env
        elif not cmd.strip().startswith("import") and self._env_id > 0:
            payload["env"] = self._env_id

        resp = self._send(payload)
        # Advance env on success
        msgs = resp.get("messages", [])
        errors = [m for m in msgs if m.get("severity") == "error"]
        if not errors and "env" in resp:
            self._env_id = resp["env"]
        return resp

    def ensure_mathlib_imported(self, anchor_file: str = "Desol/ReplAnchor.lean") -> Union[int, LeanError]:
        """Load a project file to get an env with Mathlib already elaborated.

        Uses the REPL file mode with an existing .lean file — this hits the
        pre-built .olean cache and returns in ~5s instead of re-importing Mathlib
        from scratch (~5min).
        """
        if self._env_id > 0:
            return self._env_id
        resp = self._send({"path": anchor_file, "allTactics": False})
        msgs = resp.get("messages", [])
        errors = [m for m in msgs if m.get("severity") == "error" and "sorry" not in m.get("data", "")]
        if errors:
            return LeanError(errors[0].get("data", str(errors[0])))
        self._env_id = resp.get("env", 1)
        return self._env_id

    def start_proof(self, theorem_statement: str) -> Union[int, LeanError]:
        """Open a proof using a sorry-stub and return the initial proof state ID.

        Protocol: send the theorem with a sorry body to get the initial proof state
        from the 'sorries' field, then use that proofState id for tactic application.
        """
        # Ensure Mathlib is imported
        env_or_err = self.ensure_mathlib_imported()
        if isinstance(env_or_err, LeanError):
            return env_or_err

        stmt = theorem_statement.rstrip()
        # Strip any existing body (handles `:= by ...`, `:= sorry`, `:=`) and add sorry stub
        stmt = re.sub(r":=\s*by\b.*$", "", stmt, flags=re.DOTALL).strip()
        stmt = re.sub(r":=\s*sorry\s*$", "", stmt, flags=re.DOTALL).strip()
        stmt = re.sub(r":=\s*$", "", stmt).strip()
        # Avoid redeclaration collisions when the theorem already exists in loaded env.
        tmp_name = f"__desol_tmp_{int(time.time() * 1000)}"
        stmt = re.sub(
            r"^\s*(theorem|lemma)\s+([^\s(:=]+)",
            lambda m: f"{m.group(1)} {tmp_name}",
            stmt,
            count=1,
            flags=re.MULTILINE,
        )
        stmt_with_sorry = stmt + " := by\n  sorry"

        try:
            resp = self.elaborate(stmt_with_sorry, env=self._env_id)
        except TimeoutError as exc:
            # One hard recovery attempt with fresh REPL process.
            self.restart()
            self._env_id = 0
            env_or_err = self.ensure_mathlib_imported()
            if isinstance(env_or_err, LeanError):
                return env_or_err
            try:
                resp = self.elaborate(stmt_with_sorry, env=self._env_id)
            except TimeoutError:
                return LeanError(str(exc))
        msgs = resp.get("messages", [])
        # Check for errors unrelated to sorry
        real_errors = [
            m for m in msgs
            if m.get("severity") == "error"
            and "declaration uses 'sorry'" not in m.get("data", "")
        ]
        if real_errors:
            return LeanError(real_errors[0].get("data", str(real_errors[0])))

        sorries = resp.get("sorries", [])
        if not sorries:
            # Proof might be trivially closed by sorry (no goals)
            return LeanError("No proof state returned — statement may be ill-typed")
        return sorries[0]["proofState"]

    def run_tac(self, proof_state_id: int, tactic: str) -> TacticResult:
        """Apply a tactic to proof_state_id.  Returns TacticState, ProofFinished, or LeanError.

        Protocol: {"tactic": "...", "proofState": N}
        Response on success: {"proofState": M, "goals": ["goal1", ...]}
        Response on proof done: {"proofState": M, "goals": []}
        Response on error: {"message": "..."}  (no proofState key)
        """
        tactic = tactic.strip()
        try:
            resp = self._send({"tactic": tactic, "proofState": proof_state_id})
        except TimeoutError as exc:
            return LeanError(str(exc))

        # Error: no proofState in response, or explicit message
        if "proofState" not in resp:
            msg = resp.get("message", str(resp))
            return LeanError(msg)

        # Check messages for tactic errors
        msgs = resp.get("messages", [])
        errors = [m for m in msgs if m.get("severity") == "error"]
        if errors:
            return LeanError(errors[0].get("data", str(errors[0])))

        new_ps_id = resp["proofState"]
        goals: list[str] = resp.get("goals", [])

        if not goals:
            return ProofFinished(proof_state_id=new_ps_id)
        return TacticState(goals=goals, proof_state_id=new_ps_id)

    def run_tac_sequence(self, proof_state_id: int, tactics: list[str]) -> TacticResult:
        """Apply a sequence of tactics, stopping at the first error or ProofFinished."""
        state = proof_state_id
        result: TacticResult = TacticState(goals=["<unknown>"], proof_state_id=state)
        for tactic in tactics:
            result = self.run_tac(state, tactic)
            if isinstance(result, (ProofFinished, LeanError)):
                return result
            state = result.proof_state_id
        return result

    def check_proof(self, theorem_statement: str, tactics: list[str]) -> TacticResult:
        """Convenience: open proof and apply all tactics.  Returns final TacticResult."""
        ps = self.start_proof(theorem_statement)
        if isinstance(ps, LeanError):
            return ps
        return self.run_tac_sequence(ps, tactics)

    # ── context manager for dojo compatibility ────────────────────────────────

    def as_dojo(self, file_path: Path, theorem_name: str):
        """Return a (dojo, initial_state) pair compatible with REPLDojo's interface.

        The file_path is used to load the theorem signature for elaboration.
        theorem_name must match a theorem/lemma declaration in the file.
        """
        return _DojoAdapter(self, file_path, theorem_name)


class _DojoAdapter:
    """Thin adapter so LeanREPLServer can be used wherever REPLDojo is used."""

    def __init__(self, server: LeanREPLServer, file_path: Path, theorem_name: str):
        self._server = server
        self._file_path = file_path
        self._theorem_name = theorem_name
        self._ps_id: Optional[int] = None

    def __enter__(self):
        from lean_repl_dojo import TacticState as _DTS  # noqa: F401 — type compat
        src = (self._server.project_root / self._file_path).read_text(encoding="utf-8")
        # Find theorem signature line
        import re
        lines = src.splitlines()
        sig_lines: list[str] = []
        in_sig = False
        for line in lines:
            if re.match(rf"\s*(?:lemma|theorem)\s+{re.escape(self._theorem_name)}\b", line):
                in_sig = True
            if in_sig:
                sig_lines.append(line)
                if ":= by" in line or ":= by" in " ".join(sig_lines):
                    break
        if not sig_lines:
            raise ValueError(f"Theorem {self._theorem_name!r} not found in {self._file_path}")

        stmt = " ".join(sig_lines)
        ps = self._server.start_proof(stmt)
        if isinstance(ps, LeanError):
            raise RuntimeError(f"Could not open proof: {ps.error}")
        self._ps_id = ps
        initial_state = TacticState(goals=["<elaborated by REPL>"], proof_state_id=ps)
        return self, initial_state

    def __exit__(self, *_):
        pass

    def run_tac(self, state: TacticState, tactic: str) -> TacticResult:
        return self._server.run_tac(state.proof_state_id, tactic)


# ── Availability check ────────────────────────────────────────────────────────

def repl_server_available(project_root: Path) -> bool:
    """Return True if the leanprover-community/repl binary is available."""
    elan_bin = Path.home() / ".elan" / "bin"
    env = os.environ.copy()
    env["PATH"] = str(elan_bin) + ":" + env.get("PATH", "")
    # Check direct binary path
    direct = project_root / ".lake" / "packages" / "repl" / ".lake" / "build" / "bin" / "repl"
    if direct.exists():
        return True
    # Check lake exe repl
    try:
        r = subprocess.run(
            ["lake", "exe", "repl", "--help"],
            cwd=project_root,
            capture_output=True,
            timeout=10,
            env=env,
        )
        return r.returncode == 0
    except Exception:
        return False


def get_best_dojo(project_root: Path, file_path: Path, theorem_name: str, timeout: float = 120.0):
    """Return the best available dojo implementation.

    Preference order:
    1. LeanREPLServer (real proof states, state-level MCTS)
    2. REPLDojo (fast, draft-level, no real states)
    """
    force_repl_dojo = os.environ.get("DESOL_FORCE_REPL_DOJO", "0") == "1"
    if not force_repl_dojo and repl_server_available(project_root):
        server = LeanREPLServer(project_root=project_root, timeout=timeout)
        return server.as_dojo(file_path, theorem_name)

    # Fallback to REPLDojo
    from lean_repl_dojo import REPLDojo
    return REPLDojo(
        project_root=project_root,
        file_path=file_path,
        theorem_name=theorem_name,
        timeout=int(timeout),
    )
