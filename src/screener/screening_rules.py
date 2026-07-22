"""選股策略分類（Layer 3）：環境濾網(R-SCREEN-17/18)、六六大順選股法(R-SCREEN-04)、
底部狹幅盤整突破鎖股(R-SCREEN-11)、緩漲軌道突破做多(R-SCREEN-15)。

做多/做空環境四大前提是「由上而下」(大盤→類股→個股→題材)的環境濾網，設計上應置於
個股層級技術面選股法之前執行；排名/強度計算屬於外部資料前處理，這裡只接收已算好的
排名/布林結果，不在此重新實作排名邏輯。
"""

from __future__ import annotations

from src.rule_registry import implements_rule


@implements_rule("R-SCREEN-17")
def long_environment_check(market_above_rising_ma20: bool, sector_in_top3: bool, stock_in_top3_of_sector: bool, has_hot_theme: bool = False) -> dict:
    """做多環境四大前提：大盤站上上揚月線 + 類股前3強 + 個股為該類股前3強，三者皆必要；題材為加分項。"""
    long_env_pass = market_above_rising_ma20 and sector_in_top3 and stock_in_top3_of_sector
    return {"做多環境成立": long_env_pass, "題材加分": has_hot_theme}


@implements_rule("R-SCREEN-18")
def short_environment_check(market_below_falling_ma20: bool, sector_in_bottom3: bool, stock_in_bottom3_of_sector: bool, is_priority_short_target: bool = False) -> dict:
    """做空環境四大前提：大盤在下彎月線之下 + 類股最弱前3 + 個股為該類股最弱前3，與做多前提鏡射對稱。"""
    short_env_pass = market_below_falling_ma20 and sector_in_bottom3 and stock_in_bottom3_of_sector
    return {"做空環境成立": short_env_pass, "優先目標加分": is_priority_short_target}


@implements_rule("R-SCREEN-04")
def liu_liu_da_shun_score(trend: str, kbar_ok: bool, ma_ok: bool, volume_ok: bool, indicator_ok: bool) -> dict:
    """六六大順選股法：趨勢先篩選(盤整直接退出觀察)，其餘K線/均線/成交量/指標4構面至少3項通過才列為候選。"""
    if trend == "盤整":
        return {"result": "退出觀察", "trend": trend}
    checks = {"kbar_ok": kbar_ok, "ma_ok": ma_ok, "volume_ok": volume_ok, "indicator_ok": indicator_ok}
    pass_count = sum(checks.values())
    return {"result": "候選" if pass_count >= 3 else "觀察", "trend": trend, "pass_count": pass_count, "checks": checks}


@implements_rule("R-SCREEN-11")
def narrow_range_bottom_breakout(duration_months: float, is_red_k: bool, close: float, consolidation_upper: float, volume: float, range_avg_volume: float, min_months: float = 2.0, volume_multiple: float = 2.0) -> bool:
    """底部狹幅盤整大量紅K突破鎖股：盤整須達2個月以上，收紅K站上盤整區上緣，且量能達盤整期均量2倍以上。"""
    if duration_months < min_months:
        return False
    breakout = close > consolidation_upper
    big_volume = volume >= volume_multiple * range_avg_volume
    return is_red_k and breakout and big_volume


@implements_rule("R-SCREEN-15")
def slow_rally_channel_breakout(close: float, channel_upper_value: float, is_long_red_k: bool, volume: float, avg_volume_20: float, volume_multiple: float = 2.0) -> bool:
    """緩漲上升軌道線突破大量長紅K做多：收盤(非僅盤中)突破緩漲上升軌道線，且為大量長紅K。"""
    close_above_channel = close > channel_upper_value
    big_volume = volume >= volume_multiple * avg_volume_20
    return close_above_channel and is_long_red_k and big_volume


@implements_rule("R-SCREEN-12")
def bear_rebound_above_ma20_range_breakout(
    was_downtrend: bool, stayed_above_ma20_since_rebound: bool,
    is_red_k: bool, close: float, consolidation_upper: float,
    volume: float, range_avg_volume: float, volume_multiple: float = 2.0,
) -> bool:
    """空頭強勢反彈月線上橫盤大量紅K突破鎖股：反彈後站上月線未再破底並橫盤，大量紅K突破橫盤區進場。"""
    if not (was_downtrend and stayed_above_ma20_since_rebound):
        return False
    breakout = close > consolidation_upper
    big_volume = volume >= volume_multiple * range_avg_volume
    return is_red_k and breakout and big_volume


@implements_rule("R-SCREEN-14")
def slow_rally_attack_signal(bullish_aligned_line_count: int, is_gradual_uptrend: bool, is_long_red_k: bool, volume: float, avg_volume_20: float, volume_multiple: float = 3.0) -> bool:
    """緩步上漲後大量長紅K攻擊位置：均線多排持續的緩漲階段中，突然出現大量(預設3倍均量)長紅K即為攻擊訊號。"""
    slow_rally = bullish_aligned_line_count >= 3 and is_gradual_uptrend
    if not slow_rally:
        return False
    huge_volume = volume >= volume_multiple * avg_volume_20
    return is_long_red_k and huge_volume


@implements_rule("R-SCREEN-01")
def classify_screening_mode(mode: str) -> str:
    """投資/投機選股標的池分流：投資母體鎖定台灣50/中型100成分股套用基本面查核；投機母體為中小型股套用技術面選股法。"""
    if mode == "投資":
        return "母體=台灣50/台灣中型100成分股，套用基本面查核清單"
    if mode == "投機":
        return "母體=中小型股，套用技術面選股法"
    raise ValueError(f"mode 必須是 '投資' 或 '投機'，收到：{mode!r}")


@implements_rule("R-SCREEN-02")
def direction_by_trend_only(trend_state: str) -> str:
    """公司沒有好壞只有多空：進出場方向完全依技術面趨勢判斷，不因基本面優劣或股價絕對高低排除候選股。"""
    if trend_state == "多頭":
        return "允許做多，不因基本面差或股價創新高而排除"
    if trend_state == "空頭":
        return "允許做空，不因股價已低而排除，也不因公司體質尚可而排除做空"
    return "盤整，暫不進場，等待趨勢明朗"


FUNDAMENTAL_CHECK_FIELDS = {
    "基本資料": ["股本", "營業項目", "董事會結構"],
    "經營績效": ["營業額", "獲利率", "年增率", "淨值", "本益比", "ROE"],
    "財務結構": ["資產負債表", "損益表", "現金流量表"],
    "獲利盈餘分配": ["歷年股利紀錄"],
    "價值評估": ["合理價值估算"],
    "經營者理念": ["經營者相關揭露"],
}


@implements_rule("R-SCREEN-03")
def fundamental_data_completeness_check(available_fields: set[str]) -> tuple[bool, list[str]]:
    """基本面選股六大類查核清單：僅檢查6大類必查欄位是否齊全，書中未給各欄位合格門檻，不做是否合格的判斷。"""
    missing = [f for group in FUNDAMENTAL_CHECK_FIELDS.values() for f in group if f not in available_fields]
    return len(missing) == 0, missing


@implements_rule("R-SCREEN-05")
def meets_capital_size_threshold(capital: float, max_capital: float = 5_000_000_000) -> bool:
    """4基10技選股法之「股本」項：50億元以下的中小型股，較受主力喜愛(書中明確數字)。"""
    return capital <= max_capital


@implements_rule("R-SCREEN-05")
def si_ji_shi_ji_score(fund_checks: dict[str, bool], tech_checks: dict[str, bool]) -> dict:
    """4基10技綜合評分：基本面符合項數與技術面符合項數，基本面達3項以上且技術面達4項以上建議進場。"""
    pass_fund = sum(fund_checks.values())
    pass_tech = sum(tech_checks.values())
    return {"基本面符合項數": pass_fund, "技術面符合項數": pass_tech, "建議進場": pass_fund >= 3 and pass_tech >= 4}


@implements_rule("R-SCREEN-06")
def excessive_rally_then_consolidation(swing_gain_pct: float, is_consolidation: bool, gain_threshold: float = 1.0) -> bool:
    """淘汰法第5項：波段漲幅達100%以上(書中明確數字)且已轉為盤整趨勢，應立刻出場排除。"""
    return swing_gain_pct >= gain_threshold and is_consolidation


@implements_rule("R-SCREEN-06")
def repeated_big_black_at_resistance(count_big_black_near_resistance: int, min_count: int = 2) -> bool:
    """淘汰法第6項：壓力線附近重複出現大量長黑K(預設2次以上)應排除。"""
    return count_big_black_near_resistance >= min_count


@implements_rule("R-SCREEN-06")
def institutional_sell_streak_or_black_k_cluster(
    institutional_sell_streak_days: int, consecutive_black_k_big_volume: int,
    streak_threshold: int = 3, black_k_threshold: int = 3,
) -> bool:
    """淘汰法第8項：三大法人連續賣超達門檻(書中明確3天)、或連續大量黑K達門檻，應排除。"""
    return institutional_sell_streak_days >= streak_threshold or consecutive_black_k_big_volume >= black_k_threshold


@implements_rule("R-SCREEN-06")
def frequent_big_volume_no_price_move(
    big_volume_day_count: int, price_change_pct: float, count_threshold: int = 3, flat_threshold: float = 0.03,
) -> bool:
    """淘汰法第9項：觀察窗內頻繁出現大量但股價原地不動，應排除；次數與觀察窗書中未給精確數字，此處為工程估計值。"""
    return big_volume_day_count >= count_threshold and abs(price_change_pct) < flat_threshold


@implements_rule("R-SCREEN-06")
def should_exclude_candidate(exclusion_flags: dict[str, bool]) -> tuple[bool, list[str]]:
    """淘汰法11條排除條件彙總：任一項成立即應排除候選股，回傳(是否排除, 命中原因清單)。"""
    reasons = [name for name, hit in exclusion_flags.items() if hit]
    return len(reasons) > 0, reasons


@implements_rule("R-SCREEN-07")
def special_quote_prefilter(volume: float, close_price: float, min_volume: float = 1000, min_price: float = 5.0) -> bool:
    """特別報價選股法步驟2-3：去除成交量低於1,000張、股價低於5元的股票(書中明確數字)。"""
    return volume >= min_volume and close_price >= min_price


@implements_rule("R-SCREEN-08")
def intraday_one_minute_surge(price_now: float, price_1min_ago: float, threshold: float = 0.02) -> bool:
    """盤中選股設定法第7項：1分鐘內漲幅達2%以上(書中明確數字)。"""
    return (price_now - price_1min_ago) / price_1min_ago >= threshold


@implements_rule("R-SCREEN-08")
def intraday_consecutive_ask_side_large_trades(consecutive_count: int, min_count: int = 3) -> bool:
    """盤中選股設定法第8項：連續3筆(書中明確筆數)外盤大量成交。"""
    return consecutive_count >= min_count


@implements_rule("R-SCREEN-08")
def intraday_turns_red(current_price: float, prev_close: float, open_price: float) -> bool:
    """盤中選股設定法第4項：股價翻紅，開盤不高於昨收但現價已突破昨收。"""
    return current_price > prev_close and open_price <= prev_close


@implements_rule("R-SCREEN-08")
def limit_up_reopened(was_limit_up_yesterday: bool, current_price: float, limit_up_price: float) -> bool:
    """盤中選股設定法第5項：連續漲停打開，昨日漲停、今日現價已低於漲停價。"""
    return was_limit_up_yesterday and current_price < limit_up_price


@implements_rule("R-SCREEN-08")
def limit_down_reopened(was_limit_down_yesterday: bool, current_price: float, limit_down_price: float) -> bool:
    """盤中選股設定法第6項：連續跌停打開，與漲停打開鏡射對稱。"""
    return was_limit_down_yesterday and current_price > limit_down_price


@implements_rule("R-SCREEN-09")
def base_consolidation_lock_and_breakout(
    broke_prior_low: bool, in_range_consolidation: bool, big_red_breakout: bool, bullish_aligned_line_count: int,
) -> str | None:
    """未卜先知觀察一：底部盤整未破前低即鎖股；大量紅K突破後，3線多排做短多、4線多排規劃做長多。"""
    if broke_prior_low or not in_range_consolidation:
        return None
    if not big_red_breakout:
        return "鎖股（底部盤整），等待大量紅K突破"
    if bullish_aligned_line_count >= 4:
        return "大量紅K突破底部盤整，4線多排向上，規劃做長多"
    if bullish_aligned_line_count >= 3:
        return "大量紅K突破底部盤整，3線多排向上，開始做短多"
    return None


@implements_rule("R-SCREEN-09")
def ma_tangle_min_duration_lock(tangle_duration_months: float, big_red_breakout: bool, min_months: float = 2.0) -> str | None:
    """未卜先知觀察二：均線糾結須達2個月(書中明確門檻)才開始鎖股；大量紅K突破盤整區即進場。"""
    if tangle_duration_months < min_months:
        return None
    return "大量紅K突破盤整區，短多或長多" if big_red_breakout else "鎖股（均線糾結已達2個月），等待突破"


@implements_rule("R-SCREEN-10")
def second_wave_entry_signal(wave1_confirmed: bool, pullback_detected: bool, is_attack_volume: bool, close_up: bool) -> str | None:
    """強勢飆股第2波選股法：第1波確認且已回檔修正後，出現攻擊量紅K才進場搶第2波。"""
    if not (wave1_confirmed and pullback_detected):
        return None
    if is_attack_volume and close_up:
        return "第2波進場"
    return "等待第2波確認"


@implements_rule("R-SCREEN-13")
def double_bottom_no_new_low(bottom1_low: float, bottom2_low: float) -> bool:
    """雙盤底條件①：第2次底低點不創新低(第2次底低點 >= 第1次底低點)。"""
    return bottom2_low >= bottom1_low


@implements_rule("R-SCREEN-13")
def double_bottom_breakout_signal(
    no_new_low: bool, is_red_k: bool, close: float, resistance: float,
    volume: float, range_avg_volume: float, volume_multiple: float = 2.0,
) -> bool:
    """雙盤底大量紅K突破進場：雙底不創新低+大量(2倍區間均量)紅K收盤突破兩底共同壓力區。"""
    return no_new_low and is_red_k and close > resistance and volume >= volume_multiple * range_avg_volume


@implements_rule("R-SCREEN-16")
def is_daily_mover(daily_return_pct: float, threshold: float = 0.035) -> bool:
    """每日選股掃描起手篩選：當日漲跌幅絕對值達3.5%以上(書中明確數字)才納入掃描池。"""
    return abs(daily_return_pct) >= threshold


@implements_rule("R-SCREEN-16")
def passes_initial_prefilter(is_consolidation: bool, volume: float, close_price: float, min_volume: float = 1000, min_price: float = 5.0) -> bool:
    """每日選股掃描初步排除：線形不明確(盤整)、成交量太小、股價太低者不列入候選。"""
    if is_consolidation:
        return False
    return volume >= min_volume and close_price >= min_price


@implements_rule("R-SCREEN-19")
def classify_limit_up_strength(limit_up_time: str | None, is_limit_down_at_open: bool = False) -> str | None:
    """技術線圖看圖重點第2項：漲停時間分級，開盤/09:10前最強次強、09:30前強、13:20後待次日驗證；開盤跌停為最弱。"""
    if is_limit_down_at_open:
        return "最弱"
    if limit_up_time is None:
        return None
    if limit_up_time <= "09:10":
        return "最強/次強"
    if limit_up_time <= "09:30":
        return "強"
    if limit_up_time > "13:20":
        return "待次日驗證"
    return None


@implements_rule("R-SCREEN-19")
def shortlist_top_n(ranked_candidates: list, n: int = 5) -> list:
    """技術線圖看圖重點第12項：收尾作業將候選標的收斂至3~5檔(書中明確區間，預設取上限5)。"""
    return ranked_candidates[:n]
