"""Streamlit 儀表板：顯示每日選股結果（daily_candidates 表最新一天，可點選清單中任一列
直接看該檔股票的價格走勢），也可以手動查詢任意股票代號。

「🔄 立即重新篩選」按鈕呼叫 src/screener/daily_screener.run_screen_and_store()，只用
資料庫裡『目前已有』的資料重算訊號，不會對外重新抓取TWSE/TPEx資料（那個很慢，交給
scripts/daily_pipeline.py 的每日排程做），所以按下去通常幾秒內就有結果。

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
    from src.screener.daily_screener import run_screen_and_store

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

    conn = get_conn()

    st.title("📈 台股每日選股")
    st.caption("資料來源：TWSE / TPEx(透過FinMind) — 每日收盤後自動更新")

    if st.button("🔄 立即重新篩選"):
        # 只用資料庫裡目前已有的資料重算訊號，不重新對外抓取TWSE/TPEx資料(那個很慢，交給
        # 每日排程做)，所以這個按鈕通常幾秒內就能算完，可以隨時按而不用擔心額度或等待。
        with st.spinner("正在用目前資料庫裡的最新資料重新計算選股訊號..."):
            run_screen_and_store(conn)
        st.success("已重新計算完成，候選清單已更新。")

    candidates_df, latest_date = load_latest_candidates(conn)

    selected_stock_id = None
    if latest_date is None:
        st.info("目前 Turso 資料庫裡還沒有任何每日選股紀錄，點上方「立即重新篩選」或等 GitHub Actions 排程跑完後就會顯示。")
    else:
        st.subheader(f"最新候選清單（{latest_date}，共 {len(candidates_df)} 檔）")
        if candidates_df.empty:
            st.write("這一天沒有符合條件的候選股。")
        else:
            st.caption("點選任一列可在下方查看該檔股票的價格走勢")
            event = st.dataframe(
                candidates_df, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row", key="candidates_table",
            )
            if event.selection.rows:
                selected_stock_id = str(candidates_df.iloc[event.selection.rows[0]]["stock_id"])

    st.divider()

    if selected_stock_id:
        st.subheader(f"📊 {selected_stock_id} 價格走勢（點選自候選清單）")
        price_df = load_price_history(conn, selected_stock_id)
        if price_df.empty:
            st.warning(f"查無股票代號 {selected_stock_id} 的價格資料。")
        else:
            st.line_chart(price_df[["close"]])
            st.dataframe(price_df.tail(20), use_container_width=True)
        st.divider()

    st.subheader("個股價格走勢查詢（手動輸入任意股票代號）")
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
