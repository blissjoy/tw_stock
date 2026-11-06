"""desktop/main_window.py裡不依賴Qt元件實例的純函式測試——只需要import模組本身(定義
QWidget子類別不需要QApplication)，不需要真的建立視窗，跟其餘UI邏輯分開驗證。"""

from types import SimpleNamespace

from PySide6.QtCore import QDate

from desktop.main_window import (
    CANDIDATE_SIGNAL_MAX_LINES,
    MainWindow,
    _should_follow_latest_sar_date,
    _truncate_signal_lines,
)


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


def test_sar_simulation_end_date_follows_new_latest_market_date():
    assert _should_follow_latest_sar_date("2026-10-30", "2026-10-30")


def test_sar_simulation_end_date_preserves_manual_historical_selection():
    assert not _should_follow_latest_sar_date("2026-10-15", "2026-10-30")


def test_sar_simulation_end_date_follows_latest_without_previous_market_date():
    assert _should_follow_latest_sar_date("2026-10-30", None)


class _FakeDateInput:
    def __init__(self, value):
        self.value = value
        self.minimum = None
        self.maximum = None

    def date(self):
        return QDate.fromString(self.value, "yyyy-MM-dd")

    def setMinimumDate(self, value):
        self.minimum = value.toString("yyyy-MM-dd")

    def setMaximumDate(self, value):
        self.maximum = value.toString("yyyy-MM-dd")

    def setDate(self, value):
        self.value = value.toString("yyyy-MM-dd")


class _FakeMarketConnection:
    def __init__(self, first_date, latest_date):
        self.dates = (first_date, latest_date)

    def execute(self, query):
        assert "MIN(date), MAX(date)" in query
        return self

    def fetchone(self):
        return self.dates


def _fake_sar_simulation_window(selected_end_date, previous_latest_date):
    return SimpleNamespace(
        conn=_FakeMarketConnection("2023-01-03", "2026-11-06"),
        sar_sim_start_date=_FakeDateInput("2025-11-06"),
        sar_sim_end_date=_FakeDateInput(selected_end_date),
        _sar_simulation_latest_date=previous_latest_date,
    )


def test_sar_simulation_tab_refreshes_date_range_and_follows_new_latest_data():
    window = _fake_sar_simulation_window("2026-10-30", "2026-10-30")

    MainWindow._refresh_sar_simulation_tab(window)

    assert window.sar_sim_end_date.value == "2026-11-06"
    assert window.sar_sim_end_date.maximum == "2026-11-06"
    assert window.sar_sim_start_date.maximum == "2026-11-06"
    assert window._sar_simulation_latest_date == "2026-11-06"


def test_sar_simulation_tab_refresh_preserves_manual_end_date():
    window = _fake_sar_simulation_window("2026-10-15", "2026-10-30")

    MainWindow._refresh_sar_simulation_tab(window)

    assert window.sar_sim_end_date.value == "2026-10-15"
    assert window.sar_sim_end_date.maximum == "2026-11-06"
