from retitle import util
from retitle.models import Message


def test_is_noise_catches_harness_artifacts():
    assert util.is_noise("[Request interrupted by user]")
    assert util.is_noise('<subagent_notification> {"agent_path": "x"}')
    assert util.is_noise("<system-reminder>do thing</system-reminder>")
    assert util.is_noise('{"role": "tool", "data": 1}')
    assert util.is_noise("[{'tool_result': 1}]")
    assert not util.is_noise("Add a dark mode toggle to settings")
    assert not util.is_noise("修复登录页面的样式问题")


def test_is_trivial():
    assert util.is_trivial("ok")
    assert util.is_trivial("好的")
    assert util.is_trivial("   ")
    assert util.is_trivial("/clear")
    assert util.is_trivial("[Request interrupted by user]")
    assert not util.is_trivial("Refactor the authentication module")


def test_slash_command_triviality():
    assert util.is_trivial("/clear")  # bare command
    assert util.is_trivial("/help")
    # a slash-command carrying a (CJK, space-less) argument is NOT trivial
    assert not util.is_trivial("/goal 帮我把折扣率看板改成真实数据")
    assert not util.is_trivial("/fix the login redirect bug")


def test_clean_text_strips_tags_and_paths():
    out = util.clean_text("Fix `bug` in <b>file</b> at /Users/me/proj/app.py now")
    assert "`" not in out
    assert "<b>" not in out
    assert "/Users/me/proj/app.py" not in out
    assert "Fix" in out and "now" in out


def test_signature_changes_with_content():
    a = [Message("user", "hello world")]
    b = [Message("user", "hello world"), Message("assistant", "hi")]
    assert util.signature(a) != util.signature(b)
    # stable for identical content
    assert util.signature(a) == util.signature([Message("user", "hello world")])


def test_shape_title_trims_and_caps():
    assert util.shape_title('  "Add dark mode."  ') == "Add dark mode"
    long = "word " * 40
    assert len(util.shape_title(long)) <= 61  # 60 + ellipsis
    assert util.shape_title("fix bug")[0].isupper()


def test_fmt_dur():
    assert util.fmt_dur(45) == "45s"
    assert util.fmt_dur(300) == "5m"
    assert util.fmt_dur(3600) == "1h"
    assert util.fmt_dur(90000) == "1d"
