from src.market_forecast import (
    FORECAST_TITLE,
    build_taiex_forecast,
    format_taiex_forecast,
    record_taiex_forecast,
    verify_pending_taiex_forecast,
)


def _falling_index_rows():
    rows = []
    close = 20000.0
    for day in range(1, 31):
        previous_close = close
        close -= 10
        rows.append((
            f"2026-10-{day:02d}",
            previous_close,
            previous_close + 2,
            close - 2,
            close,
            1_000_000,
        ))
    return rows


def test_build_taiex_forecast_uses_short_term_breakdown_and_key_levels():
    forecast = build_taiex_forecast(_falling_index_rows())

    assert forecast["direction"] == "偏空"
    assert forecast["confidence"] == "中"
    assert forecast["ma_state"] == "短均線空頭排列向下"
    assert forecast["confirmation"].startswith("跌破今日低點")
    assert forecast["invalidation"].startswith("站回今日高點")
    message = format_taiex_forecast(forecast)
    assert message.startswith(FORECAST_TITLE)
    assert "今日低點" in message


def test_build_taiex_forecast_requires_sufficient_index_history():
    try:
        build_taiex_forecast(_falling_index_rows()[:19])
    except ValueError as exc:
        assert "不足20個交易日" in str(exc)
    else:
        raise AssertionError("Expected a clear error for insufficient index history")


def test_forecast_log_is_idempotent_and_verifiable(tmp_path):
    log_path = tmp_path / "MARKET_FORECAST_LOG.md"
    log_path.write_text("# Market forecast log\n", encoding="utf-8")
    forecast = build_taiex_forecast(_falling_index_rows())
    forecast["date"] = "2026-11-02"

    assert record_taiex_forecast(forecast, log_path)
    assert not record_taiex_forecast(forecast, log_path)
    assert verify_pending_taiex_forecast(
        forecast_date="2026-11-02",
        actual_date="2026-11-03",
        previous_close=forecast["close"],
        close=forecast["close"] - 100,
        high=forecast["high"],
        low=forecast["low"] - 110,
        log_path=log_path,
    )
    saved = log_path.read_text(encoding="utf-8")
    assert "預估資料日 2026-11-02" in saved
    assert "事後驗證**：前收" in saved
    assert "方向符合" in saved
    assert not verify_pending_taiex_forecast(
        forecast_date="2026-11-02",
        actual_date="2026-11-03",
        previous_close=forecast["close"],
        close=forecast["close"] - 100,
        high=forecast["high"],
        low=forecast["low"] - 110,
        log_path=log_path,
    )
