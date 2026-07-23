"""yfinance批次下載股價資料：仿照`ref-project/tw_stock_analyzer/src/core/stock_scanner.py`的
做法(`yf.download`傳入ticker清單陣列一次批次下載，而非逐股個別呼叫)，用來取代原本
`src/data/finmind_client.py`逐股抓取TPEx股價的慢速路徑(實測約需1小時)。ref-project長期
實測：1000檔批次下載通常20秒內完成，對`.TW`(上市)/`.TWO`(上櫃)兩種市場代碼一視同仁，
沒有額外的可靠性落差或特殊處理。

⚠️ yfinance是靠爬Yahoo Finance內部API運作的非官方套件，Yahoo改版可能讓它默默失效或
改變回應格式，這點跟`src/data/twse_client.py`直接打TWSE官方API不同——但目前只用來抓
TPEx股價，TWSE股價仍然維持用官方API(本來就夠快，沒有理由換成可靠性較低的來源)；換來的
效率提升(~1小時 -> 數十秒)在ref-project長期使用下被驗證是值得的取捨，且原本的
`finmind_client.fetch_stock_prices()`逐股抓法仍保留，之後若yfinance失效可以退回使用。

`extract_ticker_frame()`的MultiIndex處理邏輯直接沿用ref-project的
`stock_scanner.extract_ticker_df()`寫法(已經過長期實測)，只是回傳格式改成符合本專案
`storage.upsert_stock_prices()`要求的dict清單，而不是DataFrame。
"""

from __future__ import annotations

import pandas as pd

# 比照ref-project的做法批次下載，避免單次yf.download請求過大(ref-project預設1000，
# 這裡略保守取500，兩者都遠低於yfinance/Yahoo Finance實際能負荷的量)
BATCH_SIZE = 500


def _extract_ticker_frame(df_batch: pd.DataFrame, ticker: str, num_tickers_requested: int) -> pd.DataFrame | None:
    """從yf.download()批次下載後的DataFrame裡取出單一ticker的資料，處理MultiIndex欄位
    (多檔ticker時yf.download回傳的欄位是(欄位名, ticker)的MultiIndex；只下載1檔時則是
    一般Index)。邏輯沿用ref-project的extract_ticker_df，已經過長期實測。"""
    if df_batch is None or df_batch.empty:
        return None
    try:
        if isinstance(df_batch.columns, pd.MultiIndex):
            ticker_level = 1
            if df_batch.columns.names and "Ticker" in df_batch.columns.names:
                ticker_level = df_batch.columns.names.index("Ticker")
            elif df_batch.columns.names and "ticker" in df_batch.columns.names:
                ticker_level = df_batch.columns.names.index("ticker")

            tickers_found = df_batch.columns.get_level_values(ticker_level).unique()
            if ticker not in tickers_found:
                return None
            t_df = df_batch.xs(ticker, level=ticker_level, axis=1)
            col_lower = {col.lower(): col for col in t_df.columns}
            close_col = col_lower.get("close", "Close")
            if close_col in t_df.columns:
                return t_df.dropna(subset=[close_col])
            return t_df.dropna(how="all")
        elif num_tickers_requested == 1:
            col_lower = {col.lower(): col for col in df_batch.columns}
            close_col = col_lower.get("close", "Close")
            if close_col in df_batch.columns:
                return df_batch.dropna(subset=[close_col])
            return df_batch.dropna(how="all")
    except Exception:  # noqa: BLE001 - 單一ticker解析失敗不應該讓整批下載中斷
        return None
    return None


def _frame_to_price_rows(stock_id: str, frame: pd.DataFrame) -> list[dict]:
    col_lower = {col.lower(): col for col in frame.columns}
    rows = []
    for date_idx, row in frame.iterrows():
        close = row.get(col_lower.get("close", "Close"))
        if pd.isna(close):
            continue
        rows.append({
            "stock_id": stock_id,
            "date": date_idx.strftime("%Y-%m-%d"),
            "open": float(row[col_lower.get("open", "Open")]),
            "high": float(row[col_lower.get("high", "High")]),
            "low": float(row[col_lower.get("low", "Low")]),
            "close": float(close),
            "volume": int(row[col_lower.get("volume", "Volume")]) if not pd.isna(row[col_lower.get("volume", "Volume")]) else 0,
            # yfinance不提供這三個FinMind才有的欄位，schema.sql裡本來就是nullable
            "trading_money": None, "trading_turnover": None, "spread": None,
        })
    return rows


def fetch_prices_batch(stock_ids: list[str], start_date: str, end_date: str, market_suffix: str) -> dict[str, list[dict]]:
    """批次下載股票的日K OHLCV資料，回傳{stock_id: [row, ...]}(查無資料的股票不會出現在
    回傳的dict裡)。

    start_date/end_date格式為'YYYY-MM-DD'；end_date是exclusive(yfinance/pandas慣例)，
    要抓「當天」時呼叫端要自己算好end_date=隔天，例如只抓2026-07-22這一天需傳入
    start_date="2026-07-22", end_date="2026-07-23"。
    market_suffix：Yahoo Finance的台股市場代碼後綴，上市是".TW"、上櫃(TPEx)是".TWO"。
    """
    import yfinance as yf

    tickers = [f"{sid}{market_suffix}" for sid in stock_ids]
    results: dict[str, list[dict]] = {}

    for start in range(0, len(tickers), BATCH_SIZE):
        batch_tickers = tickers[start:start + BATCH_SIZE]
        df_batch = yf.download(
            batch_tickers, start=start_date, end=end_date, interval="1d",
            progress=False, auto_adjust=False,
        )
        for ticker in batch_tickers:
            stock_id = ticker[: -len(market_suffix)]
            frame = _extract_ticker_frame(df_batch, ticker, len(batch_tickers))
            if frame is None or frame.empty:
                continue
            rows = _frame_to_price_rows(stock_id, frame)
            if rows:
                results[stock_id] = rows

    return results


def fetch_tpex_prices_batch(stock_ids: list[str], start_date: str, end_date: str) -> dict[str, list[dict]]:
    """TPEx(上櫃)股票專用的批次下載，market_suffix固定為Yahoo Finance的上櫃代碼".TWO"。"""
    return fetch_prices_batch(stock_ids, start_date, end_date, market_suffix=".TWO")
