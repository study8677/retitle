"""Codex adapter.

Titles live in ``~/.codex/state_<N>.sqlite`` (table ``threads``, column
``title``, keyed by thread ``id``). The full transcript is the rollout JSONL at
``threads.rollout_path``. Renaming is a single ``UPDATE threads SET title=?``;
the Codex Desktop app reads this column for its thread list.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..models import Message, Session
from ._sqlite import connect_read, connect_write
from .base import Adapter

_VER_RE = re.compile(r"state_(\d+)\.sqlite$")
_FULL_COLS = "id,title,rollout_path,updated_at_ms,cwd,archived,first_user_message"
_MIN_COLS = "id,title,rollout_path,updated_at_ms"


def _codex_root() -> Path:
    return Path.home() / ".codex"


def _find_state_db() -> Path | None:
    root = _codex_root()
    if not root.is_dir():
        return None
    candidates = sorted(
        root.glob("state_*.sqlite"),
        key=lambda p: int(m.group(1)) if (m := _VER_RE.search(p.name)) else -1,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    legacy = root / "state.sqlite"
    return legacy if legacy.exists() else None


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("text")
        ]
        return "\n".join(parts)
    return ""


class CodexAdapter(Adapter):
    name = "codex"
    label = "Codex"

    def available(self) -> bool:
        return _find_state_db() is not None

    def discover(self, since: float) -> list[Session]:
        db = _find_state_db()
        if not db:
            return []
        since_ms = int(since * 1000)
        con = connect_read(db)
        try:
            try:
                cur = con.execute(
                    f"SELECT {_FULL_COLS} FROM threads "
                    "WHERE updated_at_ms >= ? ORDER BY updated_at_ms DESC",
                    (since_ms,),
                )
            except sqlite3.OperationalError:
                cur = con.execute(
                    f"SELECT {_MIN_COLS} FROM threads "
                    "WHERE updated_at_ms >= ? ORDER BY updated_at_ms DESC",
                    (since_ms,),
                )
            cols = [d[0] for d in cur.description]
            out: list[Session] = []
            for row in cur.fetchall():
                r = dict(zip(cols, row))
                if r.get("archived"):  # skip archived threads (truthy flag)
                    continue
                updated = r.get("updated_at_ms")
                if not updated:
                    continue
                out.append(
                    Session(
                        tool=self.name,
                        id=r["id"],
                        title=r.get("title"),
                        last_active=updated / 1000.0,
                        cwd=r.get("cwd"),
                        meta={
                            "db": str(db),
                            "rollout_path": r.get("rollout_path"),
                            "first_user_message": r.get("first_user_message"),
                        },
                    )
                )
            return out
        finally:
            con.close()

    def read_transcript(self, session: Session) -> list[Message]:
        rollout = session.meta.get("rollout_path")
        msgs: list[Message] = []
        if rollout and Path(rollout).exists():
            try:
                with open(rollout, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if '"message"' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("type") != "response_item":
                            continue
                        payload = obj.get("payload") or {}
                        if payload.get("type") != "message":
                            continue
                        role = payload.get("role")
                        if role not in ("user", "assistant"):
                            continue
                        text = _content_text(payload.get("content"))
                        if text.strip():
                            msgs.append(Message(role=role, text=text))
            except OSError:
                msgs = []
        if not msgs:
            fum = session.meta.get("first_user_message")
            if fum:
                return [Message(role="user", text=fum)]
        return msgs

    def set_title(self, session: Session, title: str) -> None:
        db = session.meta["db"]
        con = connect_write(db)
        try:
            con.execute(
                "UPDATE threads SET title = ? WHERE id = ?", (title, session.id)
            )
            con.commit()
        finally:
            con.close()
