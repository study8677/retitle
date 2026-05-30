import time

from retitle.adapters.base import Adapter
from retitle.config import Config
from retitle.engine import Engine
from retitle.models import Message, Session
from retitle.namers.base import Namer
from retitle.state import StateStore


class FakeAdapter(Adapter):
    name = "fake"
    label = "Fake"

    def __init__(self, sessions, transcripts):
        self._sessions = sessions
        self._transcripts = transcripts
        self.writes: list[tuple[str, str]] = []

    def available(self):
        return True

    def discover(self, since):
        return [s for s in self._sessions if s.last_active >= since]

    def read_transcript(self, session):
        return self._transcripts[session.id]

    def set_title(self, session, title):
        self.writes.append((session.id, title))
        session.title = title


class FakeNamer(Namer):
    name = "fake"

    def __init__(self, title="Generated Title"):
        self._title = title

    def generate(self, messages, *, old_title=None, cwd=None, tool=None):
        return self._title


def _engine(tmp_path, adapter, namer, **cfg_kw):
    cfg = Config(idle_seconds=300, max_age_days=30, min_user_messages=1, **cfg_kw)
    state = StateStore(tmp_path / "state.json")
    return Engine(cfg, [adapter], namer, state)


def _idle_session(sid="s1", title="Old"):
    return Session("fake", sid, title, last_active=time.time() - 600)


TRANSCRIPT = {"s1": [Message("user", "Build the billing export feature")]}


def test_renames_idle_changed_session(tmp_path):
    adapter = FakeAdapter([_idle_session()], TRANSCRIPT)
    eng = _engine(tmp_path, adapter, FakeNamer("Billing export"))
    renamed, total = eng.tick()
    assert renamed == 1
    assert adapter.writes == [("s1", "Billing export")]


def test_skips_active_session(tmp_path):
    s = Session("fake", "s1", "Old", last_active=time.time())  # just now
    adapter = FakeAdapter([s], TRANSCRIPT)
    eng = _engine(tmp_path, adapter, FakeNamer())
    renamed, _ = eng.tick()
    assert renamed == 0
    assert adapter.writes == []


def test_idempotent_no_double_rename(tmp_path):
    adapter = FakeAdapter([_idle_session()], TRANSCRIPT)
    eng = _engine(tmp_path, adapter, FakeNamer("Billing export"))
    eng.tick()
    eng.tick()  # nothing changed -> must not rename again
    assert len(adapter.writes) == 1


def test_respects_manual_edit_until_content_changes(tmp_path):
    s = _idle_session()
    adapter = FakeAdapter([s], TRANSCRIPT)
    eng = _engine(tmp_path, adapter, FakeNamer("Billing export"))
    eng.tick()  # -> "Billing export"
    # user renames by hand; content (and last_active) unchanged
    s.title = "My custom name"
    eng.tick()
    assert s.title == "My custom name"  # not overwritten
    assert len(adapter.writes) == 1


def test_renames_again_after_new_content(tmp_path):
    s = _idle_session()
    transcripts = {"s1": [Message("user", "first task")]}
    adapter = FakeAdapter([s], transcripts)
    eng = _engine(tmp_path, adapter, FakeNamer("First"))
    eng.tick()
    # new activity arrives
    transcripts["s1"] = [Message("user", "first task"), Message("user", "second task")]
    s.last_active = time.time() - 400  # advanced, still idle
    eng.namer = FakeNamer("Second")
    eng.tick()
    assert adapter.writes == [("s1", "First"), ("s1", "Second")]


def test_dry_run_writes_nothing(tmp_path):
    adapter = FakeAdapter([_idle_session()], TRANSCRIPT)
    eng = _engine(tmp_path, adapter, FakeNamer(), dry_run=True)
    renamed, _ = eng.tick()
    assert renamed == 0
    assert adapter.writes == []


def test_discover_failure_preserves_state(tmp_path):
    """If an adapter's discover() throws, its state must NOT be pruned —
    otherwise the next pass treats every session as new and could clobber
    titles the user edited by hand."""
    adapter = FakeAdapter([_idle_session()], TRANSCRIPT)
    eng = _engine(tmp_path, adapter, FakeNamer("Billing export"))
    eng.tick()
    assert eng.state.get("fake", "s1") is not None
    assert eng.state.get("fake", "s1").get("content_sig")

    def boom(_since):
        raise RuntimeError("database is locked")

    adapter.discover = boom
    eng.tick()  # discover fails this pass
    assert eng.state.get("fake", "s1") is not None  # state survived
    assert eng.state.get("fake", "s1").get("content_sig")


def test_skips_reread_when_no_activity(tmp_path):
    """Once evaluated, an unchanged session is skipped without re-reading."""
    s = _idle_session()
    reads = {"n": 0}
    transcripts = {"s1": [Message("user", "Build the billing export feature")]}

    class CountingAdapter(FakeAdapter):
        def read_transcript(self, session):
            reads["n"] += 1
            return transcripts[session.id]

    adapter = CountingAdapter([s], transcripts)
    eng = _engine(tmp_path, adapter, FakeNamer("Billing export"))
    eng.tick()
    eng.tick()
    eng.tick()
    assert reads["n"] == 1  # only the first pass read the transcript


def test_tick_limit_renames_most_recent_first(tmp_path):
    now = time.time()
    sessions = [
        Session("fake", f"s{i}", f"old{i}", last_active=now - 600 - i) for i in range(5)
    ]
    transcripts = {f"s{i}": [Message("user", f"build feature number {i}")] for i in range(5)}
    adapter = FakeAdapter(sessions, transcripts)
    eng = _engine(tmp_path, adapter, FakeNamer("Fresh"))
    renamed, total = eng.tick(limit=2)
    assert total == 5  # all are candidates
    assert renamed == 2  # but only 2 renamed this pass
    assert {sid for sid, _ in adapter.writes} == {"s0", "s1"}  # most recent first


def test_batch_size_caps_renames_per_pass(tmp_path):
    now = time.time()
    sessions = [
        Session("fake", f"s{i}", f"o{i}", last_active=now - 600 - i) for i in range(5)
    ]
    transcripts = {f"s{i}": [Message("user", f"task {i}")] for i in range(5)}
    adapter = FakeAdapter(sessions, transcripts)
    eng = _engine(tmp_path, adapter, FakeNamer("X"), batch_size=3)
    renamed, total = eng.tick()  # no explicit limit -> uses batch_size
    assert total == 5
    assert renamed == 3


def test_end_to_end_real_claude_adapter(tmp_path, monkeypatch):
    """Full path: real ClaudeCodeAdapter + real Engine + real file I/O."""
    import json
    import os

    from retitle.adapters import claude_code
    from retitle.namers.heuristic import HeuristicNamer

    projects = tmp_path / "projects"
    proj = projects / "-Users-me-proj"
    proj.mkdir(parents=True)
    sid = "22222222-2222-2222-2222-222222222222"
    f = proj / f"{sid}.jsonl"
    rows = [
        {
            "type": "last-prompt",
            "lastPrompt": "Implement CSV export for the reports page",
            "sessionId": sid,
        },
        {"type": "ai-title", "aiTitle": "Initial topic", "sessionId": sid},
    ]
    f.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    old = time.time() - 600  # idle 10 minutes
    os.utime(f, (old, old))

    monkeypatch.setattr(claude_code, "_projects_root", lambda: projects)
    eng = _engine(tmp_path, claude_code.ClaudeCodeAdapter(), HeuristicNamer())
    renamed, _ = eng.tick()

    assert renamed == 1
    new_title = claude_code._last_ai_title(f)
    assert new_title != "Initial topic"
    assert "csv" in new_title.lower()
