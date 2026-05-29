from retitle.models import Message
from retitle.namers.heuristic import HeuristicNamer

namer = HeuristicNamer()


def test_picks_latest_substantive_message():
    msgs = [
        Message("user", "Set up the project skeleton"),
        Message("assistant", "done"),
        Message("user", "好的"),  # trivial, ignored
        Message("user", "Now add pagination to the results list"),
    ]
    title = namer.generate(msgs)
    assert "pagination" in title.lower()


def test_strips_leading_slash_command():
    msgs = [Message("user", "/goal make the dashboard load faster please")]
    title = namer.generate(msgs)
    assert not title.startswith("/")
    assert "dashboard" in title.lower()


def test_ignores_noise_only_session():
    msgs = [
        Message("user", "[Request interrupted by user]"),
        Message("assistant", '{"tool_result": "x"}'),
    ]
    assert namer.generate(msgs) is None


def test_cjk_title_is_bounded():
    long_cjk = "帮我把这个特别长的中文需求描述变成一个合理长度的标题不要太长了谢谢"
    msgs = [Message("user", long_cjk)]
    title = namer.generate(msgs)
    assert title
    assert len(title) <= 22


def test_returns_none_without_user_messages():
    assert namer.generate([Message("assistant", "hello")]) is None
