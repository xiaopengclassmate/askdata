"""会话上下文：只留最近几轮、能过期、能清空"""

from app import session


def test_append_and_history():
    s = session.SessionStore()
    sid = s.new_id()
    s.append(sid, "上个月各门店的营收", "SELECT 1", "各门店实收")
    s.append(sid, "那上个月呢", "SELECT 2", "")

    turns = s.history(sid)
    assert [t["question"] for t in turns] == ["上个月各门店的营收", "那上个月呢"]


def test_only_recent_turns_are_kept():
    s = session.SessionStore(max_turns=3)
    sid = s.new_id()
    for i in range(5):
        s.append(sid, f"问题{i}", f"SELECT {i}", "")
    assert len(s.history(sid)) == 3
    assert s.history(sid)[-1]["question"] == "问题4"


def test_sessions_do_not_leak():
    s = session.SessionStore()
    a, b = s.new_id(), s.new_id()
    s.append(a, "问 A", "", "")
    assert s.history(b) == []


def test_expired_session_is_dropped():
    s = session.SessionStore(ttl=-1)  # 立刻过期
    sid = s.new_id()
    s.append(sid, "问一句", "", "")
    assert s.history(sid) == []


def test_clear():
    s = session.SessionStore()
    sid = s.new_id()
    s.append(sid, "问一句", "", "")
    s.clear(sid)
    assert s.history(sid) == []


def test_render_history_keeps_two_turns_and_sql():
    turns = [
        {"question": "q1", "sql": "SELECT 1", "explanation": ""},
        {"question": "q2", "sql": "SELECT 2", "explanation": ""},
        {"question": "q3", "sql": "SELECT 3", "explanation": ""},
    ]
    text = session.render_history(turns)
    assert "q1" not in text          # 只带最近两轮
    assert "q2" in text and "SELECT 2" in text
    assert "SELECT 3" in text


def test_render_history_empty():
    assert session.render_history([]) == ""
