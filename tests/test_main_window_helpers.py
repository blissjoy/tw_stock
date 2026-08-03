"""desktop/main_window.py裡不依賴Qt元件實例的純函式測試——只需要import模組本身(定義
QWidget子類別不需要QApplication)，不需要真的建立視窗，跟其餘UI邏輯分開驗證。"""

from desktop.main_window import CANDIDATE_SIGNAL_MAX_LINES, _truncate_signal_lines


def test_truncate_signal_lines_returns_dash_when_none():
    assert _truncate_signal_lines(None) == "-"


def test_truncate_signal_lines_keeps_short_list_unchanged():
    signal_name = "\n".join(f"規則{i}" for i in range(CANDIDATE_SIGNAL_MAX_LINES))
    assert _truncate_signal_lines(signal_name) == signal_name


def test_truncate_signal_lines_truncates_and_appends_more_count():
    """5490同亨這種同時符合13條規則的情境：只顯示前5條，最後一行改成"(...+8 more)"，
    省略掉的條數=13-5=8。"""
    signal_name = "\n".join(f"規則{i}" for i in range(13))

    result = _truncate_signal_lines(signal_name)

    lines = result.split("\n")
    assert len(lines) == CANDIDATE_SIGNAL_MAX_LINES + 1
    assert lines[:CANDIDATE_SIGNAL_MAX_LINES] == [f"規則{i}" for i in range(CANDIDATE_SIGNAL_MAX_LINES)]
    assert lines[-1] == "(...+8 more)"
