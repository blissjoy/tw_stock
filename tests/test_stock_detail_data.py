import math
from datetime import date, timedelta

from src.data.storage import (
    init_db,
    upsert_institutional_investors,
    upsert_margin_trading,
    upsert_stock_prices,
    upsert_stocks,
)
from src.presentation import stock_detail_data


def _main_conn():
    return init_db(":memory:")


def _seed_stock(conn, stock_id: str, name: str, prices: list[dict]) -> None:
    upsert_stocks(conn, [{"stock_id": stock_id, "name": name, "market": "TWSE", "industry": "測試業", "updated_at": "2026-08-02T00:00:00"}])
    upsert_stock_prices(conn, [
        {
            "stock_id": stock_id, "date": p["date"], "open": p.get("open", p["close"]),
            "high": p.get("high", p["close"]), "low": p.get("low", p["close"]), "close": p["close"],
            "volume": p.get("volume", 1000), "trading_money": p.get("trading_money"),
            "trading_turnover": None, "spread": None,
        }
        for p in prices
    ])


def test_load_quote_summary_matches_manual_calculation():
    """用鴻海21元手續費那個驗證情境同一組真實數字反算：均價=成交金額/成交量、
    振幅/漲跌幅都是用前一天收盤價當分母，不能只是「差不多」。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-30", "close": 2205.0, "open": 2205.0, "high": 2205.0, "low": 2205.0, "volume": 44_328_000, "trading_money": 98_479_995_000},
        {"date": "2026-07-31", "close": 2425.0, "open": 2350.0, "high": 2425.0, "low": 2345.0, "volume": 56_896_000, "trading_money": 136_371_955_000},
    ])

    result = stock_detail_data.load_quote_summary(conn, "2330")

    assert result is not None
    assert result["date"] == "2026-07-31"
    assert result["close"] == 2425.0
    assert result["open"] == 2350.0
    assert result["high"] == 2425.0
    assert result["low"] == 2345.0
    assert math.isclose(result["avg_price"], 136_371_955_000 / 56_896_000)
    assert math.isclose(result["trading_money_billion"], 1363.71955)
    assert result["prev_close"] == 2205.0
    assert result["change"] == 220.0
    assert math.isclose(result["change_pct"], 220.0 / 2205.0 * 100)
    assert result["volume_lots"] == 56_896
    assert result["prev_volume_lots"] == 44_328
    assert math.isclose(result["amplitude_pct"], (2425.0 - 2345.0) / 2205.0 * 100)


def test_load_quote_summary_estimates_avg_price_when_trading_money_missing():
    """trading_money缺值(盤中即時價備援沒有這個欄位，或yfinance回補的舊資料沒有)時，
    2026-08-03改版：改用典型價格(最高+最低+收盤)/3估算均價，不再直接回傳None——
    (2500+2400+2420)/3=2440.0，再乘以成交量2000股回推估算成交金額4,880,000元
    (=0.0488億)，並標記avg_price_is_estimated=True供UI顯示「(估)」。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-31", "open": 2410.0, "high": 2500.0, "low": 2400.0, "close": 2420.0,
         "volume": 2000, "trading_money": None},
    ])

    result = stock_detail_data.load_quote_summary(conn, "2330")

    assert result["avg_price"] == 2440.0
    assert result["avg_price_is_estimated"] is True
    assert math.isclose(result["trading_money_billion"], 4_880_000 / 1e8)


def test_load_quote_summary_avg_price_not_estimated_when_trading_money_present():
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-31", "close": 2425.0, "volume": 56_896_000, "trading_money": 136_371_955_000},
    ])

    result = stock_detail_data.load_quote_summary(conn, "2330")

    assert result["avg_price_is_estimated"] is False


def test_load_quote_summary_avg_price_none_when_volume_also_missing():
    """volume是0(理論上不該發生在真實資料，但防呆用)時估算公式沒有意義，avg_price
    仍然回傳None，不強行除以0或用0成交量估算出一個假的均價。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-31", "close": 2425.0, "volume": 0, "trading_money": None},
    ])

    result = stock_detail_data.load_quote_summary(conn, "2330")

    assert result["avg_price"] is None
    assert result["avg_price_is_estimated"] is False


def test_load_quote_summary_first_day_has_no_prev_close():
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])

    result = stock_detail_data.load_quote_summary(conn, "2330")

    assert result["prev_close"] is None
    assert result["change"] is None
    assert result["change_pct"] is None
    assert result["amplitude_pct"] is None


def test_load_quote_summary_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_quote_summary(conn, "9999") is None


def _seed_institutional(conn, stock_id: str, by_date: dict[str, dict[str, tuple[int, int]]]) -> None:
    rows = []
    for date, by_type in by_date.items():
        for investor_type, (buy, sell) in by_type.items():
            rows.append({"stock_id": stock_id, "date": date, "investor_type": investor_type, "buy": buy, "sell": sell})
    upsert_institutional_investors(conn, rows)


def test_load_institutional_daily_groups_foreign_dealer_self_into_foreign():
    """外資自營商(Foreign_Dealer_Self)併入外資、自營商自行/避險併入自營商，三大
    法人是全部加總——這是多數看盤網站的標準分法，不是這裡另外發明的。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_institutional(conn, "2330", {
        "2026-07-31": {
            "Foreign_Investor": (1000, 200),
            "Foreign_Dealer_Self": (50, 10),
            "Investment_Trust": (300, 100),
            "Dealer_self": (80, 20),
            "Dealer_Hedging": (40, 15),
        },
    })

    result = stock_detail_data.load_institutional_daily(conn, "2330")

    assert result["外資"] == {"buy": 1050, "sell": 210, "net": 840}
    assert result["投信"] == {"buy": 300, "sell": 100, "net": 200}
    assert result["自營商"] == {"buy": 120, "sell": 35, "net": 85}
    total_buy = 1050 + 300 + 120
    total_sell = 210 + 100 + 35
    assert result["三大法人"] == {"buy": total_buy, "sell": total_sell, "net": total_buy - total_sell}


def test_load_institutional_daily_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_institutional_daily(conn, "9999") is None


def test_load_institutional_cumulative_sums_over_period_window():
    """5個交易日，只有外資有紀錄：1日/2日/3日/5日欄位應該分別是最近1/2/3/5天的
    買賣超加總，不是全部5天都加進每一欄。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_institutional(conn, "2330", {
        "2026-07-25": {"Foreign_Investor": (100, 0)},
        "2026-07-28": {"Foreign_Investor": (200, 0)},
        "2026-07-29": {"Foreign_Investor": (300, 0)},
        "2026-07-30": {"Foreign_Investor": (400, 0)},
        "2026-07-31": {"Foreign_Investor": (500, 0)},
    })

    result = stock_detail_data.load_institutional_cumulative(conn, "2330")

    assert result["外資"]["1日"] == 500
    assert result["外資"]["2日"] == 500 + 400
    assert result["外資"]["3日"] == 500 + 400 + 300
    assert result["外資"]["5日"] == 500 + 400 + 300 + 200 + 100
    # 資料只有5天，天數不足的天期(10日/30日/40日...)就用「目前實際有的天數」加總，
    # 結果應該跟5日欄位一樣，不是None或0。
    assert result["外資"]["10日"] == result["外資"]["5日"]
    assert result["外資"]["1年"] == result["外資"]["5日"]


def test_load_institutional_cumulative_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_institutional_cumulative(conn, "9999") is None


def test_load_institutional_estimated_cost_computes_weighted_average_by_net_and_avg_price():
    """2天都是外資淨買超：day1均價100(net 200)、day2均價110(net 300)，加權平均成本
    =(200*100+300*110)/(200+300)=106.0。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-30", "close": 100.0, "volume": 1000, "trading_money": 100_000},
        {"date": "2026-07-31", "close": 110.0, "volume": 1000, "trading_money": 110_000},
    ])
    _seed_institutional(conn, "2330", {
        "2026-07-30": {"Foreign_Investor": (200, 0)},
        "2026-07-31": {"Foreign_Investor": (300, 0)},
    })

    result = stock_detail_data.load_institutional_estimated_cost(conn, "2330")

    assert result["外資"]["2日"] == 106.0


def test_load_institutional_estimated_cost_marks_unavailable_when_net_sell():
    """該天期外資合計是淨賣出(買100賣500，net=-400)，沒有累積部位可言，應該回傳None
    (不適用)，不是硬算出一個負分母的數字。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-31", "close": 100.0, "volume": 1000, "trading_money": 100_000},
    ])
    _seed_institutional(conn, "2330", {
        "2026-07-31": {"Foreign_Investor": (100, 500)},
    })

    result = stock_detail_data.load_institutional_estimated_cost(conn, "2330")

    assert result["外資"]["1日"] is None


def test_load_institutional_estimated_cost_falls_back_to_close_when_no_trading_money():
    """yfinance回補的舊資料trading_money可能是None，均價無從算起時退回用收盤價加權。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-31", "close": 50.0, "trading_money": None},
    ])
    _seed_institutional(conn, "2330", {
        "2026-07-31": {"Foreign_Investor": (100, 0)},
    })

    result = stock_detail_data.load_institutional_estimated_cost(conn, "2330")

    assert result["外資"]["1日"] == 50.0


def test_load_institutional_estimated_cost_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_institutional_estimated_cost(conn, "9999") is None


def test_pick_longest_available_cost_falls_back_to_shorter_period():
    """1年/6個月/3個月都不適用(None)，40日開始才有值，應該回傳40日的數字，不是
    直接回傳None或誤用更短的天期。"""
    cost_by_period = {label: None for label in stock_detail_data.INSTITUTIONAL_PERIODS}
    cost_by_period["40日"] = 123.45
    cost_by_period["20日"] = 111.11

    assert stock_detail_data.pick_longest_available_cost(cost_by_period) == 123.45


def test_pick_longest_available_cost_returns_none_when_all_unavailable():
    cost_by_period = {label: None for label in stock_detail_data.INSTITUTIONAL_PERIODS}
    assert stock_detail_data.pick_longest_available_cost(cost_by_period) is None


def test_load_latest_institutional_cost_summary_picks_longest_available_per_group():
    """40個連續交易日、股價全程持平100元(方便驗證加權平均一定是100.0)：外資最近
    10天淨賣出(net=-100/天)、更早30天淨買超(net=+100/天)；投信全程40天都淨買超。
    外資最近10天(甚至最近20天，剛好正負抵銷成0)應該不適用，退到40日(=1年，兩者
    因為資料只有40天剛好算出同一個窗口)才有值；投信全期間都適用，直接用最長的
    「1年」那格——驗證兩個分類各自獨立套用pick_longest_available_cost()的退回
    邏輯，不會互相干擾。"""
    conn = _main_conn()
    base = date(2026, 1, 1)
    all_dates = [(base + timedelta(days=i)).isoformat() for i in range(40)]
    _seed_stock(conn, "2330", "台積電", [
        {"date": d, "close": 100.0, "volume": 1000, "trading_money": 100_000} for d in all_dates
    ])
    by_date = {}
    for d in all_dates[:30]:  # 較早30天：外資／投信都淨買超
        by_date[d] = {"Foreign_Investor": (100, 0), "Investment_Trust": (100, 0)}
    for d in all_dates[30:]:  # 最近10天：外資轉為淨賣出，投信持續淨買超
        by_date[d] = {"Foreign_Investor": (0, 100), "Investment_Trust": (100, 0)}
    _seed_institutional(conn, "2330", by_date)

    result = stock_detail_data.load_latest_institutional_cost_summary(conn, "2330")

    cost = stock_detail_data.load_institutional_estimated_cost(conn, "2330")
    assert cost["外資"]["10日"] is None  # 最近10天淨賣出
    assert cost["外資"]["20日"] is None  # 最近20天正負剛好抵銷成0，denominator不>0
    assert cost["外資"]["1年"] == 100.0  # 40天資料全部落在窗口內，淨買超2000股，均價100
    assert result["外資"] == 100.0
    assert result["投信"] == 100.0


def test_load_latest_institutional_cost_summary_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_latest_institutional_cost_summary(conn, "9999") is None


def _seed_margin(conn, stock_id: str, rows: list[dict]) -> None:
    upsert_margin_trading(conn, [
        {
            "stock_id": stock_id, "date": r["date"],
            "margin_purchase_buy": r.get("m_buy", 0), "margin_purchase_sell": r.get("m_sell", 0),
            "margin_purchase_cash_repayment": 0,
            "margin_purchase_yesterday_balance": r.get("m_yesterday"),
            "margin_purchase_today_balance": r.get("m_today"),
            "margin_purchase_limit": r.get("m_limit"),
            "short_sale_buy": r.get("s_buy", 0), "short_sale_sell": r.get("s_sell", 0),
            "short_sale_cash_repayment": 0,
            "short_sale_yesterday_balance": r.get("s_yesterday"),
            "short_sale_today_balance": r.get("s_today"),
            "short_sale_limit": r.get("s_limit"),
            "offset_loan_and_short": r.get("offset"),
        }
        for r in rows
    ])


def test_load_margin_daily_computes_change_usage_rate_and_streak():
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_margin(conn, "2330", [
        {"date": "2026-07-29", "m_today": 33_373, "m_yesterday": 32_548, "m_limit": 6_483_092, "s_today": 123, "s_yesterday": 81, "s_limit": 6_483_092, "offset": 6},
        {"date": "2026-07-30", "m_today": 32_548, "m_yesterday": 31_823, "m_limit": 6_483_092, "s_today": 98, "s_yesterday": 123, "s_limit": 6_483_092, "offset": 1},
        {"date": "2026-07-31", "m_buy": 855, "m_sell": 662, "m_today": 31_823, "m_yesterday": 32_548, "m_limit": 6_483_092, "s_buy": 4, "s_sell": 5, "s_today": 88, "s_yesterday": 98, "s_limit": 6_483_092, "offset": 3},
    ])

    result = stock_detail_data.load_margin_daily(conn, "2330")

    assert result["date"] == "2026-07-31"
    assert result["close"] == 2425.0
    assert result["margin"]["buy"] == 855
    assert result["margin"]["sell"] == 662
    assert result["margin"]["balance"] == 31_823
    assert result["margin"]["change"] == 31_823 - 32_548
    assert math.isclose(result["margin"]["usage_rate"], 31_823 / 6_483_092 * 100)
    # 3筆餘額(33,373 -> 32,548 -> 31,823)只有2組差值、都是下降：連2減
    assert result["margin"]["streak"] == "連2減"
    assert result["short"]["balance"] == 88
    # 3筆餘額(123 -> 98 -> 88)只有2組差值、都是下降：連2減
    assert result["short"]["streak"] == "連2減"
    assert result["offset_loan_and_short"] == 3
    assert math.isclose(result["short_to_margin_ratio_pct"], 88 / 31_823 * 100)


def test_load_margin_daily_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_margin_daily(conn, "9999") is None


def test_load_margin_cumulative_sums_buy_sell_over_window():
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_margin(conn, "2330", [
        {"date": "2026-07-29", "m_buy": 900, "m_sell": 1600, "m_today": 33_373},
        {"date": "2026-07-30", "m_buy": 600, "m_sell": 1200, "m_today": 32_548},
        {"date": "2026-07-31", "m_buy": 855, "m_sell": 662, "m_today": 31_823},
    ])

    result = stock_detail_data.load_margin_cumulative(conn, "2330", days=3)

    assert result["days"] == 3
    assert result["margin"]["buy"] == 900 + 600 + 855
    assert result["margin"]["sell"] == 1600 + 1200 + 662
    assert result["margin"]["change"] == 31_823 - 33_373


def test_load_margin_cumulative_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_margin_cumulative(conn, "9999") is None


def test_load_institutional_flow_analysis_detects_sell_streak_per_group():
    """外資連續3天賣超(達朱家泓淘汰法R-SCREEN-06門檻)，投信連續3天買超(達陳家豐
    投信連續加碼3~5天門檻下限)，同時驗證兩個分類各自獨立判讀，不會互相干擾。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_institutional(conn, "2330", {
        "2026-07-29": {"Foreign_Investor": (100, 500), "Investment_Trust": (300, 100)},
        "2026-07-30": {"Foreign_Investor": (100, 400), "Investment_Trust": (250, 50)},
        "2026-07-31": {"Foreign_Investor": (100, 300), "Investment_Trust": (200, 80)},
    })

    result = stock_detail_data.load_institutional_flow_analysis(conn, "2330")

    assert result["外資"]["direction"] == "sell"
    assert result["外資"]["streak_days"] == 3
    assert result["外資"]["is_sell_warning"] is True
    assert result["投信"]["direction"] == "buy"
    assert result["投信"]["streak_days"] == 3
    assert result["投信"]["is_buy_watch"] is True


def test_load_institutional_flow_analysis_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_institutional_flow_analysis(conn, "9999") is None


def test_institutional_periods_uses_20_day_not_30_day():
    """2026-08-03改版：法人買賣總覽表格的天期欄位從30日改成20日。"""
    assert stock_detail_data.INSTITUTIONAL_PERIODS["20日"] == 20
    assert "30日" not in stock_detail_data.INSTITUTIONAL_PERIODS


def test_load_institutional_momentum_analysis_splits_foreign_trust_and_combined_excludes_dealer():
    """10個交易日，外資／投信／自營商都有紀錄：外資前5天(day1~5)每天買超100、
    近5天(day6~10)每天買超200(力道增強)；投信前5天每天買超50、近5天每天買超30
    (力道減弱)；自營商每天買超1000(刻意設一個很大的數字)。2026-08-03第二次
    改版：使用者要求外資/投信要分開看、自營商不計入這裡的比較——驗證①外資／
    投信的5日近期/前期合計各自正確、②"外資+投信"合計等於兩者相加(1150/750，
    不含自營商的每天1000)、③結果裡沒有"自營商"這個分類。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_institutional(conn, "2330", {
        "2026-07-01": {"Foreign_Investor": (150, 50), "Investment_Trust": (100, 50), "Dealer_self": (2000, 1000)},
        "2026-07-02": {"Foreign_Investor": (150, 50), "Investment_Trust": (100, 50), "Dealer_self": (2000, 1000)},
        "2026-07-03": {"Foreign_Investor": (150, 50), "Investment_Trust": (100, 50), "Dealer_self": (2000, 1000)},
        "2026-07-04": {"Foreign_Investor": (150, 50), "Investment_Trust": (100, 50), "Dealer_self": (2000, 1000)},
        "2026-07-05": {"Foreign_Investor": (150, 50), "Investment_Trust": (100, 50), "Dealer_self": (2000, 1000)},
        "2026-07-06": {"Foreign_Investor": (250, 50), "Investment_Trust": (80, 50), "Dealer_self": (2000, 1000)},
        "2026-07-07": {"Foreign_Investor": (250, 50), "Investment_Trust": (80, 50), "Dealer_self": (2000, 1000)},
        "2026-07-08": {"Foreign_Investor": (250, 50), "Investment_Trust": (80, 50), "Dealer_self": (2000, 1000)},
        "2026-07-09": {"Foreign_Investor": (250, 50), "Investment_Trust": (80, 50), "Dealer_self": (2000, 1000)},
        "2026-07-10": {"Foreign_Investor": (250, 50), "Investment_Trust": (80, 50), "Dealer_self": (2000, 1000)},
    })

    result = stock_detail_data.load_institutional_momentum_analysis(conn, "2330")

    assert result["外資"]["5日"]["current"] == 1000
    assert result["外資"]["5日"]["prior"] == 500
    assert result["外資"]["5日"]["trend"] == "買超力道增強"
    assert result["投信"]["5日"]["current"] == 150
    assert result["投信"]["5日"]["prior"] == 250
    assert result["投信"]["5日"]["trend"] == "買超力道減弱"
    assert result["外資+投信"]["5日"]["current"] == 1150
    assert result["外資+投信"]["5日"]["prior"] == 750
    assert result["外資+投信"]["5日"]["trend"] == "買超力道增強"
    assert "自營商" not in result
    assert "20日" not in result["外資"]
    assert "40日" not in result["外資"]


def test_load_institutional_momentum_analysis_returns_none_when_insufficient_data():
    """只有3天資料，湊不滿5日天期要求的2*5=10天完整比較區間，整個結果應該是
    None，不是回傳空dict或用不足的天數硬湊比較。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_institutional(conn, "2330", {
        "2026-07-29": {"Foreign_Investor": (100, 0)},
        "2026-07-30": {"Foreign_Investor": (100, 0)},
        "2026-07-31": {"Foreign_Investor": (100, 0)},
    })

    assert stock_detail_data.load_institutional_momentum_analysis(conn, "2330") is None


def test_load_institutional_momentum_analysis_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_institutional_momentum_analysis(conn, "9999") is None


def test_classify_institutional_momentum_directions():
    classify = stock_detail_data._classify_institutional_momentum
    assert classify(1000, 500) == "買超力道增強"
    assert classify(500, 1000) == "買超力道減弱"
    assert classify(-1000, -500) == "賣壓加重"
    assert classify(-500, -1000) == "賣壓減緩"
    assert classify(300, -200) == "由賣轉買"
    assert classify(-300, 200) == "由買轉賣"
    assert classify(0, 0) == "買賣力道持平"


def test_load_margin_maintenance_analysis_matches_book_liquidation_example():
    """陳家豐書中範例：100元買進、6成融資，股價跌到72元時維持率剛好是斷頭線120%
    (classify_margin_maintenance_state()的既有測試已經確認120%本身歸類在「警戒區」，
    要嚴格「小於」120%才算「已跌破斷頭線」，這裡沿用同一個邊界定義，不重新定義)。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-29", "close": 100.0},
        {"date": "2026-07-30", "close": 81.0},
        {"date": "2026-07-31", "close": 72.0},
    ])
    _seed_margin(conn, "2330", [
        {"date": "2026-07-29", "m_buy": 1000, "m_today": 1000},
        {"date": "2026-07-30", "m_buy": 0, "m_today": 1000},
        {"date": "2026-07-31", "m_buy": 0, "m_today": 1000},
    ])

    result = stock_detail_data.load_margin_maintenance_analysis(conn, "2330")

    assert round(result["ratio"], 2) == round(72 / 60, 2)
    assert result["state"] == "警戒區(爹不疼娘不愛)"


def test_load_margin_maintenance_analysis_returns_none_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.load_margin_maintenance_analysis(conn, "9999") is None


def test_scan_chip_tier_detects_institutional_sell_and_trust_buy_streaks():
    """三大法人(外資賣超蓋過投信買超，合計仍是淨賣超)連續3天觸發R-SCREEN-06；
    投信自己連續3天淨買超同時觸發R-CHIP-01——兩條規則各自獨立判讀，互不干擾。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [{"date": "2026-07-31", "close": 2425.0}])
    _seed_institutional(conn, "2330", {
        "2026-07-29": {"Foreign_Investor": (100, 500), "Investment_Trust": (300, 100)},
        "2026-07-30": {"Foreign_Investor": (100, 400), "Investment_Trust": (250, 50)},
        "2026-07-31": {"Foreign_Investor": (100, 300), "Investment_Trust": (200, 80)},
    })

    results = stock_detail_data.scan_chip_tier(conn, "2330")

    rule_ids = {r["rule_id"] for r in results}
    assert "R-SCREEN-06" in rule_ids
    assert "R-CHIP-01" in rule_ids


def test_scan_chip_tier_detects_margin_liquidation_and_oversold_rebound_together():
    """融資餘額第1天用收盤價100建立加權平均成本(6成融資，loan_per_share=60)，
    之後3天收盤價都跌到60元以下維持率<120%(斷頭線)：最新一天同時符合「已跌破
    斷頭線」跟「連續3天低於120%的超跌反彈」兩個R-CHIP-02子條件。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-28", "close": 100.0},
        {"date": "2026-07-29", "close": 70.0},
        {"date": "2026-07-30", "close": 65.0},
        {"date": "2026-07-31", "close": 60.0},
    ])
    _seed_margin(conn, "2330", [
        {"date": "2026-07-28", "m_buy": 1000, "m_today": 1000},
        {"date": "2026-07-29", "m_buy": 0, "m_today": 1000},
        {"date": "2026-07-30", "m_buy": 0, "m_today": 1000},
        {"date": "2026-07-31", "m_buy": 0, "m_today": 1000},
    ])

    results = stock_detail_data.scan_chip_tier(conn, "2330")

    chip_02 = [r for r in results if r["rule_id"] == "R-CHIP-02"]
    assert len(chip_02) == 2
    notes = "\n".join(r["note"] for r in chip_02)
    assert "斷頭" in notes
    assert "超跌" in notes


def test_scan_chip_tier_returns_empty_list_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.scan_chip_tier(conn, "9999") == []


def test_analyze_chip_signals_merges_duplicate_rule_id_and_sorts_by_confidence():
    """複用上面兩個情境的資料：R-CHIP-02同時觸發斷頭+超跌，應該合併成一筆(note用
    換行接起來)，且信心分數(85)比R-SCREEN-06(75)高，排序應該在前面。"""
    conn = _main_conn()
    _seed_stock(conn, "2330", "台積電", [
        {"date": "2026-07-28", "close": 100.0},
        {"date": "2026-07-29", "close": 70.0},
        {"date": "2026-07-30", "close": 65.0},
        {"date": "2026-07-31", "close": 60.0},
    ])
    _seed_margin(conn, "2330", [
        {"date": "2026-07-28", "m_buy": 1000, "m_today": 1000},
        {"date": "2026-07-29", "m_buy": 0, "m_today": 1000},
        {"date": "2026-07-30", "m_buy": 0, "m_today": 1000},
        {"date": "2026-07-31", "m_buy": 0, "m_today": 1000},
    ])
    _seed_institutional(conn, "2330", {
        "2026-07-29": {"Foreign_Investor": (100, 500), "Investment_Trust": (300, 100)},
        "2026-07-30": {"Foreign_Investor": (100, 400), "Investment_Trust": (250, 50)},
        "2026-07-31": {"Foreign_Investor": (100, 300), "Investment_Trust": (200, 80)},
    })

    result = stock_detail_data.analyze_chip_signals(conn, "2330")

    by_id = {m["rule_id"]: m for m in result}
    assert set(by_id) == {"R-SCREEN-06", "R-CHIP-01", "R-CHIP-02"}
    assert by_id["R-CHIP-02"]["confidence"] == 85
    assert by_id["R-CHIP-02"]["note"].count("\n") == 1  # 兩筆note合併成一筆、用換行接起來
    assert result[0]["confidence"] >= result[-1]["confidence"]  # 依信心分數由高到低排序


def test_analyze_chip_signals_returns_empty_list_when_no_data():
    conn = _main_conn()
    assert stock_detail_data.analyze_chip_signals(conn, "9999") == []
