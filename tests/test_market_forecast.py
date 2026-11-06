from src.market_forecast import (
    FORECAST_TITLE,
    build_taiex_chart_image,
    build_taiex_forecast,
    format_taiex_forecast,
    format_taiex_forecast_flex,
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


def test_forecast_flex_message_contains_chart_and_forecast_text():
    forecast = build_taiex_forecast(_falling_index_rows())

    message = format_taiex_forecast_flex(forecast, "https://tmpfiles.org/dl/chart.png")

    assert message["type"] == "flex"
    assert message["contents"]["hero"]["url"] == "https://tmpfiles.org/dl/chart.png"
    body_text = "\n".join(
        item["text"] for item in message["contents"]["body"]["contents"]
        if item["type"] == "text"
    )
    assert FORECAST_TITLE in body_text
    assert forecast["direction"] in body_text
    assert "近20個交易日" in body_text


def test_build_taiex_chart_uses_shared_desktop_chart_with_20_sessions(monkeypatch):
    import pandas as pd

    import src.market_forecast as market_forecast

    price_df = pd.DataFrame(
        {
            "open": range(20),
            "high": range(1, 21),
            "low": range(20),
            "close": range(1, 21),
            "volume": range(100, 120),
        },
        index=pd.date_range("2026-10-01", periods=20),
    )
    calls = {}

    class FakeFigure:
        layout = type("Layout", (), {"width": 1000})()

        def update_layout(self, **kwargs):
            calls["layout"] = kwargs

        def to_image(self, **kwargs):
            calls["to_image"] = kwargs
            return b"rendered-png"

    monkeypatch.setattr(market_forecast.chart_data, "load_price_history", lambda conn, stock_id, days: (
        calls.update({"stock_id": stock_id, "days": days}) or price_df
    ))
    monkeypatch.setattr(market_forecast.chart_data, "load_holidays_for_chart", lambda df: ([], True))
    monkeypatch.setattr(
        market_forecast.chart_data,
        "build_candlestick_figure",
        lambda df, **kwargs: calls.update({"figure_options": kwargs}) or FakeFigure(),
    )

    image = build_taiex_chart_image(object())

    assert image == b"rendered-png"
    assert calls["days"] == 20
    assert calls["figure_options"]["show_sar"] is True
    assert calls["figure_options"]["show_macd"] is True
    assert calls["figure_options"]["show_kd"] is True
    assert len(price_df) == 20


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
