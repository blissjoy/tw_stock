"""停損停利資金管理分類（Layer 3）：全年獲利率勝率方程式(R-RISK-03)、套牢分級與不套牢紀律(R-RISK-05)。

R-RISK-03的書中原書表格第3列印刷有誤（"30次/50%(10勝10敗)"與交易次數矛盾），已依正文算式
訂正為15勝15敗，見規則檔案內的訂正說明；此處實作採用訂正後的數字。
"""

from __future__ import annotations

from src.rule_registry import implements_rule


@implements_rule("R-RISK-03")
def annual_profit_rate(total_trades: int, win_rate: float, profit_pct: float, loss_pct: float) -> float:
    """全年獲利率 = 勝場次數*停利% - 敗場次數*停損%。"""
    wins = total_trades * win_rate
    losses = total_trades * (1 - win_rate)
    return wins * profit_pct - losses * loss_pct


@implements_rule("R-RISK-03")
def required_win_rate(target_annual_profit_rate: float, total_trades: int, profit_pct: float, loss_pct: float) -> float:
    """反推應用：已知目標年化報酬率與交易次數/停損停利率，反推所需最低勝率。"""
    return (target_annual_profit_rate / total_trades + loss_pct) / (profit_pct + loss_pct)


@implements_rule("R-RISK-05")
def trapped_position_level(entry_price: float, current_price: float) -> str:
    """套牢分級：未實現虧損>=30%重度、>=20%中度、>=10%輕度，皆未達則未套牢。"""
    unrealized_loss_pct = (entry_price - current_price) / entry_price
    if unrealized_loss_pct >= 0.30:
        return "重度套牢"
    if unrealized_loss_pct >= 0.20:
        return "中度套牢"
    if unrealized_loss_pct >= 0.10:
        return "輕度套牢"
    return "未套牢"


@implements_rule("R-RISK-05")
def is_daily_warning_stock(prev_close: float, today_close: float, threshold: float = 0.05) -> bool:
    """每日警示股掃描：單日跌幅超過5%即列為警示股，準備賣出。"""
    daily_drop_pct = (prev_close - today_close) / prev_close
    return daily_drop_pct > threshold


@implements_rule("R-RISK-01")
def fixed_pct_stop_loss(entry_price: float, direction: str, pct: float = 0.05) -> float:
    """④固定比例停損法：買進價的2%~10%為停損點，硬性上限10%(書中明確，任何方法皆不可突破)。"""
    pct = max(0.02, min(pct, 0.10))
    if direction == "多":
        return entry_price * (1 - pct)
    return entry_price * (1 + pct)


@implements_rule("R-RISK-01")
def hits_absolute_stop_loss(
    consolidation_position_broken: bool = False,
    bull_high_reversed_to_bear: bool = False,
    bear_low_reversed_to_bull: bool = False,
    unrealized_loss_pct: float = 0.0,
    is_contrarian_position_reversed: bool = False,
) -> bool:
    """絕對停損：5種必須停損情境任一命中即強制平倉，優先權高於一般停損邏輯，不可拗單/攤平。"""
    return (
        consolidation_position_broken
        or bull_high_reversed_to_bear
        or bear_low_reversed_to_bull
        or unrealized_loss_pct >= 0.10
        or is_contrarian_position_reversed
    )


MARKET_PHASE_ALLOCATION_PCT = {
    "長期空頭": 0.20,
    "中期空頭": 0.30,
    "空頭確認初期": 0.40,
    "多頭確認初期": 0.30,
    "中期多頭": 0.50,
    "多頭確認且4線多排": 0.70,
    "高檔頭頭低末升段轉弱": 0.45,
}


@implements_rule("R-RISK-02")
def capital_exposure_limit(total_capital: float) -> float:
    """投入股市資金上限=可運用總資金的50%（書中明確，不可超過）。"""
    return total_capital * 0.5


@implements_rule("R-RISK-02")
def max_invested_amount(exposure_limit: float) -> float:
    """單一時點滿倉上限=股市曝險上限的90%（永遠保留至少10%現金）。"""
    return exposure_limit * 0.9


@implements_rule("R-RISK-02")
def target_invested_amount(total_capital: float, market_phase: str) -> float:
    """依大盤多空階段動態調整目標投入金額，不得超過90%滿倉上限。"""
    exposure_limit = capital_exposure_limit(total_capital)
    phase_pct = MARKET_PHASE_ALLOCATION_PCT.get(market_phase, 0.0)
    target = exposure_limit * phase_pct
    return min(target, max_invested_amount(exposure_limit))


@implements_rule("R-RISK-02")
def exceeds_position_concentration_limit(position_count: int, max_positions: int = 5) -> bool:
    """持股集中操作上限2~5檔，超過視為違反集中火力原則。"""
    return position_count > max_positions


STAGE_CONFIG = {
    "初階": {"capital": 500_000, "position_range": (1, 2), "uses_leverage": False, "half_year_target": 0.12},
    "進階": {"capital": 800_000, "position_range": (1, 3), "uses_leverage": False, "half_year_target": 0.30},
    "終極": {"capital": 1_000_000, "position_range": (2, 5), "uses_leverage": True, "half_year_target": 0.50},
}


@implements_rule("R-RISK-04")
def check_stage_compliance(stage: str, position_count: int, uses_leverage: bool) -> list[str]:
    """3階段資金規模合規檢核：持股檔數超過本階段上限、或本階段不應使用槓桿卻使用，回傳警示清單。"""
    config = STAGE_CONFIG[stage]
    warnings = []
    if position_count > config["position_range"][1]:
        warnings.append("持股檔數超過本階段上限，違反集中操作原則")
    if uses_leverage and not config["uses_leverage"]:
        warnings.append("本階段不應使用槓桿(融資)")
    return warnings


@implements_rule("R-RISK-04")
def compound_growth_path(principal: float = 100_000, annual_rate: float = 1.0, years: int = 8) -> list[float]:
    """千萬富翁複利路徑：本金*年報酬率複利成長，年報酬100%時第7年達成約1,280萬。"""
    path = [principal]
    for _ in range(years):
        path.append(path[-1] * (1 + annual_rate))
    return path


@implements_rule("R-RISK-04")
def is_weak_stock_needs_switch(period_return_pct: float, threshold: float = 0.05) -> bool:
    """牛步股換股條件：觀察週期(1週或半個月)漲幅低於5%即應換成強勢股。"""
    return period_return_pct < threshold


@implements_rule("R-RISK-06")
def is_high_win_rate_entry(matches_high_win_pattern: bool, hits_entry_taboos: bool) -> bool:
    """時機點1：符合高勝率進場型態，且未命中做多/做空10大戒律，才視為進場機會。"""
    return matches_high_win_pattern and not hits_entry_taboos


@implements_rule("R-RISK-06")
def should_take_profit(
    price_reached_resistance: bool,
    volume_weakening: bool,
    reversal_candle_signal: bool,
) -> bool:
    """時機點2：股價已達預估壓力位置，且量能轉弱或出現轉折K線訊號任一成立，才停利。"""
    return price_reached_resistance and (volume_weakening or reversal_candle_signal)


@implements_rule("R-RISK-06")
def stop_loss_and_reallocate(stop_loss_triggered: bool, high_win_rate_candidate_available: bool) -> str:
    """時機點3：觸發停損即平倉，資金不閒置，立即尋找下一個高勝率標的再配置。"""
    if not stop_loss_triggered:
        return "續抱"
    if high_win_rate_candidate_available:
        return "停損平倉並轉入新標的"
    return "停損平倉，暫無高勝率標的，資金待命"
