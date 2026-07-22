import sqlite3

from scripts.seed_turso_from_local import seed
from src.data.storage import init_db


def _build_local_db_with_days(dates: list[str]) -> sqlite3.Connection:
    conn = init_db(":memory:")
    conn.execute(
        "INSERT INTO stocks (stock_id, name, market, industry, updated_at) VALUES ('2330', '台積電', 'TWSE', NULL, ?)",
        (dates[-1],),
    )
    for d in dates:
        conn.execute(
            "INSERT INTO stock_prices (stock_id, date, open, high, low, close, volume, trading_money, trading_turnover, spread) "
            "VALUES ('2330', ?, 100, 101, 99, 100.5, 1000, NULL, NULL, NULL)",
            (d,),
        )
        conn.execute(
            "INSERT INTO institutional_investors (stock_id, date, investor_type, buy, sell) "
            "VALUES ('2330', ?, 'Foreign_Investor', 100, 50)",
            (d,),
        )
    conn.commit()
    return conn


def test_seed_copies_only_recent_window_into_target():
    all_dates = [f"2026-01-{i:02d}" for i in range(1, 11)]  # 10天
    local_conn = _build_local_db_with_days(all_dates)
    target_conn = init_db(":memory:")

    stats = seed(local_conn, target_conn, days=3)

    assert stats["dates"] == 3
    assert stats["prices"] == 3
    assert stats["institutional"] == 3

    copied_dates = {r[0] for r in target_conn.execute("SELECT date FROM stock_prices").fetchall()}
    assert copied_dates == {"2026-01-08", "2026-01-09", "2026-01-10"}  # 只灌最近3天


def test_seed_handles_empty_source_gracefully():
    local_conn = init_db(":memory:")
    target_conn = init_db(":memory:")

    stats = seed(local_conn, target_conn, days=400)

    assert stats == {"dates": 0, "stocks": 0, "prices": 0, "institutional": 0, "margin": 0}
