"""Tiny JSON-backed store remembering what we have already renamed.

The engine renames a session only when its content has changed since the last
title we wrote. That makes renaming idempotent (re-running does nothing) and
quietly respects titles a user edits by hand — until the conversation moves on.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from . import util


class StateStore:
    def __init__(self, path: Path | None = None):
        self.path = path or util.state_path()
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        self._loaded = False

    def load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}
        self._loaded = True

    def _ensure(self) -> None:
        if not self._loaded:
            self.load()

    def get(self, tool: str, sid: str) -> dict[str, Any] | None:
        self._ensure()
        return self._data.get(tool, {}).get(sid)

    def renamed_count(self, tool: str) -> int:
        """How many of `tool`'s sessions retitle has renamed (have renamed_at)."""
        self._ensure()
        return sum(1 for e in self._data.get(tool, {}).values() if e.get("renamed_at"))

    def update(self, tool: str, sid: str, **fields: Any) -> None:
        self._ensure()
        entry = self._data.setdefault(tool, {}).setdefault(sid, {})
        entry.update(fields)

    def prune(self, alive: set[tuple[str, str]], healthy: set[str]) -> None:
        """Drop bookkeeping for sessions no longer discoverable.

        Only prunes tools whose adapter discovered successfully this pass
        (``healthy``). If an adapter errored (e.g. its database was briefly
        locked), we keep all of its state untouched — otherwise the next pass
        would treat every one of its sessions as brand-new and could clobber
        titles the user set by hand.
        """
        self._ensure()
        for tool in list(self._data.keys()):
            if tool not in healthy:
                continue  # adapter failed this pass; leave its state intact
            for sid in list(self._data[tool].keys()):
                if (tool, sid) not in alive:
                    del self._data[tool][sid]
            if not self._data[tool]:
                del self._data[tool]

    def save(self) -> None:
        self._ensure()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so a crash mid-write never corrupts state.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
