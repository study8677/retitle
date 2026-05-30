import argparse
import json
import time

from retitle import cli
from retitle.config import Config
from retitle.models import Message, Session


class FakeAdapter:
    name = "fake"
    label = "FakeTool"

    def __init__(self, sessions, transcripts=None):
        self._sessions = sessions
        self._t = transcripts or {}

    def discover(self, since):
        return [s for s in self._sessions if s.last_active >= since]

    def read_transcript(self, s):
        return self._t.get(s.id, [])


def _args(**kw):
    base = dict(
        query="", content=False, tool=None, days=90, limit=30, verbose=False, json=False
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_search_by_title(capsys, monkeypatch):
    now = time.time()
    sessions = [
        Session("fake", "s1", "Fix the deploy script", last_active=now - 100),
        Session("fake", "s2", "Add dark mode toggle", last_active=now - 200),
    ]
    monkeypatch.setattr(cli, "get_adapters", lambda cfg: [FakeAdapter(sessions)])
    rc = cli.cmd_search(_args(query="deploy"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Fix the deploy script" in out
    assert "Add dark mode toggle" not in out
    assert "1 match" in out


def test_search_is_case_insensitive(capsys, monkeypatch):
    now = time.time()
    sessions = [Session("fake", "s1", "Deploy To Production", last_active=now)]
    monkeypatch.setattr(cli, "get_adapters", lambda cfg: [FakeAdapter(sessions)])
    cli.cmd_search(_args(query="DEPLOY"))
    assert "Deploy To Production" in capsys.readouterr().out


def test_search_no_match(capsys, monkeypatch):
    now = time.time()
    sessions = [Session("fake", "s1", "Hello world", last_active=now)]
    monkeypatch.setattr(cli, "get_adapters", lambda cfg: [FakeAdapter(sessions)])
    rc = cli.cmd_search(_args(query="zzz"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "No sessions matching" in out


def test_search_content_flag(capsys, monkeypatch):
    now = time.time()
    sessions = [Session("fake", "s1", "Generic title", last_active=now)]
    transcripts = {"s1": [Message("user", "we should migrate to postgres next sprint")]}
    monkeypatch.setattr(
        cli, "get_adapters", lambda cfg: [FakeAdapter(sessions, transcripts)]
    )

    # title alone does not match "postgres"
    cli.cmd_search(_args(query="postgres", content=False))
    assert "No sessions matching" in capsys.readouterr().out

    # --content searches the transcript and finds it
    cli.cmd_search(_args(query="postgres", content=True))
    out = capsys.readouterr().out
    assert "Generic title" in out
    assert "postgres" in out  # snippet shown


def test_search_respects_days_window(capsys, monkeypatch):
    now = time.time()
    sessions = [Session("fake", "s1", "old deploy notes", last_active=now - 100 * 86400)]
    monkeypatch.setattr(cli, "get_adapters", lambda cfg: [FakeAdapter(sessions)])
    cli.cmd_search(_args(query="deploy", days=30))  # 100 days ago > 30d window
    assert "No sessions matching" in capsys.readouterr().out


def test_search_json_output(capsys, monkeypatch):
    now = time.time()
    sessions = [
        Session("fake", "s1", "Deploy stuff", last_active=now - 50, cwd="/home/me/proj")
    ]
    monkeypatch.setattr(cli, "get_adapters", lambda cfg: [FakeAdapter(sessions)])
    cli.cmd_search(_args(query="deploy", json=True))
    data = json.loads(capsys.readouterr().out)
    assert data[0]["tool"] == "fake"
    assert data[0]["id"] == "s1"
    assert data[0]["title"] == "Deploy stuff"
    assert data[0]["cwd"] == "/home/me/proj"


def test_stats_json(capsys, monkeypatch):
    now = time.time()
    sessions = [
        Session("fake", "s1", "Has a title", last_active=now - 1000),  # stale
        Session("fake", "s2", None, last_active=now - 10),  # untitled, active
        Session("fake", "s3", "", last_active=now - 9999),  # untitled, stale
    ]
    monkeypatch.setattr(cli.config_mod, "load", lambda path=None: Config(idle_seconds=300))
    monkeypatch.setattr(cli, "get_adapters", lambda cfg: [FakeAdapter(sessions)])
    cli.cmd_stats(_args(days=0, json=True))
    data = json.loads(capsys.readouterr().out)
    assert data["total"]["sessions"] == 3
    assert data["total"]["untitled"] == 2
    assert data["total"]["stale"] == 2  # s1 + s3 (idle > 300s)
    assert data["tools"][0]["tool"] == "fake"


def test_stats_table(capsys, monkeypatch):
    now = time.time()
    sessions = [Session("fake", "s1", "Title", last_active=now - 1000)]
    monkeypatch.setattr(cli.config_mod, "load", lambda path=None: Config(idle_seconds=300))
    monkeypatch.setattr(cli, "get_adapters", lambda cfg: [FakeAdapter(sessions)])
    rc = cli.cmd_stats(_args(days=0))
    out = capsys.readouterr().out
    assert rc == 0
    assert "FakeTool" in out
    assert "Total" in out
