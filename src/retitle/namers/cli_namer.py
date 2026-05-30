"""Namer that shells out to an installed CLI (`claude` or `codex`).

Reuses whatever login the user already has for that tool — no API key wiring,
no extra cost beyond the tool's own usage. This is the default (via ``auto``),
which prefers ``claude`` then ``codex``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from .. import util
from .base import INSTRUCTION, Namer, build_excerpt

_TIMEOUT = 90  # codex with reasoning can take a while; keep generous

# The fast Codex model used for titling unless the user overrides it.
_CODEX_DEFAULT_MODEL = "gpt-5-codex"
# Claude's small/fast model — plenty for a 6-word title, and cheap.
_CLAUDE_DEFAULT_MODEL = "haiku"


class CliNamer(Namer):
    def __init__(self, name: str, options: dict | None = None):
        self.name = name  # "claude" or "codex"
        self.options = options or {}

    def available(self) -> bool:
        return shutil.which(self.name) is not None

    def _prompt(self, messages) -> str | None:
        excerpt = build_excerpt(messages)
        if not excerpt:
            return None
        return f"{INSTRUCTION}\n\n--- conversation ---\n{excerpt}\n--- end ---"

    # -- claude ------------------------------------------------------------ #
    def _generate_claude(self, prompt: str) -> str | None:
        argv = ["claude"]
        model = self.options.get("model", _CLAUDE_DEFAULT_MODEL)
        if model:
            argv += ["--model", str(model)]
        argv += ["-p", prompt]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=_TIMEOUT
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            util.log(f"claude namer call failed: {exc}", level="debug")
            return None
        if proc.returncode != 0:
            util.log(
                f"claude namer exited {proc.returncode}: {proc.stderr.strip()[:160]}",
                level="debug",
            )
            return None
        # `claude -p` prints just the response; take the last non-empty line.
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        return lines[-1] if lines else None

    # -- codex ------------------------------------------------------------- #
    def _generate_codex(self, prompt: str) -> str | None:
        # `codex exec` streams a noisy transcript to stdout, so we ask it to
        # write ONLY the final assistant message to a file and read that back.
        fd, out_path = tempfile.mkstemp(prefix="retitle-codex-", suffix=".txt")
        os.close(fd)
        try:
            argv = ["codex", "exec"]
            model = self.options.get("model", _CODEX_DEFAULT_MODEL)
            if model:
                argv += ["-m", str(model)]
            argv += ["--output-last-message", out_path, prompt]
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, timeout=_TIMEOUT
                )
            except (subprocess.TimeoutExpired, OSError) as exc:
                util.log(f"codex namer call failed: {exc}", level="debug")
                return None
            if proc.returncode != 0:
                util.log(
                    f"codex namer exited {proc.returncode}: "
                    f"{proc.stderr.strip()[:160]}",
                    level="debug",
                )
                return None
            try:
                text = open(out_path, encoding="utf-8", errors="replace").read()
            except OSError:
                return None
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            return lines[-1] if lines else None
        finally:
            if os.path.exists(out_path):
                os.unlink(out_path)

    def generate(self, messages, *, old_title=None, cwd=None, tool=None):
        prompt = self._prompt(messages)
        if not prompt:
            return None
        if self.name == "codex":
            return self._generate_codex(prompt)
        return self._generate_claude(prompt)
