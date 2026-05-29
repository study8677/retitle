"""Core data structures shared across adapters, namers and the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """A single turn in a conversation transcript."""

    role: str  # "user" | "assistant" | "system"
    text: str
    ts: float | None = None  # epoch seconds, if known


@dataclass
class Session:
    """A discovered session from one of the supported tools.

    ``meta`` carries adapter-private data (file paths, db handles, row keys)
    that the same adapter uses later to read the transcript or write the title.
    The engine treats it as opaque.
    """

    tool: str  # adapter name, e.g. "claude-code"
    id: str  # session / thread / composer id
    title: str | None  # current title, if any
    last_active: float  # epoch seconds of last activity
    cwd: str | None = None  # working directory / project path, if known
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id[:8] if self.id else "?"

    def idle_seconds(self, now: float) -> float:
        return max(0.0, now - self.last_active)


@dataclass
class RenamePlan:
    """The engine's decision about a single session during one pass."""

    session: Session
    action: str  # "rename" | "skip"
    new_title: str | None = None
    reason: str = ""
    content_sig: str | None = None
    # True once we've fully read+evaluated this exact state, so the next pass
    # can skip re-reading the transcript while last_active is unchanged.
    mark_seen: bool = False
