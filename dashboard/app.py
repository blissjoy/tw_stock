"""Streamlit 儀表板：顯示每日選股結果（daily_candidates 表最新一天），以及查詢個股近期
價格走勢。純讀取，不做任何指標運算（那些已經由 scripts/daily_pipeline.py 算完寫進
daily_candidates 表），儀表板端保持輕量。

部署：Streamlit Community Cloud，在其後台 Secrets 設定與 GitHub Actions 同一組
TURSO_DATABASE_URL / TURSO_AUTH_TOKEN（st.secrets 在這裡先搬進 os.environ，讓
src/data/config.py 既有的讀取邏輯不必為了 Streamlit 另外寫一套）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_latest_candidates(conn) -> tuple[pd.DataFrame, str | None]:
    """回傳 (最新一天的候選清單DataFrame, 該日期字串)；尚無任何紀錄時回傳(空DataFrame, None)。"""
    latest_date = conn.execute("SELECT MAX(date) FROM daily_candidates").fetchone()[0]
    if latest_date is None:
        return pd.DataFrame(), None
    cur = conn.execute(
        """
        SELECT dc.stock_id, s.name, dc.signal_name, dc.entry_price, dc.stop_loss, dc.note
        FROM daily_candidates dc LEFT JOIN stocks s ON dc.stock_id = s.stock_id
        WHERE dc.date = ? ORDER BY dc.stock_id
        """,
        (latest_date,),
    )
    columns = [d[0] for d in cur.description]
    return pd.DataFrame(cur.fetchall(), columns=columns), latest_date


def load_price_history(conn, stock_id: str, days: int = 120) -> pd.DataFrame:
    """回傳指定股票最近days天的OHLCV，依date遞增排序、index為date；查無資料回傳空DataFrame。"""
    cur = conn.execute(
        "SELECT date, open, high, low, close, volume FROM stock_prices WHERE stock_id = ? ORDER BY date DESC LIMIT ?",
        (stock_id, days),
    )
    columns = [d[0] for d in cur.description]
    df = pd.DataFrame(cur.fetchall(), columns=columns).iloc[::-1].reset_index(drop=True)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    return df


def main() -> None:
    import streamlit as st

    from src.data import storage, turso_client

    for key, value in st.secrets.items():
        os.environ.setdefault(key, str(value))

    st.set_page_config(page_title="台股每日選股", page_icon="📈", layout="wide")

    @st.cache_resource
    def get_conn():
        # Turso資料庫可能是全新、還沒被seed_turso_from_local.py或daily_pipeline.py建過表的狀態
        # （例如儀表板比每日pipeline更早部署），這裡確保schema一定存在，query才不會噴
        # "no such table" 的錯誤。
        conn = turso_client.get_connection()
        storage.ensure_schema(conn)
        return conn

    st.title("📈 台股每日選股")
    st.caption("資料來源：TWSE / TPEx(透過FinMind) — 每日收盤後自動更新")

    conn = get_conn()
    candidates_df, latest_date = load_latest_candidates(conn)

    if latest_date is None:
        st.info("目前 Turso 資料庫裡還沒有任何每日選股紀錄，等 GitHub Actions 第一次跑完後就會顯示。")
    else:
        st.subheader(f"最新候選清單（{latest_date}，共 {len(candidates_df)} 檔）")
        if candidates_df.empty:
            st.write("這一天沒有符合條件的候選股。")
        else:
            st.dataframe(candidates_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("個股價格走勢查詢")
    stock_id = st.text_input("輸入股票代號（例如 2330）", value="")
    if stock_id:
        price_df = load_price_history(conn, stock_id.strip())
        if price_df.empty:
            st.warning(f"查無股票代號 {stock_id} 的價格資料。")
        else:
            st.line_chart(price_df[["close"]])
            st.dataframe(price_df.tail(20), use_container_width=True)


if __name__ == "__main__":
    main()
