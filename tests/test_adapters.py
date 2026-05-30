"""Round-trip tests for each adapter against synthetic fixtures.

These also exercise the write path (set_title) without touching any real data.
"""

import json
import sqlite3
import time

import pytest

from retitle.adapters import claude_code, codex, cursor


# --------------------------------------------------------------------------- #
# Claude Code
# --------------------------------------------------------------------------- #
def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")


def test_claude_adapter_roundtrip(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    proj = projects / "-Users-me-proj"
    proj.mkdir(parents=True)
    sid = "11111111-1111-1111-1111-111111111111"
    f = proj / f"{sid}.jsonl"
    _write_jsonl(
        f,
        [
            {"type": "permission-mode", "permissionMode": "default"},
            {"type": "last-prompt", "lastPrompt": "Add a dark mode toggle", "sessionId": sid},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Sure."}]},
            },
            {"type": "last-prompt", "lastPrompt": "好的", "sessionId": sid},
            {"type": "last-prompt", "lastPrompt": "Now fix the migration bug", "sessionId": sid},
            {"type": "ai-title", "aiTitle": "Dark mode toggle", "sessionId": sid},
        ],
    )
    monkeypatch.setattr(claude_code, "_projects_root", lambda: projects)
    adapter = claude_code.ClaudeCodeAdapter()

    sessions = adapter.discover(0)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.id == sid
    assert s.title == "Dark mode toggle"

    msgs = adapter.read_transcript(s)
    users = [m.text for m in msgs if m.role == "user"]
    assert "Add a dark mode toggle" in users
    assert "Now fix the migration bug" in users

    adapter.set_title(s, "Fix DB migration")
    assert claude_code._last_ai_title(f) == "Fix DB migration"


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #
def test_codex_adapter_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "state_5.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, rollout_path TEXT, "
        "updated_at_ms INTEGER, cwd TEXT, archived INTEGER, first_user_message TEXT)"
    )
    tid = "019e0000-0000-7000-8000-000000000000"
    rollout = tmp_path / "rollout.jsonl"
    _write_jsonl(
        rollout,
        [
            {"type": "session_meta", "payload": {"id": tid}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Refactor the auth module"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ],
    )
    now_ms = int(time.time() * 1000)
    con.execute(
        "INSERT INTO threads VALUES (?,?,?,?,?,?,?)",
        (tid, "Old title", str(rollout), now_ms, "/proj", 0, "Refactor the auth module"),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(codex, "_find_state_db", lambda: db)
    adapter = codex.CodexAdapter()

    sessions = adapter.discover(0)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.id == tid and s.title == "Old title"

    msgs = adapter.read_transcript(s)
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].text == "Refactor the auth module"

    adapter.set_title(s, "Auth refactor")
    con = sqlite3.connect(db)
    got = con.execute("SELECT title FROM threads WHERE id=?", (tid,)).fetchone()[0]
    con.close()
    assert got == "Auth refactor"


def test_codex_skips_archived(tmp_path, monkeypatch):
    db = tmp_path / "state_5.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, rollout_path TEXT, "
        "updated_at_ms INTEGER, cwd TEXT, archived INTEGER, first_user_message TEXT)"
    )
    now_ms = int(time.time() * 1000)
    con.execute(
        "INSERT INTO threads VALUES (?,?,?,?,?,?,?)",
        ("a", "T", "", now_ms, "/p", 1, "x"),  # archived
    )
    con.commit()
    con.close()
    monkeypatch.setattr(codex, "_find_state_db", lambda: db)
    assert codex.CodexAdapter().discover(0) == []


# --------------------------------------------------------------------------- #
# Cursor
# --------------------------------------------------------------------------- #
def test_cursor_adapter_roundtrip(tmp_path, monkeypatch):
    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    cid = "comp-1"
    now_ms = int(time.time() * 1000)
    headers = {"allComposers": [{"composerId": cid, "name": "Old Name", "lastUpdatedAt": now_ms}]}
    con.execute(
        "INSERT INTO ItemTable VALUES (?,?)",
        ("composer.composerHeaders", json.dumps(headers)),
    )
    composer_data = {
        "composerId": cid,
        "name": "Old Name",
        "fullConversationHeadersOnly": [
            {"bubbleId": "b1", "type": 1},
            {"bubbleId": "b2", "type": 2},
        ],
    }
    con.execute(
        "INSERT INTO cursorDiskKV VALUES (?,?)",
        (f"composerData:{cid}", json.dumps(composer_data)),
    )
    con.execute(
        "INSERT INTO cursorDiskKV VALUES (?,?)",
        (f"bubbleId:{cid}:b1", json.dumps({"type": 1, "text": "Optimize the SQL query"})),
    )
    con.execute(
        "INSERT INTO cursorDiskKV VALUES (?,?)",
        (f"bubbleId:{cid}:b2", json.dumps({"type": 2, "text": "Sure"})),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(cursor, "_vscdb", lambda: db)
    adapter = cursor.CursorAdapter()

    sessions = adapter.discover(0)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.id == cid and s.title == "Old Name"

    msgs = adapter.read_transcript(s)
    assert msgs[0].role == "user" and "SQL" in msgs[0].text
    assert msgs[1].role == "assistant"

    adapter.set_title(s, "SQL optimization")
    con = sqlite3.connect(db)
    hraw = con.execute(
        "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
    ).fetchone()[0]
    craw = con.execute(
        "SELECT value FROM cursorDiskKV WHERE key=?", (f"composerData:{cid}",)
    ).fetchone()[0]
    con.close()
    assert json.loads(hraw)["allComposers"][0]["name"] == "SQL optimization"
    assert json.loads(craw)["name"] == "SQL optimization"


def _cursor_db_with_headers(tmp_path, cid, composer_data_value):
    """Build a Cursor DB with a header row and an optional composerData row."""
    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    headers = {
        "allComposers": [
            {"composerId": cid, "name": "Old", "lastUpdatedAt": int(time.time() * 1000)}
        ]
    }
    con.execute(
        "INSERT INTO ItemTable VALUES (?,?)", ("composer.composerHeaders", json.dumps(headers))
    )
    if composer_data_value is not None:
        con.execute(
            "INSERT INTO cursorDiskKV VALUES (?,?)", (f"composerData:{cid}", composer_data_value)
        )
    con.commit()
    con.close()
    return db


def _header_name(db, cid):
    con = sqlite3.connect(db)
    raw = con.execute(
        "SELECT value FROM ItemTable WHERE key='composer.composerHeaders'"
    ).fetchone()[0]
    con.close()
    return json.loads(raw)["allComposers"][0]["name"]


def test_cursor_set_title_missing_blob_raises_and_rolls_back(tmp_path, monkeypatch):
    cid = "comp-x"
    db = _cursor_db_with_headers(tmp_path, cid, composer_data_value=None)  # no composerData row
    monkeypatch.setattr(cursor, "_vscdb", lambda: db)
    adapter = cursor.CursorAdapter()
    s = adapter.discover(0)[0]
    with pytest.raises(Exception):
        adapter.set_title(s, "New")
    assert _header_name(db, cid) == "Old"  # header NOT half-updated


def test_cursor_set_title_corrupt_blob_rolls_back(tmp_path, monkeypatch):
    cid = "comp-y"
    db = _cursor_db_with_headers(tmp_path, cid, composer_data_value="{not valid json")
    monkeypatch.setattr(cursor, "_vscdb", lambda: db)
    adapter = cursor.CursorAdapter()
    s = adapter.discover(0)[0]
    with pytest.raises(Exception):
        adapter.set_title(s, "New")
    assert _header_name(db, cid) == "Old"  # atomic: header untouched


# --------------------------------------------------------------------------- #
# Robustness: malformed / missing data must degrade gracefully, not crash
# --------------------------------------------------------------------------- #
def test_claude_skips_corrupt_lines(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    proj = projects / "-Users-me-proj"
    proj.mkdir(parents=True)
    sid = "c0000000-0000-0000-0000-000000000000"
    f = proj / f"{sid}.jsonl"
    f.write_text(
        '{"type":"last-prompt","lastPrompt":"Real request one","sessionId":"%s"}\n'
        "this line is not json at all {oops\n"
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"text","text":"ok"}]}}\n'
        '{"type":"ai-title","aiTitle":"Good title","sessionId":"%s"}\n' % (sid, sid)
    )
    monkeypatch.setattr(claude_code, "_projects_root", lambda: projects)
    adapter = claude_code.ClaudeCodeAdapter()
    s = adapter.discover(0)[0]
    assert s.title == "Good title"  # ai-title read past the corrupt line
    users = [m.text for m in adapter.read_transcript(s) if m.role == "user"]
    assert "Real request one" in users  # corrupt line skipped, real prompt kept


def test_codex_read_transcript_falls_back_when_rollout_missing(tmp_path, monkeypatch):
    db = tmp_path / "state_5.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, rollout_path TEXT, "
        "updated_at_ms INTEGER, cwd TEXT, archived INTEGER, first_user_message TEXT)"
    )
    now_ms = int(time.time() * 1000)
    con.execute(
        "INSERT INTO threads VALUES (?,?,?,?,?,?,?)",
        ("t1", "Title", "/no/such/rollout.jsonl", now_ms, "/p", 0, "The original request"),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(codex, "_find_state_db", lambda: db)
    adapter = codex.CodexAdapter()
    s = adapter.discover(0)[0]
    msgs = adapter.read_transcript(s)  # rollout file is gone
    assert len(msgs) == 1
    assert msgs[0].text == "The original request"  # falls back to first_user_message


def test_cursor_discover_survives_corrupt_headers(tmp_path, monkeypatch):
    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    con.execute(
        "INSERT INTO ItemTable VALUES (?,?)",
        ("composer.composerHeaders", "{this is not valid json"),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(cursor, "_vscdb", lambda: db)
    assert cursor.CursorAdapter().discover(0) == []  # no crash, just empty
