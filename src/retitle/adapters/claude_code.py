"""Claude Code adapter.

Sessions live at ``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl``.
The display title is the *last* ``{"type":"ai-title", ...}`` line in the file;
Claude Code appends a fresh one whenever it (re)titles, and reads the latest.
So to rename a session we just append our own ``ai-title`` line — append-only,
which is the safest possible write.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import Message, Session
from .base import Adapter

# Match the value, not "type":"ai-title", so we tolerate any JSON spacing.
_AI_TITLE = b'"ai-title"'


def _projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _last_ai_title(path: Path, *, window: int = 1_000_000) -> str | None:
    """Find the most recent ai-title by scanning the tail of the file."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    want = min(size, window)
    with open(path, "rb") as fh:
        fh.seek(size - want)
        buf = fh.read(want)
    if want < size:  # drop a possibly-truncated leading line
        nl = buf.find(b"\n")
        if nl != -1:
            buf = buf[nl + 1 :]
    last: bytes | None = None
    for line in buf.split(b"\n"):
        if _AI_TITLE in line:
            last = line
    if last is None:
        return None
    try:
        return json.loads(last.decode("utf-8", "replace")).get("aiTitle")
    except json.JSONDecodeError:
        return None


class ClaudeCodeAdapter(Adapter):
    name = "claude-code"
    label = "Claude Code"

    def available(self) -> bool:
        return _projects_root().is_dir()

    def discover(self, since: float) -> list[Session]:
        root = _projects_root()
        if not root.is_dir():
            return []
        out: list[Session] = []
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                if mtime < since:
                    continue
                out.append(
                    Session(
                        tool=self.name,
                        id=f.stem,
                        title=_last_ai_title(f),
                        last_active=mtime,
                        cwd=None,
                        meta={"path": str(f), "project": proj.name},
                    )
                )
        return out

    def read_transcript(self, session: Session) -> list[Message]:
        # Prefer "last-prompt" lines for user turns: they hold the exact prompt
        # the user typed, free of the caveats / interruption markers / tool
        # results that pollute raw "user" message lines.
        path = Path(session.meta["path"])
        msgs: list[Message] = []
        last_user: str | None = None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    is_prompt = '"last-prompt"' in line
                    is_asst = '"assistant"' in line
                    if not (is_prompt or is_asst):
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    typ = obj.get("type")
                    if typ == "last-prompt":
                        prompt = (obj.get("lastPrompt") or "").strip()
                        if prompt and prompt != last_user:
                            msgs.append(Message(role="user", text=prompt))
                            last_user = prompt
                    elif typ == "assistant":
                        text = _content_text((obj.get("message") or {}).get("content"))
                        if text.strip():
                            msgs.append(Message(role="assistant", text=text))
        except OSError:
            return []
        return msgs

    def set_title(self, session: Session, title: str) -> None:
        path = Path(session.meta["path"])
        line = json.dumps(
            {"type": "ai-title", "aiTitle": title, "sessionId": session.id},
            ensure_ascii=False,
            separators=(",", ":"),  # match Claude Code's own compact JSONL
        ).encode("utf-8")
        # One handle in append mode: the write always lands at EOF (O_APPEND)
        # regardless of where we seek to read, so there's no check-then-write
        # gap. A leading newline is added only if the file doesn't end in one.
        with open(path, "a+b") as fh:
            fh.seek(0, 2)
            prefix = b""
            if fh.tell() > 0:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    prefix = b"\n"
            fh.write(prefix + line + b"\n")
