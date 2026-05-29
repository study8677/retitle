"""Cursor adapter.

Cursor keeps chats ("composers") in the global ``state.vscdb`` SQLite file.
The title shows up in two synchronized places, both of which we update:

  * ``ItemTable['composer.composerHeaders']`` -> JSON ``allComposers[i].name``
    (this is what the chat list renders)
  * ``cursorDiskKV['composerData:<id>']`` -> top-level ``name``

Messages are stored per-bubble at ``cursorDiskKV['bubbleId:<cid>:<bid>']``
(``type`` 1 = user, 2 = assistant), ordered by ``fullConversationHeadersOnly``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..models import Message, Session
from ._sqlite import connect_read, connect_write
from .base import Adapter

_HEADERS_KEY = "composer.composerHeaders"
_MAX_BUBBLES = 40  # only the recent tail matters for naming


def _vscdb() -> Path | None:
    candidates = [
        Path.home()
        / "Library/Application Support/Cursor/User/globalStorage/state.vscdb",  # macOS
        Path.home() / ".config/Cursor/User/globalStorage/state.vscdb",  # Linux
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "Cursor/User/globalStorage/state.vscdb")
    for c in candidates:
        if c.exists():
            return c
    return None


def _item(con, key: str) -> str | None:
    row = con.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _kv(con, key: str) -> str | None:
    row = con.execute(
        "SELECT value FROM cursorDiskKV WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def _ms_to_epoch(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v / 1000.0 if v > 1e12 else v


def _rich_text(rich) -> str:
    """Best-effort plain text from Cursor's Lexical richText payload."""
    if not rich:
        return ""
    if isinstance(rich, str):
        try:
            rich = json.loads(rich)
        except json.JSONDecodeError:
            return rich
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("text"), str):
                out.append(node["text"])
            for child in node.get("children", []) or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(rich.get("root", rich) if isinstance(rich, dict) else rich)
    return " ".join(out).strip()


class CursorAdapter(Adapter):
    name = "cursor"
    label = "Cursor"

    def available(self) -> bool:
        return _vscdb() is not None

    def discover(self, since: float) -> list[Session]:
        db = _vscdb()
        if not db:
            return []
        con = connect_read(db)
        try:
            raw = _item(con, _HEADERS_KEY)
            if not raw:
                return []
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return []
            out: list[Session] = []
            for c in data.get("allComposers", []):
                if not isinstance(c, dict) or c.get("isArchived"):
                    continue
                cid = c.get("composerId")
                if not cid:
                    continue
                last_active = _ms_to_epoch(
                    c.get("lastUpdatedAt") or c.get("createdAt") or 0
                )
                if last_active < since:
                    continue
                cwd = None
                wid = c.get("workspaceIdentifier")
                if isinstance(wid, dict):
                    cwd = (wid.get("uri") or {}).get("path")
                out.append(
                    Session(
                        tool=self.name,
                        id=cid,
                        title=c.get("name"),
                        last_active=last_active,
                        cwd=cwd,
                        meta={"db": str(db)},
                    )
                )
            return out
        finally:
            con.close()

    def read_transcript(self, session: Session) -> list[Message]:
        db = session.meta["db"]
        con = connect_read(db)
        try:
            raw = _kv(con, f"composerData:{session.id}")
            if not raw:
                return []
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return []
            headers = data.get("fullConversationHeadersOnly") or []
            msgs: list[Message] = []
            for h in headers[-_MAX_BUBBLES:]:
                if not isinstance(h, dict):
                    continue
                bid = h.get("bubbleId")
                if not bid:
                    continue
                braw = _kv(con, f"bubbleId:{session.id}:{bid}")
                if not braw:
                    continue
                try:
                    b = json.loads(braw)
                except json.JSONDecodeError:
                    continue
                text = (b.get("text") or "").strip() or _rich_text(b.get("richText"))
                if not text:
                    continue
                role = "user" if b.get("type") == 1 else "assistant"
                msgs.append(Message(role=role, text=text))
            return msgs
        finally:
            con.close()

    def set_title(self, session: Session, title: str) -> None:
        # Both title copies (list registry + per-composer blob) must move
        # together, atomically. We take the write lock up front (BEGIN
        # IMMEDIATE) to close the read-then-write race with a running Cursor,
        # and roll back on any error so we never commit a half update. Raising
        # here lets the engine log a warning and *not* record a false success.
        db = session.meta["db"]
        con = connect_write(db)
        con.isolation_level = None  # we manage the transaction explicitly
        try:
            con.execute("BEGIN IMMEDIATE")
            raw = _item(con, _HEADERS_KEY)
            kraw = _kv(con, f"composerData:{session.id}")
            if not raw or not kraw:
                raise RuntimeError(f"composer {session.id}: title rows missing")

            data = json.loads(raw)
            found = False
            for c in data.get("allComposers", []):
                if isinstance(c, dict) and c.get("composerId") == session.id:
                    c["name"] = title
                    found = True
                    break
            if not found:
                raise RuntimeError(f"composer {session.id} not in composerHeaders")

            cdata = json.loads(kraw)
            cdata["name"] = title

            r1 = con.execute(
                "UPDATE ItemTable SET value = ? WHERE key = ?",
                (json.dumps(data, ensure_ascii=False), _HEADERS_KEY),
            )
            r2 = con.execute(
                "UPDATE cursorDiskKV SET value = ? WHERE key = ?",
                (json.dumps(cdata, ensure_ascii=False), f"composerData:{session.id}"),
            )
            if r1.rowcount < 1 or r2.rowcount < 1:
                raise RuntimeError(f"composer {session.id}: update affected no rows")
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
