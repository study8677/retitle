"""Offline namer: derive a title from the latest substantive user message.

No network, no API key, no token cost. The title tracks the *current* topic
because it keys off the most recent real request — which is exactly the drift
problem retitle exists to fix.
"""

from __future__ import annotations

import re

from ..util import clean_text, is_trivial
from .base import Namer

_CLAUSE = re.compile(r"[。.!?！？\n;；:：]")
_LEAD_SLASH = re.compile(r"^/[a-zA-Z][\w-]*\s+")
_LEAD_FILLER = re.compile(
    r"^(please|pls|can you|could you|help me|i want to|i need to|let's|lets|"
    r"now|ok|okay|so|then|next|帮我|请|麻烦|我想|我要|然后|现在|帮忙|给我)\s+",
    re.IGNORECASE,
)


def _has_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def _condense(text: str) -> str:
    """Reduce a message to a topic-sized phrase."""
    first = _LEAD_SLASH.sub("", text).strip()
    first = _CLAUSE.split(first, 1)[0].strip() or first
    first = _LEAD_FILLER.sub("", first).strip()
    if _has_cjk(first):
        return first[:20]
    words = first.split()
    if len(words) > 9:
        first = " ".join(words[:9])
    return first


class HeuristicNamer(Namer):
    name = "heuristic"

    def generate(self, messages, *, old_title=None, cwd=None, tool=None):
        users = [
            clean_text(m.text)
            for m in messages
            if m.role == "user" and not is_trivial(m.text)
        ]
        users = [u for u in users if u]
        if not users:
            return None
        # Prefer the most recent message with enough substance; the freshest
        # real request best reflects what the session is about *now*.
        chosen = next((u for u in reversed(users) if len(u) >= 12), None)
        if chosen is None:
            chosen = max(users, key=len)
        return _condense(chosen)
