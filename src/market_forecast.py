"""Short-term TAIEX forecasts using the project's Zhu Jia-hong rule notes."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.indicators.moving_average import sma
from src.indicators.pivots import compute_turning_points

FORECAST_LOG_PATH = Path(__file__).resolve().parents[1] / "ai" / "MARKET_FORECAST_LOG.md"
FORECAST_TITLE = "【明日大盤預估｜朱老師短線】"


def build_taiex_forecast(rows: list[tuple]) -> dict:
    """Build a concise next-session forecast from dated TAIEX OHLCV rows."""
    if len(rows) < 20:
        raise ValueError(f"加權指數資料不足20個交易日（目前{len(rows)}筆），不產生預估。")

    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    frame = frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if len(frame) < 20:
        raise ValueError(f"加權指數有效交易日資料不足20筆（目前{len(frame)}筆），不產生預估。")

    close = frame["close"].astype(float)
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    ma5_series, ma10_series, ma20_series = (sma(close, period) for period in (5, 10, 20))
    ma5, ma10, ma20 = (series.iloc[-1] for series in (ma5_series, ma10_series, ma20_series))
    today = frame.iloc[-1]
    previous = frame.iloc[-2]
    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
        raise ValueError("加權指數均線資料不足，不產生預估。")

    turning_points = compute_turning_points(high, low, close, n=5)
    heads = [point.price for point in turning_points if point.type == "head"]
    bottoms = [point.price for point in turning_points if point.type == "bottom"]
    structure = "未確認短線轉折"
    if len(heads) >= 2 and len(bottoms) >= 2:
        if heads[-1] > heads[-2] and bottoms[-1] > bottoms[-2]:
            structure = "頭頭高、底底高"
        elif heads[-1] < heads[-2] and bottoms[-1] < bottoms[-2]:
            structure = "頭頭低、底底低"
        else:
            structure = "轉折高低點未同向，偏整理"

    ma_rising = all(series.iloc[-1] > series.iloc[-2] for series in (ma5_series, ma10_series, ma20_series))
    ma_falling = all(series.iloc[-1] < series.iloc[-2] for series in (ma5_series, ma10_series, ma20_series))
    if ma5 > ma10 > ma20 and ma_rising:
        ma_state = "短均線多頭排列向上"
    elif ma5 < ma10 < ma20 and ma_falling:
        ma_state = "短均線空頭排列向下"
    else:
        ma_state = "短均線排列或方向未確認"

    broke_high = float(today["close"]) > float(previous["high"])
    broke_low = float(today["close"]) < float(previous["low"])
    below_ma5 = float(today["close"]) < ma5
    above_ma5 = float(today["close"]) > ma5

    if structure == "頭頭高、底底高" and ma_state == "短均線多頭排列向上" and above_ma5:
        direction = "偏多"
        confidence = "高" if broke_high else "中"
    elif structure == "頭頭低、底底低" and ma_state == "短均線空頭排列向下" and below_ma5:
        direction = "偏空"
        confidence = "高" if broke_low else "中"
    elif broke_low and below_ma5:
        direction, confidence = "偏空", "中"
    elif broke_high and above_ma5:
        direction, confidence = "偏多", "中"
    else:
        direction, confidence = "區間／待確認", "低"

    if direction == "偏多":
        confirmation = f"站穩今日高點 {today['high']:.0f} 偏強"
        invalidation = f"跌破今日低點 {today['low']:.0f} 轉弱"
    elif direction == "偏空":
        confirmation = f"跌破今日低點 {today['low']:.0f} 續弱"
        invalidation = f"站回今日高點 {today['high']:.0f} 轉強"
    else:
        confirmation = f"突破今日高點 {today['high']:.0f} 才偏強"
        invalidation = f"跌破今日低點 {today['low']:.0f} 轉弱"

    return {
        "date": str(today["date"]),
        "direction": direction,
        "confidence": confidence,
        "structure": structure,
        "ma_state": ma_state,
        "close": float(today["close"]),
        "high": float(today["high"]),
        "low": float(today["low"]),
        "confirmation": confirmation,
        "invalidation": invalidation,
    }


def format_taiex_forecast(forecast: dict) -> str:
    return "\n".join([
        FORECAST_TITLE,
        f"方向：{forecast['direction']}｜信心：{forecast['confidence']}",
        f"依據：{forecast['structure']}；{forecast['ma_state']}",
        f"明日關卡：{forecast['confirmation']}；{forecast['invalidation']}",
        f"資料截至：{forecast['date']} 收盤",
    ])


def _forecast_sections(content: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^### 預估資料日 (\d{4}-\d{2}-\d{2}).*$", content))
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        sections.append((match.group(1), content[match.start():end]))
    return sections


def record_taiex_forecast(forecast: dict, log_path: Path = FORECAST_LOG_PATH) -> bool:
    """Append one forecast per target date; return False if already recorded."""
    content = log_path.read_text(encoding="utf-8")
    target_date = forecast["date"]
    if any(date == target_date for date, _ in _forecast_sections(content)):
        return False

    entry = f"""
### 預估資料日 {target_date}（預估下一交易日）

- **對象**：於 {target_date} 收盤後預估的下一交易日加權指數漲跌勢。
- **預估發布時間**：14:30 收盤資料更新完成後。
- **朱老師規則 ID 與實際套用項目**：`R-TREND-01`（5日轉折波）、`R-TREND-03`／`R-TREND-04`（頭頭高底底高／頭頭低底底低）、`R-CANDLE-01`（前一日高低點支撐壓力）、`R-STRATEGY-01`（短線均線判讀參考）。
- **資料截至／來源**：{target_date} 收盤；本機加權指數 OHLCV。
- **轉折波／頭頭高底底高或頭頭低底底低判讀**：{forecast['structure']}。
- **均線、K線支撐與壓力**：{forecast['ma_state']}；今日高 {forecast['high']:.2f} 作明日壓力參考，今日低 {forecast['low']:.2f} 作支撐參考。
- **原始主要判斷**：{forecast['direction']}；信心 {forecast['confidence']}。明日偏強條件：{forecast['confirmation']}；偏弱／失效條件：{forecast['invalidation']}。
- **資料完整度與限制**：使用當日本機已儲存的加權指數日線資料；這是規則化技術情境判讀，不是精確機率或保證。
- **事後驗證**：待驗證。
- **檢討**：待收盤驗證。
"""
    separator = "" if content.endswith("\n") else "\n"
    log_path.write_text(content + separator + entry, encoding="utf-8")
    return True


def verify_pending_taiex_forecast(
    forecast_date: str,
    actual_date: str,
    previous_close: float,
    close: float,
    high: float,
    low: float,
    log_path: Path = FORECAST_LOG_PATH,
) -> bool:
    """Fill the forecast's outcome when its target session has closed."""
    content = log_path.read_text(encoding="utf-8")
    sections = _forecast_sections(content)
    for target_date, section in sections:
        if target_date != forecast_date or "- **事後驗證**：待驗證。" not in section:
            continue

        change = close - previous_close
        pct = change / previous_close * 100 if previous_close else 0.0
        if change > 0:
            actual_direction = "上漲"
        elif change < 0:
            actual_direction = "下跌"
        else:
            actual_direction = "平盤"
        forecast_match = re.search(r"\*\*原始主要判斷\*\*：([^；]+)", section)
        expected = forecast_match.group(1) if forecast_match else "未知"
        if expected == "偏多" and change > 0 or expected == "偏空" and change < 0:
            review = "方向符合，但仍須獨立檢視預估的強弱與區間描述。"
        elif expected == "區間／待確認":
            review = "原預估未給出明確漲跌方向；按方向命中標準不計為命中。"
        else:
            review = "方向未符合實際收盤結果。"
        verification = (
            f"- **事後驗證**：前收 {previous_close:,.2f}；收盤 {close:,.2f}，"
            f"{actual_direction} {change:+,.2f} 點（{pct:+.2f}%）；"
            f"當日高 {high:,.2f}、低 {low:,.2f}。"
        )
        updated_section = section.replace("- **事後驗證**：待驗證。", verification)
        updated_section = updated_section.replace("- **檢討**：待收盤驗證。", f"- **檢討**：{review}")
        start = content.index(section)
        content = content[:start] + updated_section + content[start + len(section):]
        log_path.write_text(content, encoding="utf-8")
        return True
    return False
