#!/usr/bin/env python3
"""Small structured-output wrapper around ``codex exec``."""
from __future__ import annotations

import json
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _retryable(message: str) -> bool:
    lower = message.lower()
    return any(
        marker in lower
        for marker in (
            "429",
            "rate limit",
            "reconnecting",
            "502",
            "503",
            "504",
            "timed out",
            "timeout",
            "temporarily",
            "connection reset",
            "service unavailable",
            "overloaded",
        )
    )


def codex_exec_json(
    prompt: str,
    schema: dict[str, Any],
    *,
    cwd: Path,
    codex_bin: str,
    model: str,
    reasoning_effort: str,
    sandbox: str,
    timeout: int,
    retries: int,
    retry_delay: float,
) -> dict[str, Any]:
    """Run one ephemeral OAuth-backed Codex task and parse its JSON result."""
    last_error = "codex exec was not attempted"
    for attempt in range(retries + 1):
        with tempfile.TemporaryDirectory(prefix="cmass-codex-") as tmp:
            tmp_path = Path(tmp)
            schema_path = tmp_path / "schema.json"
            output_path = tmp_path / "result.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            cmd = [
                codex_bin,
                "--ask-for-approval",
                "never",
                "exec",
                "--ephemeral",
                "-C",
                str(cwd),
                "--model",
                model,
                "-c",
                f'model_reasoning_effort="{reasoning_effort}"',
                "--sandbox",
                sandbox,
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                "-",
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    input=prompt,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                combined = f"codex exec timed out after {timeout}s"
                last_error = combined
            else:
                combined = f"{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}"
                if proc.returncode == 0 and output_path.exists():
                    return _parse_json_object(output_path.read_text(encoding="utf-8").strip())
                last_error = f"codex exec exited {proc.returncode}:\n{combined}"

        if attempt >= retries or not _retryable(combined):
            break
        delay = min(600.0, retry_delay * (2**attempt) + random.uniform(0.0, retry_delay))
        time.sleep(delay)

    raise RuntimeError(last_error)
