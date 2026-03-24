#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys

from dotenv import load_dotenv


def check_cmd(cmd: str) -> bool:
    path = shutil.which(cmd)
    if not path:
        print(f"[fail] command not found: {cmd}")
        return False
    print(f"[ok] {cmd}: {path}")
    return True


def run_version(cmd: str, args: list[str]) -> bool:
    try:
        out = subprocess.check_output([cmd, *args], text=True, stderr=subprocess.STDOUT).strip()
        first_line = out.splitlines()[0] if out else ""
        print(f"[ok] {cmd} version: {first_line}")
        return True
    except Exception as exc:  # pragma: no cover
        print(f"[fail] {cmd} version check failed: {exc}")
        return False


def maybe_call_mistral() -> bool:
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    model = os.getenv("MISTRAL_MODEL", "labs-leanstral-2603").strip() or "labs-leanstral-2603"

    if not api_key:
        print("[warn] MISTRAL_API_KEY not set. Skipping API call.")
        return True

    try:
        from mistralai.client import Mistral

        client = Mistral(api_key=api_key)
        response = client.chat.complete(
            model=model,
            messages=[{"role": "user", "content": "Reply with: setup-ok"}],
            max_tokens=16,
            temperature=0,
        )
        print(f"[ok] Mistral chat.complete succeeded on model: {model}")

        # Response shape can vary by SDK/model; print a compact preview safely.
        preview = str(response)[:200].replace("\n", " ")
        print(f"[info] response preview: {preview}")
        return True
    except Exception as exc:
        print(f"[fail] Mistral API call failed: {exc}")
        return False


def main() -> int:
    load_dotenv()

    ok = True

    if check_cmd("lean"):
        ok = run_version("lean", ["--version"]) and ok
    else:
        ok = False

    if check_cmd("lake"):
        ok = run_version("lake", ["--version"]) and ok
    else:
        ok = False

    try:
        import lean_dojo  # type: ignore

        _ = lean_dojo
        print("[ok] lean_dojo import")
    except Exception as exc:
        print(f"[fail] lean_dojo import failed: {exc}")
        ok = False

    try:
        import mistralai.client  # type: ignore

        _ = mistralai.client
        print("[ok] mistralai.client import")
    except Exception as exc:
        print(f"[fail] mistralai import failed: {exc}")
        ok = False

    ok = maybe_call_mistral() and ok

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
